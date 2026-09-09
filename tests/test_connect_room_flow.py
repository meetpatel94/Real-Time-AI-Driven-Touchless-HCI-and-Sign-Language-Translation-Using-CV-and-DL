"""Tests for the Connect two-person room relay (two-device LAN flow).

Validates the exact FINAL TEST scenario of the two-device Connect flow:

1. Device A creates a room and receives a code (e.g. GF-XXXX).
2. Device B joins the same room with that code.
3. Both sides report the other participant as connected.
4. A gesture relayed by A is received by B, and vice versa (seat handover).
5. Text messages relay in both directions.
6. A third device cannot join a full room; unknown codes are rejected.
7. Disconnecting one side updates the peer status on the other side.

The tests drive the transport-agnostic room service with fake WebSocket
objects, mirroring exactly what ``routes/connect_socket.py`` does in
production (raw JSON messages over the WebSocket).
"""

import json
import unittest

from services.connect_room_service import connect_room_service


class FakeWs:
    """Minimal stand-in for a Flask-Sock WebSocket object."""

    def __init__(self):
        self.sent = []

    def send(self, raw):
        self.sent.append(json.loads(raw))

    def messages(self, message_type):
        return [m for m in self.sent if m.get("type") == message_type]


def reset_service():
    """Clear rooms/seat state between tests (ephemeral in-memory relay)."""
    with connect_room_service._lock:
        for room in list(connect_room_service._rooms.values()):
            for participant in room.participants.values():
                participant.detach()
        connect_room_service._rooms.clear()
        connect_room_service._ws_index.clear()
        connect_room_service._seat_owner = None


def relay(device, payload):
    """Send a client message the way the WebSocket route does."""
    raw = json.dumps(payload)
    device.send(raw)
    connect_room_service.handle_message(device, raw)


def create_room(device, client_id):
    relay(device, {
        "type": "create",
        "client_id": client_id,
        "display_name": "",
    })
    snapshots = device.messages("room_snapshot")
    assert snapshots, "creator did not receive a room snapshot"
    return snapshots[-1]


def join_room(device, client_id, code):
    relay(device, {
        "type": "join",
        "code": code,
        "client_id": client_id,
        "display_name": "",
    })


