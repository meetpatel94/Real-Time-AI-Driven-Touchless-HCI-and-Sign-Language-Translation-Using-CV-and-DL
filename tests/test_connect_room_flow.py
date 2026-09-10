"""Regression tests for the password-protected two-person Connect relay.

The tests exercise the transport-agnostic service the same way the Flask-Sock
route does: room authentication happens through the room API operation, then
only an opaque session token and public identity are sent on the WebSocket.
"""

import json
import unittest

from services.connect_room_service import MAX_GESTURE_HISTORY, connect_room_service


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


def room_for(code):
    with connect_room_service._lock:
        return connect_room_service._rooms.get(code)


def received_gestures(code, client_id):
    """Server-side per-participant remote gesture history.

    This is exactly what the OTHER USER -> GESTURE HISTORY card renders for
    ``client_id`` (the gestures it received from the other participant).
    """
    with connect_room_service._lock:
        room = connect_room_service._rooms.get(code)
        assert room is not None and client_id in room.participants, \
            f"no participant {client_id} in room {code}"
        return [dict(item) for item in room.participants[client_id].received_gestures]


def peers_last_gesture(device, client_id):
    """The peer summary's ``last_gesture`` field as seen by ``device``."""
    messages = device.messages("peers")
    assert messages, "no peers summary delivered"
    for participant in messages[-1]["participants"]:
        if participant["client_id"] == client_id:
            return participant["last_gesture"]
    return None


