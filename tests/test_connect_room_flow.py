"""Regression tests for the password-protected two-person Connect relay.

The tests exercise the transport-agnostic service the same way the Flask-Sock
route does: room authentication happens through the room API operation, then
only an opaque session token and public identity are sent on the WebSocket.
"""

import json
import unittest

from services.connect_room_service import connect_room_service


ROOM_PASSWORD = "room-pass-123"


class FakeWs:
    """Minimal stand-in for a Flask-Sock WebSocket object."""

    def __init__(self):
        self.sent = []

    def send(self, raw):
        self.sent.append(json.loads(raw))

    def messages(self, message_type):
        return [message for message in self.sent if message.get("type") == message_type]


def reset_service():
    """Clear rooms/seat state between tests (ephemeral in-memory relay)."""
    with connect_room_service._lock:
        for room in list(connect_room_service._rooms.values()):
            for participant in room.participants.values():
                participant.detach()
        connect_room_service._rooms.clear()
        connect_room_service._ws_index.clear()
        connect_room_service._seat_owner = None
        connect_room_service._engine_source = None


def relay(device, payload):
    """Send a client message the way the WebSocket route does."""
    connect_room_service.handle_message(device, json.dumps(payload))


def attach(device, auth, message_type):
    participant = auth["participant"]
    relay(device, {
        "type": message_type,
        "code": auth["code"],
        "client_id": participant["client_id"],
        "role": participant["role"],
        "display_name": participant["display_name"],
        "session_token": auth["session_token"],
    })


def create_room(device, client_id="device-a-client-id", display_name="Rahul", password=ROOM_PASSWORD):
    auth = connect_room_service.create_room(display_name, password, client_id)
    assert auth["success"], auth
    attach(device, auth, "create")
    snapshots = device.messages("room_snapshot")
    assert snapshots, "creator did not receive a room snapshot"
    return auth, snapshots[-1]


def join_room(
    device,
    code,
    client_id="device-b-client-id",
    display_name="Amit",
    password=ROOM_PASSWORD,
):
    auth = connect_room_service.join_room(code, display_name, password, client_id)
    if auth["success"]:
        attach(device, auth, "join")
    return auth