class ConnectRoomFlowTest(unittest.TestCase):

    def setUp(self):
        reset_service()

    def tearDown(self):
        reset_service()

    def test_device_a_creates_and_device_b_joins_with_code(self):
        ws_a = FakeWs()
        ws_b = FakeWs()

        snapshot_a = create_room(ws_a, "device-a-client-id")
        code = snapshot_a["code"]
        self.assertTrue(code.startswith("GF-"), code)
        self.assertEqual(snapshot_a["role"], "creator")
        self.assertEqual(snapshot_a["client_id"], "device-a-client-id")
        self.assertEqual(len(snapshot_a["participants"]), 1)

        # Device B joins with the exact code shown to A (case-insensitive).
        join_room(ws_b, "device-b-client-id", code.lower())
        snapshot_b = ws_b.messages("room_snapshot")[-1]
        self.assertEqual(snapshot_b["role"], "joiner")
        self.assertEqual(snapshot_b["client_id"], "device-b-client-id")

        # Both devices see each other as connected participants.
        peers_a = ws_a.messages("peers")[-1]["participants"]
        peers_b = ws_b.messages("peers")[-1]["participants"]
        self.assertEqual({p["client_id"] for p in peers_a},
                         {"device-a-client-id", "device-b-client-id"})
        self.assertEqual({p["client_id"] for p in peers_b},
                         {"device-a-client-id", "device-b-client-id"})
        b_on_a = next(p for p in peers_a if p["client_id"] == "device-b-client-id")
        a_on_b = next(p for p in peers_b if p["client_id"] == "device-a-client-id")
        self.assertTrue(b_on_a["connected"])
        self.assertTrue(a_on_b["connected"])

    def test_text_messages_relay_in_both_directions(self):
        ws_a = FakeWs()
        ws_b = FakeWs()
        snapshot = create_room(ws_a, "device-a-client-id")
        join_room(ws_b, "device-b-client-id", snapshot["code"])

        relay(ws_a, {"type": "message", "text": "Hello from A"})
        relay(ws_b, {"type": "message", "text": "Hello from B"})

        # The relay echoes every event to both seats (the sender renders its
        # own copy on the timeline), so filter by the peer's client id.
        text_on_b = [m for m in ws_b.messages("text") if m["from"] == "device-a-client-id"]
        text_on_a = [m for m in ws_a.messages("text") if m["from"] == "device-b-client-id"]
        self.assertEqual(text_on_b[-1]["text"], "Hello from A")
        self.assertEqual(text_on_a[-1]["text"], "Hello from B")

    def test_gesture_relay_works_from_either_seat(self):
        ws_a = FakeWs()
        ws_b = FakeWs()
        snapshot = create_room(ws_a, "device-a-client-id")
        join_room(ws_b, "device-b-client-id", snapshot["code"])

        # Seat handover: B requests the camera feed, A releases it.
        relay(ws_b, {"type": "gesture_cam", "enabled": True})
        relay(ws_a, {"type": "gesture_cam", "enabled": False})
        peers_b = ws_b.messages("peers")[-1]["participants"]
        seat_owner_b = next(p for p in peers_b if p["seat"])
        self.assertEqual(seat_owner_b["client_id"], "device-b-client-id")

        # Simulate a detector event while B holds the seat -> A receives it.
        gesture = {
            "type": "gesture",
            "gesture_id": "HII",
            "meaning": "Hii",
            "symbol": "☝️",
            "kind": "pose",
            "confidence": 0.97,
            "has_replay": False,
        }
        with connect_room_service._lock:
            connect_room_service._push_gesture_message(
                {"gesture_id": gesture["gesture_id"], "meaning": gesture["meaning"],
                 "symbol": gesture["symbol"], "kind": gesture["kind"],
                 "confidence": gesture["confidence"], "has_replay": gesture["has_replay"]})

        gestures_on_a = ws_a.messages("gesture")
        self.assertTrue(gestures_on_a, "A did not receive B's gesture")
        self.assertEqual(gestures_on_a[-1]["from"], "device-b-client-id")
        self.assertEqual(gestures_on_a[-1]["meaning"], "Hii")

        # Hand the seat back to A and relay a gesture from A -> B receives it.
        relay(ws_a, {"type": "gesture_cam", "enabled": True})
        relay(ws_b, {"type": "gesture_cam", "enabled": False})
        with connect_room_service._lock:
            connect_room_service._push_gesture_message(
                {"gesture_id": "OK", "meaning": "Okay", "symbol": "👍",
                 "kind": "pose", "confidence": 0.91, "has_replay": False})
        gestures_on_b = ws_b.messages("gesture")
        self.assertTrue(gestures_on_b, "B did not receive A's gesture")
        self.assertEqual(gestures_on_b[-1]["from"], "device-a-client-id")
        self.assertEqual(gestures_on_b[-1]["meaning"], "Okay")

    def test_third_device_is_rejected_room_is_full(self):
        ws_a = FakeWs()
        ws_b = FakeWs()
        ws_c = FakeWs()
        snapshot = create_room(ws_a, "device-a-client-id")
        join_room(ws_b, "device-b-client-id", snapshot["code"])
        join_room(ws_c, "device-c-client-id", snapshot["code"])

        errors = ws_c.messages("error")
        self.assertTrue(errors)
        self.assertEqual(errors[-1]["error"], "room_full")
        # No third snapshot is delivered and the room still seats two.
        self.assertEqual(len(ws_c.messages("room_snapshot")), 0)
        peers_a = ws_a.messages("peers")[-1]["participants"]
        self.assertEqual(len(peers_a), 2)

    def test_unknown_room_code_is_rejected(self):
        ws_a = FakeWs()
        create_room(ws_a, "device-a-client-id")
        ws_b = FakeWs()
        join_room(ws_b, "device-b-client-id", "GF-ZZZZ")
        errors = ws_b.messages("error")
        self.assertTrue(errors)
        self.assertEqual(errors[-1]["error"], "invalid_room")

    def test_disconnect_updates_peer_status(self):
        ws_a = FakeWs()
        ws_b = FakeWs()
        snapshot = create_room(ws_a, "device-a-client-id")
        join_room(ws_b, "device-b-client-id", snapshot["code"])

        connect_room_service.disconnect(ws_b)
        peers_a = ws_a.messages("peers")[-1]["participants"]
        b_on_a = next(p for p in peers_a if p["client_id"] == "device-b-client-id")
        self.assertFalse(b_on_a["connected"])


if __name__ == "__main__":
    unittest.main()