class ConnectGestureHistoryTest(unittest.TestCase):
    """Per-participant gesture history for the two-person relay.

    LAST GESTURE stays the single most recent received gesture (peer summary
    ``last_gesture``); GESTURE HISTORY is the bounded, chronological,
    duplicate-free list of every gesture received from the other participant.
    """

    def setUp(self):
        reset_service()

    def tearDown(self):
        reset_service()

    def _two_devices(self):
        ws_a = FakeWs()
        ws_b = FakeWs()
        _, snapshot = create_room(ws_a)
        self.assertTrue(join_room(ws_b, snapshot["code"])["success"])
        return ws_a, ws_b, snapshot["code"]

    def _gesture(self, event_id, gesture_id, meaning, **extra):
        payload = {
            "type": "gesture",
            "id": event_id,
            "gesture_id": gesture_id,
            "meaning": meaning,
            "symbol": extra.pop("symbol", "✋"),
            "kind": extra.pop("kind", "pose"),
            "confidence": extra.pop("confidence", 0.95),
        }
        payload.update(extra)
        return payload

    # 1. User 1 sends gesture A -> User 2 receives A.
    def test_first_gesture_lands_in_remote_history_and_last_gesture(self):
        ws_a, ws_b, code = self._two_devices()
        relay(ws_a, self._gesture("ev-a1", "five", "Hii", symbol="🖐️"))

        received = [m for m in ws_b.messages("gesture") if m["from"] == "device-a-client-id"]
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["meaning"], "Hii")

        history = received_gestures(code, "device-b-client-id")
        self.assertEqual([g["meaning"] for g in history], ["Hii"])
        # The creator has not received anything yet.
        self.assertEqual(received_gestures(code, "device-a-client-id"), [])
        self.assertEqual(peers_last_gesture(ws_b, "device-a-client-id")["meaning"], "Hii")

    # 2. User 1 sends gesture B -> User 2 still has A in history, B is Last.
    def test_second_gesture_keeps_first_in_history_and_becomes_last(self):
        ws_a, ws_b, code = self._two_devices()
        relay(ws_a, self._gesture("ev-a1", "one", "Hii", symbol="☝️"))
        relay(ws_a, self._gesture("ev-a2", "two", "bee", symbol="✌️"))

        history = received_gestures(code, "device-b-client-id")
        self.assertEqual([g["meaning"] for g in history], ["Hii", "bee"])
        self.assertEqual(peers_last_gesture(ws_b, "device-a-client-id")["meaning"], "bee")

    # 3. User 1 sends A, B, C -> history holds A, B, C; Last Gesture is C.
    def test_history_accumulates_in_chronological_order(self):
        ws_a, ws_b, code = self._two_devices()
        relay(ws_a, self._gesture("ev-a1", "one", "A", symbol="☝️"))
        relay(ws_a, self._gesture("ev-a2", "two", "B", symbol="✌️"))
        relay(ws_a, self._gesture("ev-a3", "three", "C", symbol="🤟"))

        history = received_gestures(code, "device-b-client-id")
        self.assertEqual([g["meaning"] for g in history], ["A", "B", "C"])
        self.assertEqual([g["id"] for g in history], ["ev-a1", "ev-a2", "ev-a3"])
        self.assertLessEqual(history[0]["ts"], history[1]["ts"])
        self.assertLessEqual(history[1]["ts"], history[2]["ts"])
        self.assertEqual(peers_last_gesture(ws_b, "device-a-client-id")["meaning"], "C")

    # 4. User 2 sends gesture X -> User 1 receives X in their own history.
    def test_user2_gesture_reaches_user1_history(self):
        ws_a, ws_b, code = self._two_devices()
        relay(ws_b, self._gesture("ev-b1", "thumbs_up", "Thank you", symbol="👍"))

        received = [m for m in ws_a.messages("gesture") if m["from"] == "device-b-client-id"]
        self.assertEqual(len(received), 1)
        self.assertEqual([g["meaning"] for g in received_gestures(code, "device-a-client-id")],
                         ["Thank you"])
        self.assertEqual(peers_last_gesture(ws_a, "device-b-client-id")["meaning"], "Thank you")

    # 5. The two per-participant histories never mix each other's gestures.
    def test_histories_remain_separate_per_participant(self):
        ws_a, ws_b, code = self._two_devices()
        relay(ws_a, self._gesture("ev-a1", "one", "Hii"))
        relay(ws_a, self._gesture("ev-a2", "two", "bee"))
        relay(ws_b, self._gesture("ev-b1", "five", "Hello"))

        a_history = received_gestures(code, "device-a-client-id")
        b_history = received_gestures(code, "device-b-client-id")
        self.assertEqual([g["meaning"] for g in a_history], ["Hello"])
        self.assertEqual([g["meaning"] for g in b_history], ["Hii", "bee"])
        for gesture in a_history:
            self.assertEqual(gesture["from"], "device-b-client-id")
        for gesture in b_history:
            self.assertEqual(gesture["from"], "device-a-client-id")

    # 6. The shared session timeline still carries events from both users,
    #    and text messages never enter a gesture history.
    def test_timeline_keeps_both_users_and_text_stays_out_of_gesture_history(self):
        ws_a, ws_b, code = self._two_devices()
        relay(ws_a, self._gesture("ev-a1", "five", "Hii"))
        relay(ws_b, self._gesture("ev-b1", "two", "bee"))
        relay(ws_a, {"type": "message", "id": "tx-1", "text": "Nice one"})

        with connect_room_service._lock:
            timeline = list(room_for(code).history)
        self.assertEqual([event["type"] for event in timeline], ["gesture", "gesture", "text"])
        self.assertEqual({event["from"] for event in timeline[:2]},
                         {"device-a-client-id", "device-b-client-id"})
        self.assertEqual(timeline[2]["text"], "Nice one")
        self.assertEqual([g["meaning"] for g in received_gestures(code, "device-a-client-id")],
                         ["bee"])
        self.assertEqual([g["meaning"] for g in received_gestures(code, "device-b-client-id")],
                         ["Hii"])

    # 7. An accidentally duplicated WebSocket event creates one entry only.
    def test_duplicate_gesture_event_creates_single_history_entry(self):
        ws_a, ws_b, code = self._two_devices()
        event = self._gesture("same-event", "thumbs_up", "Okay", symbol="👍")
        relay(ws_a, event)
        relay(ws_a, dict(event))

        received = [m for m in ws_b.messages("gesture") if m["id"] == "same-event"]
        self.assertEqual(len(received), 1)
        self.assertEqual(len(received_gestures(code, "device-b-client-id")), 1)

    # 8. The per-participant history is bounded by MAX_HISTORY (oldest drop).
    def test_history_respects_max_history_limit(self):
        self.assertGreaterEqual(MAX_GESTURE_HISTORY, 10)
        ws_a, ws_b, code = self._two_devices()
        total = MAX_GESTURE_HISTORY + 5
        for index in range(1, total + 1):
            relay(ws_a, self._gesture(f"ev-{index:04d}", f"g{index}", f"Gesture {index}"))

        history = received_gestures(code, "device-b-client-id")
        self.assertEqual(len(history), MAX_GESTURE_HISTORY)
        self.assertEqual(history[0]["id"], f"ev-{total - MAX_GESTURE_HISTORY + 1:04d}")
        self.assertEqual(history[-1]["id"], f"ev-{total:04d}")
        dropped = {f"ev-{index:04d}" for index in range(1, total - MAX_GESTURE_HISTORY + 1)}
        self.assertFalse(dropped & {g["id"] for g in history})

    # 9. History items keep everything the existing replay mechanism needs.
    def test_history_items_preserve_replay_and_identity_fields(self):
        ws_a, ws_b, code = self._two_devices()
        relay(ws_a, self._gesture(
            "ev-custom-1", "cg-thankyou", "Thank you very much",
            symbol="✋", kind="custom", confidence=0.93, has_replay=True,
        ))

        (item,) = received_gestures(code, "device-b-client-id")
        # Existing replay path: openReplay(gesture_id) -> GET /api/connect/replay/<gesture_id>.
        self.assertEqual(item["gesture_id"], "cg-thankyou")
        self.assertEqual(item["kind"], "custom")
        self.assertTrue(item["has_replay"])
        # Session/participant identity for the history row.
        self.assertEqual(item["meaning"], "Thank you very much")
        self.assertEqual(item["confidence"], 0.93)
        self.assertEqual(item["from"], "device-a-client-id")
        self.assertEqual(item["sender_name"], "Rahul")
        self.assertEqual(item["sender_user"], "User 1")
        self.assertEqual(item["sender_role"], "creator")
        self.assertEqual(item["symbol"], "✋")
        self.assertEqual(item["type"], "gesture")
        self.assertGreater(item["ts"], 0)

    # 10a. A network drop + resume keeps the same-session history.
    def test_resume_preserves_remote_history_for_same_session(self):
        ws_a = FakeWs()
        ws_b = FakeWs()
        _, snapshot = create_room(ws_a)
        auth_b = join_room(ws_b, snapshot["code"])
        self.assertTrue(auth_b["success"])
        code = snapshot["code"]

        relay(ws_a, self._gesture("ev-a1", "five", "Hii"))
        relay(ws_b, self._gesture("ev-b1", "two", "bee"))

        connect_room_service.disconnect(ws_b)  # drop — the seat (and history) stays
        ws_b_again = FakeWs()
        attach(ws_b_again, auth_b, "resume")
        resumed = ws_b_again.messages("room_snapshot")[-1]

        self.assertEqual([g["meaning"] for g in resumed["gesture_history"]], ["Hii"])
        self.assertEqual([g["meaning"] for g in received_gestures(code, "device-a-client-id")],
                         ["bee"])
        # The shared timeline survived the drop as well.
        self.assertEqual(len(resumed["history"]), 2)

    # 10b. A fresh joiner starts with an empty remote history even if the
    #      creator gestured while alone.
    def test_join_starts_with_empty_remote_history(self):
        ws_a = FakeWs()
        ws_b = FakeWs()
        _, snapshot = create_room(ws_a)
        code = snapshot["code"]
        relay(ws_a, self._gesture("ev-solo", "five", "Hello"))

        self.assertTrue(join_room(ws_b, code)["success"])
        joined = ws_b.messages("room_snapshot")[-1]
        self.assertEqual(joined["gesture_history"], [])

        relay(ws_a, self._gesture("ev-a2", "two", "bee"))
        self.assertEqual([g["meaning"] for g in received_gestures(code, "device-b-client-id")],
                         ["bee"])

    # 10c. Leave/rejoin resets per-occupant views and new rooms start clean.
    def test_leave_and_rejoin_does_not_leak_previous_occupant_history(self):
        ws_a = FakeWs()
        ws_b = FakeWs()
        _, snapshot = create_room(ws_a)
        code = snapshot["code"]
        self.assertTrue(join_room(ws_b, code)["success"])

        relay(ws_a, self._gesture("ev-a1", "five", "Hii"))  # joiner's view
        relay(ws_b, self._gesture("ev-b1", "two", "bee"))   # creator's view

        relay(ws_b, {"type": "leave"})
        # The creator's "Other User" view of the now-empty seat resets.
        self.assertEqual(received_gestures(code, "device-a-client-id"), [])

        ws_b_again = FakeWs()
        self.assertTrue(join_room(ws_b_again, code)["success"])
        rejoined = ws_b_again.messages("room_snapshot")[-1]
        self.assertEqual(rejoined["gesture_history"], [])
        self.assertEqual(received_gestures(code, "device-b-client-id"), [])

        # A brand-new room never inherits the old room's traffic.
        ws_c = FakeWs()
        ws_d = FakeWs()
        _, snapshot2 = create_room(ws_c, client_id="device-c-client-id")
        self.assertTrue(join_room(ws_d, snapshot2["code"], client_id="device-d-client-id")["success"])
        relay(ws_c, self._gesture("ev-newroom", "three", "I Love You"))
        d_history = received_gestures(snapshot2["code"], "device-d-client-id")
        self.assertEqual([g["meaning"] for g in d_history], ["I Love You"])
        self.assertNotIn("Hii", [g["meaning"] for g in d_history])
        self.assertNotIn("bee", [g["meaning"] for g in d_history])


if __name__ == "__main__":
    unittest.main()
