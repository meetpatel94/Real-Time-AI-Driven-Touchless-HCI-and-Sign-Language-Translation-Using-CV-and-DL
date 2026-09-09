"""Connect room relay for independent, two-way participants.

Connect rooms only relay small, already-recognized events.  Each browser owns
its camera and recognition loop; the WebSocket carries gesture meanings and
text, never video, image data, or landmark streams.  Rooms are process-local
and ephemeral and contain at most a creator and a joiner.

The existing server-side GestureEngine hook is retained as a fail-open local
fallback for installations that still use the server camera.  It is not a
room-wide camera lock and it never blocks either browser participant.
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
_MAX_TEXT_LENGTH = 600
_MAX_GESTURE_ID_LENGTH = 120
_MAX_GESTURE_MEANING_LENGTH = 180
_MAX_GESTURE_SYMBOL_LENGTH = 32
_MAX_EVENT_ID_LENGTH = 96
_MAX_SEEN_EVENT_IDS = 240


class Participant:
    """One independent browser participant in a room."""

    def __init__(self, client_id: str, role: str, display_name: str) -> None:
        self.client_id = client_id
        self.role = role
        self.user_number = 1 if role == _ROLE_CREATOR else 2
        default_name = f"User {self.user_number}"
        self.display_name = display_name or default_name
        self.ws: Any = None
        self.connected = False
        self.camera_active = False
        self.recognition_active = False
        self.last_gesture: Optional[Dict[str, Any]] = None
        self.seen_event_ids: Deque[str] = deque(maxlen=_MAX_SEEN_EVENT_IDS)
        self.send_lock = threading.Lock()

    @property
    def user_label(self) -> str:
        return f"User {self.user_number}"

    def attach(self, ws: Any) -> None:
        self.ws = ws
        self.connected = True

    def detach(self) -> None:
        self.ws = None
        self.connected = False
        self.camera_active = False
        self.recognition_active = False

    def send_json(self, payload: Dict[str, Any]) -> bool:
        if not self.connected or self.ws is None:
            return False
        try:
            with self.send_lock:
                self.ws.send(json.dumps(payload, separators=(",", ":")))
            return True
        except Exception:
            # The room sweep/reconnect path will publish the new disconnected
            # state.  Never let one dead socket break delivery to its peer.
            self.connected = False
            self.camera_active = False
            self.recognition_active = False
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

    def snapshot_for(self, client_id: str, role: Optional[str] = None) -> Dict[str, Any]:
        participant = self.participants.get(client_id)
        resolved_role = role or (participant.role if participant else _ROLE_JOINER)
        return {
            "type": "room_snapshot",
            "code": self.code,
            "client_id": client_id,
            "role": resolved_role,
            "user_number": 1 if resolved_role == _ROLE_CREATOR else 2,
            "user_label": "User 1" if resolved_role == _ROLE_CREATOR else "User 2",
            "participants": self._participant_summary(),
            "history": list(self.history),
            "ts": time.time(),
        }

    def _participant_summary(self, compatibility_source: Optional[Tuple[str, str]] = None) -> List[Dict[str, Any]]:
        """Return participant state without granting or reserving a camera.

        ``compatibility_source`` is used only for old clients that still send
        the retired ``gesture_cam`` message.  New Connect clients never receive
        the legacy ``seat`` key and never use that compatibility path.
        """
        summary = []
        for participant in self.participants.values():
            item = {
                "client_id": participant.client_id,
                "role": participant.role,
                "user_number": participant.user_number,
                "user_label": participant.user_label,
                "display_name": participant.display_name,
                "connected": participant.connected,
                "camera_active": bool(participant.camera_active),
                "recognition_active": bool(participant.recognition_active),
                "last_gesture": participant.last_gesture,
            }
            # Kept solely so pre-existing transport-level clients can migrate
            # without crashing. It is never consulted by Connect recognition.
            if compatibility_source is not None:
                item["seat"] = compatibility_source == (self.code, participant.client_id)
            summary.append(item)
        return summary


class ConnectRoomService:
    """Own rooms and relay independent gesture/text streams for /connect."""

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
        # ws identity -> (room code, client id); used only for lifecycle lookup.
        self._ws_index: Dict[int, Tuple[str, str]] = {}

        # The retired protocol is kept as a narrow migration shim for old
        # clients/tests. It is never used by the camera or recognition path.
        self._legacy_source: Optional[Tuple[str, str]] = None

        # Optional server-camera fallback. Browser recognition takes precedence
        # whenever any participant reports recognition_active=True.
        self._engine_source: Optional[Tuple[str, str]] = None
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
        logger.info("Connect room relay initialized (independent participant streams).")

    # ------------------------------------------------------------------
    # Compatibility property for the old transport tests only.
    # ------------------------------------------------------------------
    @property
    def _seat_owner(self) -> Optional[Tuple[str, str]]:  # pragma: no cover - migration shim
        return self._legacy_source

    @_seat_owner.setter
    def _seat_owner(self, value: Optional[Tuple[str, str]]) -> None:  # pragma: no cover
        self._legacy_source = value

    # ------------------------------------------------------------------
    # Room code and client id helpers
    # ------------------------------------------------------------------
    def _generate_code(self) -> str:
        prefix = str(Config.CONNECT_ROOM_CODE_PREFIX)
        alphabet = str(Config.CONNECT_ROOM_CODE_ALPHABET)
        length = int(Config.CONNECT_ROOM_CODE_LENGTH)
        for _ in range(200):
            code = prefix + "-" + "".join(secrets.choice(alphabet) for _ in range(length))
            if code not in self._rooms:
                return code
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

    @staticmethod
    def _bounded_text(value: Any, length: int) -> str:
        return str(value or "").strip()[:length]

    @staticmethod
    def _bounded_confidence(value: Any) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.0

    def _new_client_id(self) -> str:
        return "gf-" + uuid.uuid4().hex[:20]

    # ------------------------------------------------------------------
    # Lifecycle and relay helpers
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
            "participants": room._participant_summary(self._legacy_source),
            "ts": time.time(),
        }
        for participant in list(room.participants.values()):
            participant.send_json(payload)

    def _relay(self, room: Room, payload: Dict[str, Any]) -> None:
        """Append an event to history and deliver it to both participants."""
        room.history.append(payload)
        self._record_activity(room)
        for participant in list(room.participants.values()):
            participant.send_json(payload)

    def _send_error(self, ws: Any, code: str, message: str) -> None:
        self._safe_send(ws, {"type": "error", "error": code, "message": message})

    @staticmethod
    def _safe_send(ws: Any, payload: Dict[str, Any]) -> None:
        try:
            ws.send(json.dumps(payload, separators=(",", ":")))
        except Exception:
            pass

    def _participant_for_ws(self, ws: Any) -> Optional[Tuple[Room, Participant]]:
        entry = self._ws_index.get(id(ws))
        if not entry:
            return None
        code, client_id = entry
        room = self._rooms.get(code)
        participant = room.participants.get(client_id) if room else None
        return (room, participant) if room and participant else None

    # ------------------------------------------------------------------
    # Message dispatch
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
        elif message_type in {"device_status", "camera_status"}:
            self._handle_device_status(ws, payload)
        elif message_type in {"gesture", "client_gesture"}:
            self._handle_client_gesture(ws, payload)
        elif message_type == "message":
            self._handle_text(ws, payload)
        elif message_type == "ping":
            self._safe_send(ws, {"type": "pong", "ts": time.time()})
        elif message_type == "gesture_cam":
            # Migration shim only. New clients never send this message.
            self._handle_legacy_source(ws, payload)
        else:
            self._send_error(ws, "bad_request", f"Unknown message type '{message_type}'.")

    # ------------------------------------------------------------------
    # Room creation / joining / resume
    # ------------------------------------------------------------------
    def _handle_create(self, ws: Any, payload: Dict[str, Any]) -> None:
        client_id = payload.get("client_id") or ""
        if not self._valid_client_id(client_id):
            client_id = self._new_client_id()
        display_name = self._bounded_text(payload.get("display_name"), 40)

        with self._lock:
            self._purge_rooms()
            room = Room(self._generate_code())
            participant = Participant(client_id, _ROLE_CREATOR, display_name)
            participant.attach(ws)
            room.participants[client_id] = participant
            self._rooms[room.code] = room
            self._ws_index[id(ws)] = (room.code, client_id)
            self._record_activity(room)
            self._safe_send(ws, room.snapshot_for(client_id, _ROLE_CREATOR))
            self._send_peers(room)
            logger.info(f"Connect room created: {room.code} ({client_id}, User 1)")

    def _handle_join(self, ws: Any, payload: Dict[str, Any]) -> None:
        code = self._normalize_code(payload.get("code"))
        client_id = payload.get("client_id") or ""
        if not self._valid_client_id(client_id):
            client_id = self._new_client_id()
        display_name = self._bounded_text(payload.get("display_name"), 40)

        with self._lock:
            self._purge_rooms()
            room = self._rooms.get(code)
            if room is None:
                self._send_error(ws, "invalid_room", f"Room '{code}' does not exist or has expired.")
                return
            if client_id in room.participants:
                self._resume_participant(room, client_id, ws)
                return
            if len(room.participants) >= self.max_participants:
                self._send_error(
                    ws,
                    "room_full",
                    "Room is full. A Connect room supports exactly two participants.",
                )
                return
            role = (
                _ROLE_JOINER
                if _ROLE_CREATOR in {participant.role for participant in room.participants.values()}
                else _ROLE_CREATOR
            )
            participant = Participant(client_id, role, display_name)
            participant.attach(ws)
            room.participants[client_id] = participant
            self._ws_index[id(ws)] = (room.code, client_id)
            self._record_activity(room)
            self._safe_send(ws, room.snapshot_for(client_id, role))
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
        previous_ws = participant.ws
        if previous_ws is not None and previous_ws is not ws:
            self._ws_index.pop(id(previous_ws), None)
        participant.attach(ws)
        self._ws_index[id(ws)] = (room.code, client_id)
        self._record_activity(room)
        self._safe_send(ws, room.snapshot_for(client_id, participant.role))
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
            if room.participants:
                self._send_peers(room)

    def _remove_participant(self, room: Room, client_id: str) -> None:
        participant = room.participants.pop(client_id, None)
        if participant is not None:
            participant.detach()
        source = (room.code, client_id)
        if self._legacy_source == source:
            self._legacy_source = None
        if self._engine_source == source:
            self._engine_source = None
            connect_detector.reset()
        self._record_activity(room)
        if not room.participants:
            self._rooms.pop(room.code, None)
            logger.info(f"Connect room {room.code} closed (empty).")

    # ------------------------------------------------------------------
    # Independent browser device status
    # ------------------------------------------------------------------
    def _handle_device_status(self, ws: Any, payload: Dict[str, Any]) -> None:
        with self._lock:
            found = self._participant_for_ws(ws)
            if not found:
                self._send_error(ws, "not_in_room", "You are not in a room.")
                return
            room, participant = found
            camera_active = bool(payload.get("camera_active", payload.get("enabled", False)))
            recognition_active = bool(payload.get("recognition_active", False))
            participant.camera_active = camera_active
            participant.recognition_active = camera_active and recognition_active
            self._record_activity(room)
            self._send_peers(room)

    # ------------------------------------------------------------------
    # Lightweight client-recognized gestures
    # ------------------------------------------------------------------
    @staticmethod
    def _contains_forbidden_camera_payload(payload: Dict[str, Any]) -> bool:
        forbidden = {
            "frame", "frames", "image", "images", "video", "webcam",
            "landmarks", "landmark", "hand_landmarks", "raw_landmarks",
            "continuous_landmarks", "jpeg", "data_url", "blob",
        }
        return any(key in payload for key in forbidden)

    def _event_id_for(self, participant: Participant, payload: Dict[str, Any]) -> str:
        supplied = self._bounded_text(payload.get("id") or payload.get("event_id"), _MAX_EVENT_ID_LENGTH)
        return supplied or uuid.uuid4().hex[:16]

    def _build_gesture_message(
        self,
        participant: Participant,
        payload: Dict[str, Any],
        event_id: str,
    ) -> Optional[Dict[str, Any]]:
        if self._contains_forbidden_camera_payload(payload):
            return None
        kind = str(payload.get("kind") or "pose").lower().strip()
        if kind not in {"pose", "custom"}:
            kind = "pose"
        gesture_id = self._bounded_text(payload.get("gesture_id"), _MAX_GESTURE_ID_LENGTH)
        meaning = self._bounded_text(payload.get("meaning"), _MAX_GESTURE_MEANING_LENGTH)
        symbol = self._bounded_text(payload.get("symbol"), _MAX_GESTURE_SYMBOL_LENGTH)
        if not gesture_id or not meaning:
            return None
        return {
            "type": "gesture",
            "id": event_id,
            "from": participant.client_id,
            "sender_role": participant.role,
            "sender_name": participant.display_name,
            "sender_user": participant.user_label,
            "kind": kind,
            "gesture_id": gesture_id,
            "symbol": symbol or "✋",
            "meaning": meaning,
            "confidence": self._bounded_confidence(payload.get("confidence", 0.0)),
            "has_replay": bool(payload.get("has_replay", False)),
            "ts": time.time(),
        }

    def _handle_client_gesture(self, ws: Any, payload: Dict[str, Any]) -> None:
        with self._lock:
            found = self._participant_for_ws(ws)
            if not found:
                self._send_error(ws, "not_in_room", "You are not in a room.")
                return
            room, participant = found
            event_id = self._event_id_for(participant, payload)
            if event_id in participant.seen_event_ids:
                return
            message = self._build_gesture_message(participant, payload, event_id)
            if message is None:
                self._send_error(
                    ws,
                    "invalid_gesture",
                    "Send only a recognized gesture id, meaning, symbol, and confidence.",
                )
                return
            participant.seen_event_ids.append(event_id)
            participant.last_gesture = {
                "gesture_id": message["gesture_id"],
                "symbol": message["symbol"],
                "meaning": message["meaning"],
                "kind": message["kind"],
                "confidence": message["confidence"],
                "has_replay": message["has_replay"],
                "ts": message["ts"],
            }
            self._relay(room, message)
            self._send_peers(room)

    # Public helper for the local engine fallback and transport-level tests.
    def push_gesture(self, client_id: str, payload: Dict[str, Any]) -> bool:
        with self._lock:
            for room in self._rooms.values():
                participant = room.participants.get(str(client_id))
                if participant is None:
                    continue
                event_id = self._event_id_for(participant, payload)
                if event_id in participant.seen_event_ids:
                    return False
                message = self._build_gesture_message(participant, payload, event_id)
                if message is None:
                    return False
                participant.seen_event_ids.append(event_id)
                participant.last_gesture = {
                    key: message[key]
                    for key in ("gesture_id", "symbol", "meaning", "kind", "confidence", "has_replay", "ts")
                }
                self._relay(room, message)
                self._send_peers(room)
                return True
        return False

    # ------------------------------------------------------------------
    # Text messages
    # ------------------------------------------------------------------
    def _handle_text(self, ws: Any, payload: Dict[str, Any]) -> None:
        text = self._bounded_text(payload.get("text"), _MAX_TEXT_LENGTH)
        if not text:
            return
        with self._lock:
            found = self._participant_for_ws(ws)
            if not found:
                self._send_error(ws, "not_in_room", "You are not in a room.")
                return
            room, participant = found
            event_id = self._bounded_text(payload.get("id") or payload.get("event_id"), _MAX_EVENT_ID_LENGTH)
            event_id = event_id or uuid.uuid4().hex[:16]
            if event_id in participant.seen_event_ids:
                return
            participant.seen_event_ids.append(event_id)
            self._relay(room, {
                "type": "text",
                "id": event_id,
                "from": participant.client_id,
                "sender_role": participant.role,
                "sender_name": participant.display_name,
                "sender_user": participant.user_label,
                "text": text,
                "ts": time.time(),
            })

    # ------------------------------------------------------------------
    # Retired camera-seat protocol migration shim
    # ------------------------------------------------------------------
    def _handle_legacy_source(self, ws: Any, payload: Dict[str, Any]) -> None:  # pragma: no cover - old clients
        """Accept an old message without ever blocking the real data path.

        Old clients used this message to request a mutually exclusive source.
        We simply remember the last legacy sender so those clients can finish
        upgrading. There is no queue, handover, or recognition gate here.
        """
        enabled = bool(payload.get("enabled"))
        with self._lock:
            found = self._participant_for_ws(ws)
            if not found:
                self._send_error(ws, "not_in_room", "You are not in a room.")
                return
            room, participant = found
            source = (room.code, participant.client_id)
            if enabled:
                self._legacy_source = source
            elif self._legacy_source == source:
                self._legacy_source = None
            self._send_peers(room)

    # ------------------------------------------------------------------
    # Disconnect and server GestureEngine fallback
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
            if self._legacy_source == (code, client_id):
                self._legacy_source = None
            if self._engine_source == (code, client_id):
                self._engine_source = None
                connect_detector.reset()
            self._record_activity(room)
            self._send_peers(room)
            logger.info(f"Connect participant disconnected: {client_id} in {room.code}")

    def notify_camera_off(self) -> None:
        """Reset only the optional server-camera fallback.

        Browser-owned Connect cameras do not use the global CameraManager, so a
        camera toggle in another module must not change either participant's
        local state.
        """
        with self._lock:
            if self._camera_was_off:
                return
            self._camera_was_off = True
            connect_detector.reset()
            self._engine_source = None

    def on_engine_frame(
        self,
        left_hand: Any,
        right_hand: Any,
        connect_active: bool,
        client_id: Optional[str] = None,
    ) -> None:
        """Feed the optional server camera without creating a room-wide lock.

        A browser that is recognizing locally always wins. If no participant is
        using local recognition, the legacy server CameraManager can still feed
        the creator as a backwards-compatible fallback. This method never
        changes participant availability and never prevents a joiner from
        sending its own client-recognized events.
        """
        with self._lock:
            self._camera_was_off = False
            if not connect_active:
                self._engine_source = None
                connect_detector.reset()
                return

            target: Optional[Tuple[str, str]] = None
            if client_id:
                for code, room in self._rooms.items():
                    participant = room.participants.get(str(client_id))
                    if participant and participant.connected:
                        target = (code, participant.client_id)
                        break
            if target is None:
                for code, room in self._rooms.items():
                    creator = next(
                        (p for p in room.participants.values()
                         if p.role == _ROLE_CREATOR and p.connected),
                        None,
                    )
                    if creator:
                        target = (code, creator.client_id)
                        break
            if target is None:
                self._engine_source = None
                connect_detector.reset()
                return

            room = self._rooms.get(target[0])
            if room is None:
                self._engine_source = None
                connect_detector.reset()
                return
            # Do not duplicate a browser's own local stream with the global
            # server stream. Other participants remain fully independent.
            if any(p.recognition_active for p in room.participants.values()):
                self._engine_source = None
                connect_detector.reset()
                return
            self._engine_source = target
        connect_detector.update(left_hand, right_hand)

    def _detector_sink(self, event_type: str, payload: Dict[str, Any]) -> None:
        if event_type == "status":
            self._push_local_status(payload)
        elif event_type == "gesture":
            source_client_id = self._engine_source[1] if self._engine_source else None
            self._push_gesture_message(payload, source_client_id=source_client_id)

    def _push_local_status(self, payload: Dict[str, Any]) -> None:
        with self._lock:
            if self._engine_source is None:
                return
            code, client_id = self._engine_source
            room = self._rooms.get(code)
            participant = room.participants.get(client_id) if room else None
            if participant is None:
                return
            participant.send_json({"type": "local_status", "ts": time.time(), **payload})

    def _push_gesture_message(
        self,
        payload: Dict[str, Any],
        source_client_id: Optional[str] = None,
        client_id: Optional[str] = None,
    ) -> None:
        """Relay a recognized payload from an explicit participant source."""
        source = source_client_id or client_id
        with self._lock:
            if source is None and self._engine_source is not None:
                source = self._engine_source[1]
            if source is None and self._legacy_source is not None:
                source = self._legacy_source[1]
            if source is None:
                return
            self.push_gesture(str(source), payload)

    # ------------------------------------------------------------------
    # Room sweep
    # ------------------------------------------------------------------
    def _purge_rooms(self) -> None:
        now = time.time()
        expired: List[str] = []
        for code, room in list(self._rooms.items()):
            if now - room.created_at > self.max_age:
                expired.append(code)
            elif room.connected_count == 0 and (now - room.last_activity) > self.idle_ttl:
                expired.append(code)
        for code in expired:
            room = self._rooms.pop(code, None)
            if room is None:
                continue
            for participant in room.participants.values():
                if participant.ws is not None:
                    self._ws_index.pop(id(participant.ws), None)
            if self._legacy_source and self._legacy_source[0] == code:
                self._legacy_source = None
            if self._engine_source and self._engine_source[0] == code:
                self._engine_source = None
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
