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

import hashlib
import hmac
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
# Per-participant gesture history cap. A room only relays small recognized
# events, so this bounded, in-memory deque is the entire session history:
# oldest entries drop first once the limit is reached. No database involved.
MAX_GESTURE_HISTORY = int(Config.CONNECT_GESTURE_HISTORY_LIMIT)
_MAX_DISPLAY_NAME_LENGTH = 40
_MIN_ROOM_PASSWORD_LENGTH = 4
_MAX_ROOM_PASSWORD_LENGTH = 128
_MAX_SESSION_TOKEN_LENGTH = 512

_ERROR_MESSAGES = {
    "display_name_required": "Display name is required.",
    "display_name_too_long": "Display name must be 40 characters or fewer.",
    "room_password_required": "Room password is required.",
    "room_password_too_short": "Room password must be at least 4 characters.",
    "room_password_too_long": "Room password must be 128 characters or fewer.",
    "room_code_required": "Room code is required.",
    "invalid_room": "That room does not exist or has expired.",
    "authentication_failed": "Unable to authenticate for this room.",
    "room_full": "Room is full. A Connect room supports exactly two participants.",
}


def _session_token_digest(token: str) -> str:
    """Hash an opaque session token before keeping it in process memory."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


_PASSWORD_HASH_ITERATIONS = 210_000


def _room_password_hash(password: str) -> str:
    """Create a salted, intentionally slow password hash.

    The format is local to the ephemeral room service and keeps plaintext room
    passwords out of memory after the create/join API call returns.
    """
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        _PASSWORD_HASH_ITERATIONS,
    )
    return "pbkdf2_sha256${}${}${}".format(
        _PASSWORD_HASH_ITERATIONS,
        salt.hex(),
        derived.hex(),
    )


def _room_password_matches(password_hash: str, password: str) -> bool:
    try:
        scheme, iterations, salt_hex, expected_hex = password_hash.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        rounds = int(iterations)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(expected_hex)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


class Participant:
    """One independent browser participant in a room.

    The session credential is deliberately kept separate from the public
    participant fields.  Only its digest is retained here; the raw token is
    returned once by the same-origin room API and is never included in a WebSocket
    payload or participant summary.
    """

    def __init__(
        self,
        client_id: str,
        role: str,
        display_name: str,
        session_token_hash: str = "",
    ) -> None:
        self.client_id = client_id
        self.role = role
        self.user_number = 1 if role == _ROLE_CREATOR else 2
        self.display_name = str(display_name or "").strip()
        self._session_token_hash = session_token_hash
        self.ws: Any = None
        self.connected = False
        self.camera_active = False
        self.recognition_active = False
        self.last_gesture: Optional[Dict[str, Any]] = None
        # Gestures *received from the other participant* during this session —
        # the server-side source of the "Other User -> Gesture History" list.
        # A fresh participant (join or rejoin after a leave) starts empty; a
        # disconnected participant keeps it until the room purges them.
        self.received_gestures: Deque[Dict[str, Any]] = deque(maxlen=MAX_GESTURE_HISTORY)
        self.seen_event_ids: Deque[str] = deque(maxlen=_MAX_SEEN_EVENT_IDS)
        self.send_lock = threading.Lock()

    @property
    def user_label(self) -> str:
        return f"User {self.user_number}"

    def issue_session_token(self) -> str:
        """Rotate and return a raw token; only its digest is stored."""
        token = secrets.token_urlsafe(32)
        self._session_token_hash = _session_token_digest(token)
        return token

    def accepts_session_token(self, token: Any) -> bool:
        value = str(token or "")
        if not value or len(value) > _MAX_SESSION_TOKEN_LENGTH:
            return False
        return bool(self._session_token_hash) and secrets.compare_digest(
            self._session_token_hash,
            _session_token_digest(value),
        )

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
    """A private two-person communication room.

    ``_password_hash`` is intentionally private and is never included in a
    snapshot, status response, history entry, or peer summary.
    """

    def __init__(self, code: str, password_hash: str) -> None:
        self.code = code
        self._password_hash = password_hash
        self.created_at = time.time()
        self.last_activity = time.time()
        self.participants: Dict[str, Participant] = {}
        self.history: Deque[Dict[str, Any]] = deque(maxlen=int(Config.CONNECT_ROOM_HISTORY_LIMIT))

    def accepts_password(self, password: Any) -> bool:
        value = str(password if password is not None else "")
        return bool(value and self._password_hash and _room_password_matches(self._password_hash, value))

    @property
    def connected_count(self) -> int:
        return sum(1 for participant in self.participants.values() if participant.connected)

    def peer_of(self, client_id: str) -> Optional[Participant]:
        """Return the other participant of this two-person room, if present."""
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
            "display_name": participant.display_name if participant else "",
            "participants": self._participant_summary(),
            "history": list(self.history),
            # Per-participant remote gesture history (gestures this participant
            # received from the other one).  This is the only history the
            # resume path synchronizes — live events are relayed one at a time
            # and appended locally, so the full history is never resent for
            # every gesture.
            "gesture_history": list(participant.received_gestures) if participant else [],
            "ts": time.time(),
        }

    def _participant_summary(self, compatibility_source: Optional[Tuple[str, str]] = None) -> List[Dict[str, Any]]:
        """Return participant state without granting or reserving a camera.

        ``compatibility_source`` is used only for old clients that still send
        the retired ``gesture_cam`` message.  New Connect clients never receive
        the legacy ``seat`` key and never use that compatibility path.
        """
        summary = []
        for participant in sorted(self.participants.values(), key=lambda item: item.user_number):
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

    @staticmethod
    def _validated_display_name(value: Any) -> Tuple[Optional[str], Optional[str]]:
        name = str(value or "").strip()
        if not name:
            return None, "display_name_required"
        if len(name) > _MAX_DISPLAY_NAME_LENGTH:
            return None, "display_name_too_long"
        return name, None

    @staticmethod
    def _validated_room_password(value: Any) -> Tuple[Optional[str], Optional[str]]:
        # Password whitespace is meaningful and is not stripped before
        # hashing.  A whitespace-only value is still rejected as empty.
        password = str(value if value is not None else "")
        if not password.strip():
            return None, "room_password_required"
        if len(password) < _MIN_ROOM_PASSWORD_LENGTH:
            return None, "room_password_too_short"
        if len(password) > _MAX_ROOM_PASSWORD_LENGTH:
            return None, "room_password_too_long"
        return password, None

    @staticmethod
    def _result_error(code: str, message: Optional[str] = None) -> Dict[str, Any]:
        return {
            "success": False,
            "error": code,
            "message": message or _ERROR_MESSAGES.get(code, "Unable to complete the room request."),
        }

    @staticmethod
    def _participant_identity(participant: Participant) -> Dict[str, Any]:
        """Return the public identity/state-independent participant fields."""
        return {
            "client_id": participant.client_id,
            "role": participant.role,
            "user_number": participant.user_number,
            "user_label": participant.user_label,
            "display_name": participant.display_name,
        }

    def _auth_result(self, room: Room, participant: Participant, token: str) -> Dict[str, Any]:
        # This is the only response that carries the opaque session token.  It
        # is not a password and it is never copied to a peer or room status.
        return {
            "success": True,
            "code": room.code,
            "ws_path": Config.CONNECT_WS_PATH,
            "session_token": token,
            "participant": self._participant_identity(participant),
        }

    # ------------------------------------------------------------------
    # Authenticated room API operations
    # ------------------------------------------------------------------
    def create_room(
        self,
        display_name: Any,
        password: Any,
        client_id: Any = None,
    ) -> Dict[str, Any]:
        """Create a room and issue an opaque token for its creator.

        The HTTP route calls this before opening the WebSocket.  Keeping the
        password operation outside the relay means create/join WebSocket
        messages contain only an already-issued session token and identity
        metadata; a password can never be relayed to the other participant.
        """
        name, name_error = self._validated_display_name(display_name)
        if name_error:
            return self._result_error(name_error)
        room_password, password_error = self._validated_room_password(password)
        if password_error:
            return self._result_error(password_error)

        resolved_client_id = str(client_id or "")
        if not self._valid_client_id(resolved_client_id):
            resolved_client_id = self._new_client_id()

        with self._lock:
            self._purge_rooms()
            room = Room(
                self._generate_code(),
                _room_password_hash(room_password),
            )
            participant = Participant(resolved_client_id, _ROLE_CREATOR, name)
            token = participant.issue_session_token()
            room.participants[resolved_client_id] = participant
            self._rooms[room.code] = room
            self._record_activity(room)
            logger.info(f"Connect room created: {room.code} ({resolved_client_id}, User 1)")
            return self._auth_result(room, participant, token)

    def join_room(
        self,
        code: Any,
        display_name: Any,
        password: Any,
        client_id: Any = None,
    ) -> Dict[str, Any]:
        """Authenticate a join request and issue a token for User 2.

        Password failures intentionally use one generic authentication error;
        the server does not disclose whether a code is occupied by a particular
        participant or whether only the password was wrong.
        """
        normalized_code = self._normalize_code(code)
        if not normalized_code:
            return self._result_error("room_code_required")
        name, name_error = self._validated_display_name(display_name)
        if name_error:
            return self._result_error(name_error)
        room_password, password_error = self._validated_room_password(password)
        if password_error:
            return self._result_error(password_error)

        resolved_client_id = str(client_id or "")
        if not self._valid_client_id(resolved_client_id):
            resolved_client_id = self._new_client_id()

        with self._lock:
            self._purge_rooms()
            room = self._rooms.get(normalized_code)
            if room is None:
                return self._result_error("invalid_room")
            if not room.accepts_password(room_password):
                return self._result_error("authentication_failed")

            existing = room.participants.get(resolved_client_id)
            if existing is not None:
                # A re-authenticated browser keeps its original seat and
                # identity.  Rotate its opaque token rather than creating a
                # third participant.
                token = existing.issue_session_token()
                self._record_activity(room)
                return self._auth_result(room, existing, token)

            if len(room.participants) >= self.max_participants:
                return self._result_error("room_full")

            # Normally the creator is already present, so a new participant is
            # User 2.  If User 1 explicitly left, preserve the original
            # two-seat model by allowing the next authenticated participant to
            # occupy the open creator seat rather than creating User 3 or two
            # joiners.
            role = (
                _ROLE_JOINER
                if any(p.role == _ROLE_CREATOR for p in room.participants.values())
                else _ROLE_CREATOR
            )
            participant = Participant(resolved_client_id, role, name)
            token = participant.issue_session_token()
            room.participants[resolved_client_id] = participant
            self._record_activity(room)
            logger.info(f"Participant joined Connect room {room.code}: {resolved_client_id} ({role})")
            return self._auth_result(room, participant, token)

    def room_status(
        self,
        code: Any,
        client_id: Any = None,
        session_token: Any = None,
    ) -> Dict[str, Any]:
        """Return safe room status for an authenticated participant only."""
        normalized_code = self._normalize_code(code)
        with self._lock:
            self._purge_rooms()
            room = self._rooms.get(normalized_code)
            if room is None:
                return self._result_error("invalid_room")
            participant = room.participants.get(str(client_id or ""))
            if participant is None or not participant.accepts_session_token(session_token):
                return self._result_error("authentication_failed")
            return {
                "success": True,
                "code": room.code,
                "connected_count": room.connected_count,
                "max_participants": self.max_participants,
                "participants": room._participant_summary(self._legacy_source),
            }

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
    # Authenticated WebSocket attach / resume
    # ------------------------------------------------------------------
    def _handle_authenticated_attach(
        self,
        ws: Any,
        payload: Dict[str, Any],
        expected_role: Optional[str] = None,
    ) -> None:
        """Attach a WebSocket using the opaque token issued by the room API.

        Passwords are deliberately rejected here.  Authentication happens via
        the HTTP room API so a relay message can never contain a room password.
        """
        if "password" in payload or "room_password" in payload:
            self._send_error(
                ws,
                "bad_request",
                "Authenticate through the Connect room API before opening the room WebSocket.",
            )
            return

        code = self._normalize_code(payload.get("code"))
        client_id = str(payload.get("client_id") or "")
        token = payload.get("session_token")
        requested_role = str(payload.get("role") or "").lower()
        if not code or not self._valid_client_id(client_id) or not token:
            self._send_error(ws, "authentication_required", _ERROR_MESSAGES["authentication_failed"])
            return

        with self._lock:
            self._purge_rooms()
            room = self._rooms.get(code)
            participant = room.participants.get(client_id) if room else None
            role_matches = (
                participant is not None
                and (not expected_role or participant.role == expected_role)
                and (not requested_role or participant.role == requested_role)
            )
            if room is None:
                self._send_error(ws, "invalid_room", _ERROR_MESSAGES["invalid_room"])
                return
            if not role_matches or not participant.accepts_session_token(token):
                self._send_error(ws, "invalid_session", _ERROR_MESSAGES["authentication_failed"])
                return
            self._resume_participant(room, client_id, ws)

    def _handle_create(self, ws: Any, payload: Dict[str, Any]) -> None:
        self._handle_authenticated_attach(ws, payload, _ROLE_CREATOR)

    def _handle_join(self, ws: Any, payload: Dict[str, Any]) -> None:
        self._handle_authenticated_attach(ws, payload, _ROLE_JOINER)

    def _handle_resume(self, ws: Any, payload: Dict[str, Any]) -> None:
        self._handle_authenticated_attach(ws, payload)

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
        # The leaving participant's received history dies with their seat, and
        # the peer's view of *this* participant resets too: the next joiner
        # starts with an empty "Other User" history instead of inheriting the
        # previous occupant's gestures.  A plain disconnect (resume path)
        # keeps the seat and therefore keeps the history.
        peer = room.peer_of(client_id)
        if peer is not None:
            peer.received_gestures.clear()
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

    def _record_accepted_gesture(
        self,
        room: Room,
        participant: Participant,
        message: Dict[str, Any],
    ) -> None:
        """Store an accepted gesture for the session and relay it to the room.

        The event is appended to the *peer's* per-participant received history
        so each browser can rebuild its "Other User -> Gesture History" list on
        resume.  Only completed gesture events reach this path — text messages
        relay separately — so the gesture history can never contain text.
        """
        participant.last_gesture = {
            "gesture_id": message["gesture_id"],
            "symbol": message["symbol"],
            "meaning": message["meaning"],
            "kind": message["kind"],
            "confidence": message["confidence"],
            "has_replay": message["has_replay"],
            "ts": message["ts"],
        }
        peer = room.peer_of(participant.client_id)
        if peer is not None:
            peer.received_gestures.append(message)
        self._relay(room, message)
        self._send_peers(room)

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
            self._record_accepted_gesture(room, participant, message)

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
                self._record_accepted_gesture(room, participant, message)
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
