"""Connect: in-memory two-person room relay (WebSocket transport agnostic).

Rooms are process-local and ephemeral:
* A room code identifies exactly one room; a room holds at most two
  participants (creator + joiner).
* Only recognized gesture events and text messages are relayed — never camera
  frames, never landmarks, never raw predictions.
* Conversation history is kept in memory only (bounded deque) for catch-up when
  a participant joins/resumes; nothing is written to disk or MongoDB.

The relay consumes events produced by :class:`ConnectDetector` (which runs
inside the existing GestureEngine loop) and pushes them to the WebSocket layer
through a tiny sink callback registered by the Flask-Sock route module.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
import uuid
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

from config import Config
from core.connect.detector import connect_detector
from services.logging_service import logger

_ROLE_CREATOR = "creator"
_ROLE_JOINER = "joiner"
_ROLES = {_ROLE_CREATOR, _ROLE_JOINER}

_MAX_TEXT_LENGTH = 600


class Participant:
    """One connected seat in a room (identified by a stable client id)."""

    def __init__(self, client_id: str, role: str, display_name: str) -> None:
        self.client_id = client_id
        self.role = role
        self.display_name = display_name or ("User A" if role == _ROLE_CREATOR else "User B")
        self.ws: Any = None
        self.connected = False
        self.want_seat = False
        self.send_lock = threading.Lock()

    def attach(self, ws: Any) -> None:
        self.ws = ws
        self.connected = True

    def detach(self) -> None:
        self.ws = None
        self.connected = False

    def send_json(self, payload: Dict[str, Any]) -> bool:
        if not self.connected or self.ws is None:
            return False
        try:
            with self.send_lock:
                self.ws.send(json.dumps(payload, separators=(",", ":")))
            return True
        except Exception:
            self.connected = False
            return False


class Room:
    """A private two-person communication room."""

    def __init__(self, code: str) -> None:
        self.code = code
        self.created_at = time.time()
        self.last_activity = time.time()
        self.participants: Dict[str, Participant] = {}
        self.history: Deque[Dict[str, Any]] = deque(maxlen=int(Config.CONNECT_ROOM_HISTORY_LIMIT))

    @property
    def connected_count(self) -> int:
        return sum(1 for participant in self.participants.values() if participant.connected)

    @property
    def peer_of(self, client_id: str) -> Optional[Participant]:
        return next(
            (participant for cid, participant in self.participants.items() if cid != client_id),
            None,
        )

    def snapshot_for(self, client_id: str, role: str, seat_owner: Optional[Tuple[str, str]] = None) -> Dict[str, Any]:
        return {
            "type": "room_snapshot",
            "code": self.code,
            "client_id": client_id,
            "role": role,
            "participants": self._participant_summary(seat_owner),
            "history": list(self.history),
            "ts": time.time(),
        }

    def _participant_summary(self, seat_owner: Optional[Tuple[str, str]] = None) -> List[Dict[str, Any]]:
        return [
            {
                "client_id": participant.client_id,
                "role": participant.role,
                "display_name": participant.display_name,
                "connected": participant.connected,
                "seat": seat_owner == (self.code, participant.client_id),
            }
            for participant in self.participants.values()
        ]


class ConnectRoomService:
    """Owns rooms, seating (camera feed) and message relay for /connect."""

    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialize()
            return cls._instance

    def _initialize(self) -> None:
        self._lock = threading.RLock()
        self._rooms: Dict[str, Room] = {}
        # ws identity -> (room_code, client_id); used on disconnect.
        self._ws_index: Dict[int, Tuple[str, str]] = {}
        # Global exclusive camera-feed seat (one physical camera per process).
        self._seat_owner: Optional[Tuple[str, str]] = None
        self._camera_was_off = False
        self._purged_rooms = 0

        self.max_participants = int(Config.CONNECT_ROOM_MAX_PARTICIPANTS)
        self.idle_ttl = float(Config.CONNECT_ROOM_IDLE_TTL_SECONDS)
        self.max_age = float(Config.CONNECT_ROOM_MAX_AGE_SECONDS)

        connect_detector.set_sink(self._detector_sink)

        self._sweep_thread = threading.Thread(
            target=self._sweep_loop, name="connect-room-sweep", daemon=True
        )
        self._sweep_thread.start()
        logger.info("Connect room relay initialized (ephemeral rooms, no persistence).")

    # ------------------------------------------------------------------
    # Room code generation
    # ------------------------------------------------------------------
    def _generate_code(self) -> str:
        prefix = str(Config.CONNECT_ROOM_CODE_PREFIX)
        alphabet = str(Config.CONNECT_ROOM_CODE_ALPHABET)
        length = int(Config.CONNECT_ROOM_CODE_LENGTH)
        for _ in range(200):
            code = prefix + "-" + "".join(secrets.choice(alphabet) for _ in range(length))
            if code not in self._rooms:
                return code
        # Extremely unlikely; widen with a random tail if ever reached.
        return prefix + "-" + secrets.token_hex(3).upper()

    @staticmethod
    def _normalize_code(code: Any) -> str:
        return str(code or "").strip().upper()

    @staticmethod
    def _valid_client_id(client_id: Any) -> bool:
        value = str(client_id or "")
        if not (8 <= len(value) <= 64):
            return False
        return all(character.isalnum() or character in "-_" for character in value)

    def _new_client_id(self) -> str:
        return "gf-" + uuid.uuid4().hex[:20]

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------
    def _room_by_ws(self, ws: Any) -> Optional[Tuple[Room, str]]:
        entry = self._ws_index.get(id(ws))
        if not entry:
            return None
        code, client_id = entry
        room = self._rooms.get(code)
        if room is None:
            return None
        return room, client_id

    def _record_activity(self, room: Room) -> None:
        room.last_activity = time.time()

    def _send_peers(self, room: Room) -> None:
        payload = {
            "type": "peers",
            "code": room.code,
            "participants": room._participant_summary(self._seat_owner),
            "ts": time.time(),
        }
        for participant in list(room.participants.values()):
            participant.send_json(payload)

    def _relay(self, room: Room, payload: Dict[str, Any]) -> None:
        """Append to room history and deliver to every connected participant."""
        room.history.append(payload)
        self._record_activity(room)
        for participant in list(room.participants.values()):
            participant.send_json(payload)

    # ------------------------------------------------------------------
    # Message handlers (called from the WebSocket route thread)
    # ------------------------------------------------------------------
    def handle_message(self, ws: Any, raw_payload: Any) -> None:
        try:
            payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
        except (TypeError, ValueError):
            self._send_error(ws, "bad_request", "Malformed message.")
            return
        if not isinstance(payload, dict):
            self._send_error(ws, "bad_request", "Malformed message.")
            return

        message_type = str(payload.get("type", "")).lower()
        if message_type == "create":
            self._handle_create(ws, payload)
        elif message_type == "join":
            self._handle_join(ws, payload)
        elif message_type == "resume":
            self._handle_resume(ws, payload)
        elif message_type == "leave":
            self._handle_leave(ws)
        elif message_type == "gesture_cam":
            self._handle_seat(ws, payload)
        elif message_type == "message":
            self._handle_text(ws, payload)
        elif message_type == "ping":
            self._safe_send(ws, {"type": "pong", "ts": time.time()})
        else:
            self._send_error(ws, "bad_request", f"Unknown message type '{message_type}'.")

    def _send_error(self, ws: Any, code: str, message: str) -> None:
        self._safe_send(ws, {"type": "error", "error": code, "message": message})

    @staticmethod
    def _safe_send(ws: Any, payload: Dict[str, Any]) -> None:
        try:
            ws.send(json.dumps(payload, separators=(",", ":")))
        except Exception:
            pass

    def _handle_create(self, ws: Any, payload: Dict[str, Any]) -> None:
        client_id = payload.get("client_id") or ""
        if not self._valid_client_id(client_id):
            client_id = self._new_client_id()
        display_name = str(payload.get("display_name") or "").strip()[:40]

        with self._lock:
            self._purge_rooms()
            room = Room(self._generate_code())
            participant = Participant(client_id, _ROLE_CREATOR, display_name)
            participant.attach(ws)
            room.participants[client_id] = participant
            self._rooms[room.code] = room
            self._ws_index[id(ws)] = (room.code, client_id)
            self._record_activity(room)
            connect_detector.reset()

            # The room creator starts as the gesture-sending seat holder so
            # they can greet the joiner immediately.
            participant.want_seat = True
            if self._seat_owner is None:
                self._seat_owner = (room.code, client_id)

            self._safe_send(ws, room.snapshot_for(client_id, _ROLE_CREATOR, self._seat_owner))
            self._send_peers(room)
            logger.info(f"Connect room created: {room.code} ({client_id})")

    def _handle_join(self, ws: Any, payload: Dict[str, Any]) -> None:
        code = self._normalize_code(payload.get("code"))
        client_id = payload.get("client_id") or ""
        if not self._valid_client_id(client_id):
            client_id = self._new_client_id()
        display_name = str(payload.get("display_name") or "").strip()[:40]

        with self._lock:
            self._purge_rooms()
            room = self._rooms.get(code)
            if room is None:
                self._send_error(ws, "invalid_room", f"Room '{code}' does not exist or has expired.")
                return
            if client_id in room.participants:
                # Reload/refresh of an existing participant — treat as resume.
                self._resume_participant(room, client_id, ws)
                return
            if len(room.participants) >= self.max_participants:
                self._send_error(
                    ws,
                    "room_full",
                    "Room is full. A Connect room supports exactly two participants.",
                )
                return
            role = _ROLE_JOINER if _ROLE_CREATOR in {
                participant.role for participant in room.participants.values()
            } else _ROLE_CREATOR
            participant = Participant(client_id, role, display_name)
            participant.attach(ws)
            room.participants[client_id] = participant
            self._ws_index[id(ws)] = (room.code, client_id)
            self._record_activity(room)
            self._safe_send(ws, room.snapshot_for(client_id, role, self._seat_owner))
            self._send_peers(room)
            logger.info(f"Participant joined Connect room {room.code}: {client_id} ({role})")

    def _handle_resume(self, ws: Any, payload: Dict[str, Any]) -> None:
        code = self._normalize_code(payload.get("code"))
        client_id = str(payload.get("client_id") or "")
        role = str(payload.get("role") or "").lower()
        with self._lock:
            self._purge_rooms()
            room = self._rooms.get(code)
            if room is None:
                self._send_error(ws, "invalid_room", "Room has expired. Create or join a new room.")
                return
            participant = room.participants.get(client_id)
            if participant is None or (role and participant.role != role):
                self._send_error(ws, "invalid_session", "This browser session no longer belongs to the room.")
                return
            self._resume_participant(room, client_id, ws)

    def _resume_participant(self, room: Room, client_id: str, ws: Any) -> None:
        participant = room.participants[client_id]
        was_connected = participant.connected
        participant.attach(ws)
        self._ws_index[id(ws)] = (room.code, client_id)
        self._record_activity(room)

        if self._seat_owner == (room.code, client_id) and participant.want_seat:
            pass  # Seat already owned by this participant.
        elif participant.want_seat and self._seat_owner is None:
            self._seat_owner = (room.code, client_id)
            connect_detector.reset()

        if not was_connected:
            connect_detector.reset()
        self._safe_send(ws, room.snapshot_for(client_id, participant.role, self._seat_owner))
        self._send_peers(room)
        logger.info(f"Participant resumed Connect room {room.code}: {client_id}")

    def _handle_leave(self, ws: Any) -> None:
        with self._lock:
            entry = self._ws_index.pop(id(ws), None)
            if not entry:
                return
            code, client_id = entry
            room = self._rooms.get(code)
            if room is None:
                return
            self._remove_participant(room, client_id)
            self._send_peers(room)

    def _remove_participant(self, room: Room, client_id: str) -> None:
        participant = room.participants.pop(client_id, None)
        if participant is not None:
            participant.detach()
        if self._seat_owner == (room.code, client_id):
            self._seat_owner = None
            connect_detector.reset()
            self._grant_next_waiter()
        self._record_activity(room)
        if not room.participants:
            self._rooms.pop(room.code, None)
            logger.info(f"Connect room {room.code} closed (empty).")

    def _handle_seat(self, ws: Any, payload: Dict[str, Any]) -> None:
        enabled = bool(payload.get("enabled"))
        with self._lock:
            entry = self._ws_index.get(id(ws))
            if not entry:
                self._send_error(ws, "not_in_room", "You are not in a room.")
                return
            code, client_id = entry
            room = self._rooms.get(code)
            if room is None:
                self._send_error(ws, "invalid_room", "Room no longer exists.")
                return
            participant = room.participants.get(client_id)
            if participant is None:
                return

            if not enabled:
                participant.want_seat = False
                if self._seat_owner == (code, client_id):
                    self._seat_owner = None
                    connect_detector.reset()
                    self._grant_next_waiter()
                self._send_peers(room)
                return

            participant.want_seat = True
            if self._seat_owner is None:
                self._seat_owner = (code, client_id)
                connect_detector.reset()
                self._send_peers(room)
                return
            if self._seat_owner == (code, client_id):
                self._send_peers(room)
                return

            # Seat is busy: queue this participant. The request is granted
            # automatically as soon as the current holder releases the feed.
            self._send_error(
                ws,
                "seat_pending",
                "The camera feed is with the other participant. Your request is "
                "queued and will switch over automatically when they stop sending.",
            )
            self._send_peers(room)

    def _grant_next_waiter(self) -> None:
        """Grant the camera feed to the first connected participant waiting."""
        for code, room in self._rooms.items():
            for client_id, participant in room.participants.items():
                if participant.connected and participant.want_seat and self._seat_owner is None:
                    self._seat_owner = (code, client_id)
                    connect_detector.reset()
                    self._send_peers(room)
                    return

    def _handle_text(self, ws: Any, payload: Dict[str, Any]) -> None:
        text = str(payload.get("text") or "").strip()
        if not text:
            return
        if len(text) > _MAX_TEXT_LENGTH:
            text = text[:_MAX_TEXT_LENGTH]
        with self._lock:
            entry = self._ws_index.get(id(ws))
            if not entry:
                self._send_error(ws, "not_in_room", "You are not in a room.")
                return
            code, client_id = entry
            room = self._rooms.get(code)
            if room is None:
                return
            participant = room.participants.get(client_id)
            if participant is None:
                return
            self._relay(room, {
                "type": "text",
                "id": uuid.uuid4().hex[:12],
                "from": client_id,
                "sender_role": participant.role,
                "sender_name": participant.display_name,
                "text": text,
                "ts": time.time(),
            })

    # ------------------------------------------------------------------
    # Disconnect (socket close / page navigation away)
    # ------------------------------------------------------------------
    def disconnect(self, ws: Any) -> None:
        with self._lock:
            entry = self._ws_index.pop(id(ws), None)
            if not entry:
                return
            code, client_id = entry
            room = self._rooms.get(code)
            if room is None:
                return
            participant = room.participants.get(client_id)
            if participant is None:
                return
            participant.detach()
            was_seat_owner = self._seat_owner == (code, client_id)
            if was_seat_owner:
                self._seat_owner = None
                connect_detector.reset()
                self._grant_next_waiter()
            self._record_activity(room)
            self._send_peers(room)
            logger.info(f"Connect participant disconnected: {client_id} in {room.code}")

    # ------------------------------------------------------------------
    # Gesture engine feed (called from the existing MediaPipe loop)
    # ------------------------------------------------------------------
    def notify_camera_off(self) -> None:
        with self._lock:
            if self._camera_was_off:
                return
            self._camera_was_off = True
            connect_detector.reset()
            if self._seat_owner is None:
                return
            code, client_id = self._seat_owner
            room = self._rooms.get(code)
            participant = room.participants.get(client_id) if room else None
            if participant is not None:
                participant.send_json({
                    "type": "local_status",
                    "state": "camera_off",
                    "ts": time.time(),
                })

    def on_engine_frame(self, left_hand: Any, right_hand: Any, connect_active: bool) -> None:
        """Feed the detector only while the Connect page module is active."""
        with self._lock:
            self._camera_was_off = False
            if not connect_active or self._seat_owner is None:
                connect_detector.reset()
                return
            code, client_id = self._seat_owner
            room = self._rooms.get(code)
            participant = room.participants.get(client_id) if room else None
            if participant is None or not participant.connected:
                self._seat_owner = None
                connect_detector.reset()
                return
        connect_detector.update(left_hand, right_hand)

    # ------------------------------------------------------------------
    # Detector sink (runs on the gesture engine thread)
    # ------------------------------------------------------------------
    def _detector_sink(self, event_type: str, payload: Dict[str, Any]) -> None:
        if event_type == "status":
            self._push_local_status(payload)
            return
        if event_type == "gesture":
            self._push_gesture_message(payload)

    def _push_local_status(self, payload: Dict[str, Any]) -> None:
        with self._lock:
            if self._seat_owner is None:
                return
            code, client_id = self._seat_owner
            room = self._rooms.get(code)
            participant = room.participants.get(client_id) if room else None
            if participant is None:
                return
            message = {"type": "local_status", "ts": time.time(), **payload}
            participant.send_json(message)

    def _push_gesture_message(self, payload: Dict[str, Any]) -> None:
        with self._lock:
            if self._seat_owner is None:
                return
            code, client_id = self._seat_owner
            room = self._rooms.get(code)
            participant = room.participants.get(client_id) if room else None
            if participant is None:
                return
            message = {
                "type": "gesture",
                "id": uuid.uuid4().hex[:12],
                "from": client_id,
                "sender_role": participant.role,
                "sender_name": participant.display_name,
                **payload,
            }
            self._relay(room, message)

    # ------------------------------------------------------------------
    # Room sweep
    # ------------------------------------------------------------------
    def _purge_rooms(self) -> None:
        now = time.time()
        expired: List[str] = []
        for code, room in self._rooms.items():
            if now - room.created_at > self.max_age:
                expired.append(code)
            elif room.connected_count == 0 and (now - room.last_activity) > self.idle_ttl:
                expired.append(code)
        for code in expired:
            self._rooms.pop(code, None)
            self._purged_rooms += 1
        if expired:
            logger.info(f"Connect sweep removed {len(expired)} expired room(s).")

    def _sweep_loop(self) -> None:
        interval = float(Config.CONNECT_ROOM_SWEEP_INTERVAL_SECONDS)
        while True:
            time.sleep(interval)
            try:
                with self._lock:
                    self._purge_rooms()
            except Exception as exc:
                logger.warning(f"Connect room sweep failed: {exc}")


connect_room_service = ConnectRoomService()