class ConnectRoomFlowTest(unittest.TestCase):

    def setUp(self):
        reset_service()

    def tearDown(self):
        reset_service()

    def test_create_room_requires_name_and_password(self):
        missing_name = connect_room_service.create_room("   ", ROOM_PASSWORD, "device-a-client-id")
        self.assertFalse(missing_name["success"])
        self.assertEqual(missing_name["error"], "display_name_required")

        missing_password = connect_room_service.create_room("Rahul", "", "device-a-client-id")
        self.assertFalse(missing_password["success"])
        self.assertEqual(missing_password["error"], "room_password_required")

    def test_create_room_stores_trimmed_identity_and_secure_password_hash(self):
        ws_a = FakeWs()
        auth, snapshot = create_room(ws_a, display_name="  Rahul  ")

        self.assertEqual(auth["participant"]["display_name"], "Rahul")
        self.assertEqual(snapshot["display_name"], "Rahul")
        room = connect_room_service._rooms[auth["code"]]
        self.assertTrue(room.accepts_password(ROOM_PASSWORD))
        self.assertFalse(room.accepts_password("wrong-password"))
        self.assertNotIn(ROOM_PASSWORD, room._password_hash)
        self.assertNotIn("password", snapshot)

    def test_device_a_creates_and_device_b_joins_with_code_password_and_name(self):
        ws_a = FakeWs()
        ws_b = FakeWs()

        auth_a, snapshot_a = create_room(ws_a)
        code = snapshot_a["code"]
        self.assertTrue(code.startswith("GF-"), code)
        self.assertEqual(snapshot_a["role"], "creator")
        self.assertEqual(snapshot_a["client_id"], "device-a-client-id")
        self.assertEqual(len(snapshot_a["participants"]), 1)

        auth_b = join_room(ws_b, code.lower())
        self.assertTrue(auth_b["success"], auth_b)
        snapshot_b = ws_b.messages("room_snapshot")[-1]
        self.assertEqual(snapshot_b["role"], "joiner")
        self.assertEqual(snapshot_b["client_id"], "device-b-client-id")
        self.assertEqual(snapshot_b["display_name"], "Amit")

        peers_a = ws_a.messages("peers")[-1]["participants"]
        peers_b = ws_b.messages("peers")[-1]["participants"]
        self.assertEqual({p["client_id"] for p in peers_a},
                         {"device-a-client-id", "device-b-client-id"})
        self.assertEqual({p["client_id"] for p in peers_b},
                         {"device-a-client-id", "device-b-client-id"})
        b_on_a = next(p for p in peers_a if p["client_id"] == "device-b-client-id")
        a_on_b = next(p for p in peers_b if p["client_id"] == "device-a-client-id")
        self.assertEqual(b_on_a["display_name"], "Amit")
        self.assertEqual(a_on_b["display_name"], "Rahul")
        self.assertTrue(b_on_a["connected"])
        self.assertTrue(a_on_b["connected"])

    def test_wrong_password_and_invalid_room_are_rejected(self):
        ws_a = FakeWs()
        _, snapshot = create_room(ws_a)

        wrong = connect_room_service.join_room(
            snapshot["code"], "Amit", "not-the-password", "device-b-client-id"
        )
        self.assertFalse(wrong["success"])
        self.assertEqual(wrong["error"], "authentication_failed")
        self.assertNotIn("not-the-password", json.dumps(wrong))

        invalid = connect_room_service.join_room(
            "GF-ZZZZ", "Amit", ROOM_PASSWORD, "device-b-client-id"
        )
        self.assertFalse(invalid["success"])
        self.assertEqual(invalid["error"], "invalid_room")

    def test_third_device_is_rejected_room_is_full(self):
        ws_a = FakeWs()
        ws_b = FakeWs()
        ws_c = FakeWs()
        _, snapshot = create_room(ws_a)
        self.assertTrue(join_room(ws_b, snapshot["code"])["success"])

        third = join_room(ws_c, snapshot["code"], client_id="device-c-client-id", display_name="Chirag")
        self.assertFalse(third["success"])
        self.assertEqual(third["error"], "room_full")
        self.assertEqual(len(ws_c.messages("room_snapshot")), 0)
        peers_a = ws_a.messages("peers")[-1]["participants"]
        self.assertEqual(len(peers_a), 2)

    def test_password_is_not_in_websocket_payloads_or_room_status(self):
        ws_a = FakeWs()
        ws_b = FakeWs()
        _, snapshot = create_room(ws_a)
        join_room(ws_b, snapshot["code"])

        all_wire_messages = json.dumps(ws_a.sent + ws_b.sent)
        self.assertNotIn(ROOM_PASSWORD, all_wire_messages)
        self.assertNotIn('"password"', all_wire_messages)
        self.assertNotIn('"password_hash"', all_wire_messages)

        joiner = connect_room_service._rooms[snapshot["code"]].participants["device-b-client-id"]
        status = connect_room_service.room_status(
            snapshot["code"], joiner.client_id, joiner._session_token_hash
        )
        # A digest is not a raw token, so use the API token from the participant
        # helper in the actual status check below instead of accepting internals.
        self.assertFalse(status["success"])
        self.assertNotIn(ROOM_PASSWORD, json.dumps(status))

    def test_status_api_shape_omits_password_material(self):
        ws_a = FakeWs()
        auth, snapshot = create_room(ws_a)
        status = connect_room_service.room_status(
            snapshot["code"], auth["participant"]["client_id"], auth["session_token"]
        )
        self.assertTrue(status["success"], status)
        self.assertNotIn(ROOM_PASSWORD, json.dumps(status))
        self.assertNotIn("password_hash", json.dumps(status))
        self.assertNotIn("session_token", json.dumps(status))

    def test_text_messages_relay_in_both_directions(self):
        ws_a = FakeWs()
        ws_b = FakeWs()
        _, snapshot = create_room(ws_a)
        self.assertTrue(join_room(ws_b, snapshot["code"])["success"])

        relay(ws_a, {"type": "message", "text": "Hello from A"})
        relay(ws_b, {"type": "message", "text": "Hello from B"})

        text_on_b = [m for m in ws_b.messages("text") if m["from"] == "device-a-client-id"]
        text_on_a = [m for m in ws_a.messages("text") if m["from"] == "device-b-client-id"]
        self.assertEqual(text_on_b[-1]["text"], "Hello from A")
        self.assertEqual(text_on_a[-1]["text"], "Hello from B")
        self.assertEqual(text_on_b[-1]["sender_name"], "Rahul")
        self.assertEqual(text_on_a[-1]["sender_name"], "Amit")

    def test_gesture_relay_works_from_either_seat(self):
        ws_a = FakeWs()
        ws_b = FakeWs()
        _, snapshot = create_room(ws_a)
        self.assertTrue(join_room(ws_b, snapshot["code"])["success"])

        # Seat handover remains a compatibility path for old clients.
        relay(ws_b, {"type": "gesture_cam", "enabled": True})
        relay(ws_a, {"type": "gesture_cam", "enabled": False})
        peers_b = ws_b.messages("peers")[-1]["participants"]
        seat_owner_b = next(p for p in peers_b if p["seat"])
        self.assertEqual(seat_owner_b["client_id"], "device-b-client-id")

        with connect_room_service._lock:
            connect_room_service._push_gesture_message({
                "gesture_id": "HII", "meaning": "Hii", "symbol": "☝️",
                "kind": "pose", "confidence": 0.97, "has_replay": False,
            })
        gestures_on_a = ws_a.messages("gesture")
        self.assertTrue(gestures_on_a)
        self.assertEqual(gestures_on_a[-1]["from"], "device-b-client-id")
        self.assertEqual(gestures_on_a[-1]["sender_name"], "Amit")

        relay(ws_a, {"type": "gesture_cam", "enabled": True})
        relay(ws_b, {"type": "gesture_cam", "enabled": False})
        with connect_room_service._lock:
            connect_room_service._push_gesture_message({
                "gesture_id": "OK", "meaning": "Okay", "symbol": "👍",
                "kind": "pose", "confidence": 0.91, "has_replay": False,
            })
        gestures_on_b = ws_b.messages("gesture")
        self.assertTrue(gestures_on_b)
        self.assertEqual(gestures_on_b[-1]["from"], "device-a-client-id")
        self.assertEqual(gestures_on_b[-1]["sender_name"], "Rahul")

    def test_duplicate_suppression_is_preserved(self):
        ws_a = FakeWs()
        ws_b = FakeWs()
        _, snapshot = create_room(ws_a)
        self.assertTrue(join_room(ws_b, snapshot["code"])["success"])
        event = {
            "type": "gesture", "id": "same-event", "gesture_id": "OK",
            "meaning": "Okay", "symbol": "👍", "kind": "pose",
            "confidence": 0.9,
        }
        relay(ws_a, event)
        relay(ws_a, event)
        received = [message for message in ws_b.messages("gesture") if message["id"] == "same-event"]
        self.assertEqual(len(received), 1)

    def test_disconnect_reconnect_restores_same_identity_and_seat(self):
        ws_a = FakeWs()
        ws_b = FakeWs()
        auth_a, snapshot = create_room(ws_a)
        auth_b = join_room(ws_b, snapshot["code"])

        connect_room_service.disconnect(ws_b)
        peers_a = ws_a.messages("peers")[-1]["participants"]
        b_on_a = next(p for p in peers_a if p["client_id"] == "device-b-client-id")
        self.assertFalse(b_on_a["connected"])

        ws_b_reconnected = FakeWs()
        attach(ws_b_reconnected, auth_b, "resume")
        resumed = ws_b_reconnected.messages("room_snapshot")[-1]
        self.assertEqual(resumed["role"], "joiner")
        self.assertEqual(resumed["display_name"], "Amit")
        self.assertEqual(len(resumed["participants"]), 2)
        self.assertEqual(
            [p["user_number"] for p in resumed["participants"]], [1, 2]
        )
        self.assertEqual(auth_a["participant"]["user_number"], 1)
        self.assertEqual(auth_b["participant"]["user_number"], 2)

    def test_leave_then_rejoin_reuses_the_open_seat(self):
        ws_a = FakeWs()
        ws_b = FakeWs()
        _, snapshot = create_room(ws_a)
        join_auth = join_room(ws_b, snapshot["code"])
        relay(ws_b, {"type": "leave"})

        ws_b_again = FakeWs()
        rejoin_auth = join_room(ws_b_again, snapshot["code"], password=ROOM_PASSWORD)
        self.assertTrue(rejoin_auth["success"], rejoin_auth)
        self.assertEqual(rejoin_auth["participant"]["user_number"], 2)
        self.assertEqual(rejoin_auth["participant"]["display_name"], "Amit")
        self.assertEqual(len(connect_room_service._rooms[snapshot["code"]].participants), 2)
        self.assertNotEqual(join_auth["session_token"], rejoin_auth["session_token"])

    def test_password_in_websocket_is_rejected_instead_of_being_relayed(self):
        ws = FakeWs()
        relay(ws, {
            "type": "create", "code": "GF-AAAA", "client_id": "device-a-client-id",
            "password": ROOM_PASSWORD,
        })
        self.assertEqual(ws.messages("room_snapshot"), [])
        self.assertEqual(ws.messages("error")[-1]["error"], "bad_request")
        self.assertNotIn(ROOM_PASSWORD, json.dumps(ws.sent))


if __name__ == "__main__":
    unittest.main()
