"""Unit tests for the shared MediaPipe-landmark hand scroll controller."""

import unittest
from types import SimpleNamespace

from core.mouse.scroll_controller import ScrollController


class FakeRightHand:
    """Small MediaPipe-compatible landmark fixture; pose/finger data is irrelevant."""

    def __init__(self, palm_x, palm_y):
        self.landmark = [
            SimpleNamespace(x=palm_x, y=palm_y, z=0.0)
            for _ in range(21)
        ]


class GlobalHandScrollControllerTests(unittest.TestCase):
    def make_controller(self):
        events = []
        controller = ScrollController(
            event_publisher=lambda direction, amount: events.append({
                "direction": direction,
                "amount": amount,
            })
        )
        return controller, events

    def test_upward_right_palm_motion_publishes_an_up_event(self):
        controller, events = self.make_controller()

        self.assertFalse(controller.process_hand(FakeRightHand(0.50, 0.50), timestamp=0.00))
        self.assertFalse(controller.process_hand(FakeRightHand(0.50, 0.47), timestamp=0.08))
        self.assertTrue(controller.process_hand(FakeRightHand(0.50, 0.43), timestamp=0.16))

        self.assertEqual(events[0]["direction"], "up")
        self.assertGreaterEqual(events[0]["amount"], controller.scroll_amount)
        self.assertLessEqual(events[0]["amount"], controller.max_amount)

    def test_downward_right_palm_motion_publishes_a_down_event(self):
        controller, events = self.make_controller()

        controller.process_hand(FakeRightHand(0.50, 0.40), timestamp=0.00)
        controller.process_hand(FakeRightHand(0.50, 0.44), timestamp=0.08)
        self.assertTrue(controller.process_hand(FakeRightHand(0.50, 0.49), timestamp=0.16))

        self.assertEqual(events, [{"direction": "down", "amount": events[0]["amount"]}])

    def test_stationary_jitter_and_horizontal_motion_do_not_scroll(self):
        controller, events = self.make_controller()
        for timestamp, y in ((0.00, 0.50), (0.08, 0.505), (0.16, 0.497), (0.24, 0.503)):
            controller.process_hand(FakeRightHand(0.50, y), timestamp=timestamp)
        self.assertEqual(events, [])

        controller.reset()
        for timestamp, x, y in ((1.00, 0.50, 0.50), (1.08, 0.58, 0.47), (1.16, 0.66, 0.43)):
            controller.process_hand(FakeRightHand(x, y), timestamp=timestamp)
        self.assertEqual(events, [])

    def test_cooldown_and_reset_prevent_uncontrolled_repeats(self):
        controller, events = self.make_controller()
        for timestamp, y in ((0.00, 0.50), (0.08, 0.47), (0.16, 0.43)):
            controller.process_hand(FakeRightHand(0.50, y), timestamp=timestamp)
        self.assertEqual(len(events), 1)

        # A second deliberate travel inside the 420ms cooldown must be ignored.
        for timestamp, y in ((0.24, 0.40), (0.32, 0.36), (0.40, 0.31)):
            controller.process_hand(FakeRightHand(0.50, y), timestamp=timestamp)
        self.assertEqual(len(events), 1)

        # Once the cooldown expires, a new movement can produce exactly one more
        # event.  Holding the final hand position does not create another one.
        for timestamp, y in ((0.64, 0.27), (0.72, 0.23), (0.80, 0.19), (0.88, 0.19), (0.96, 0.19)):
            controller.process_hand(FakeRightHand(0.50, y), timestamp=timestamp)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[1]["direction"], "up")

    def test_controller_uses_palm_motion_only_not_finger_counts_or_labels(self):
        controller, events = self.make_controller()

        # The fixture has no meaningful finger geometry.  The controller needs
        # only landmarks 0 and 9, so its behaviour cannot depend on a custom
        # gesture name such as "hi" or a finger count.
        for timestamp, y in ((0.00, 0.40), (0.08, 0.44), (0.16, 0.49)):
            controller.process_hand(FakeRightHand(0.50, y), timestamp=timestamp)

        self.assertEqual(events[0]["direction"], "down")


if __name__ == "__main__":
    unittest.main()
