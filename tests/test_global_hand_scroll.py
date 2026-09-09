"""Unit tests for the shared MediaPipe-landmark global hand scroll controller.

The gesture contract under test:

    RIGHT HAND + STABLE OPEN PALM + CLEAR DOMINANT VERTICAL MOVEMENT  -> scroll
    anything else (sideways, diagonal, tiny, still, fist, one finger) -> no scroll

Two kinds of fixtures are used:

* ``build_hand`` composes anatomically plausible 21-landmark hands so motion
  sequences can be simulated frame by frame.
* ``OPEN_PALM_RAISED`` / ``OPEN_PALM_SPREAD`` / ``CLOSED_FIST`` are REAL
  MediaPipe hand-tracking outputs captured from photographs.  They lock the
  pose thresholds to genuine landmark geometry instead of synthetic guesses.
"""

import math
import unittest
from types import SimpleNamespace

from config import Config
from core.mouse.scroll_controller import ScrollController


# ---------------------------------------------------------------------------
# Real MediaPipe landmark captures (x, y only; z is unused by the controller)
# ---------------------------------------------------------------------------
OPEN_PALM_RAISED = [
    (0.4987, 0.8090), (0.5662, 0.7387), (0.6071, 0.6183),
    (0.6318, 0.5079), (0.6604, 0.4266), (0.5435, 0.4451),
    (0.5533, 0.3077), (0.5574, 0.2212), (0.5583, 0.1413),
    (0.4985, 0.4391), (0.4996, 0.2890), (0.4995, 0.1898),
    (0.4976, 0.1019), (0.4581, 0.4655), (0.4516, 0.3250),
    (0.4519, 0.2345), (0.4522, 0.1524), (0.4194, 0.5171),
    (0.4041, 0.4056), (0.3962, 0.3327), (0.3905, 0.2601),
]

OPEN_PALM_SPREAD = [
    (0.5571, 0.5757), (0.5274, 0.4347), (0.4652, 0.3337),
    (0.4081, 0.2489), (0.3767, 0.1592), (0.3543, 0.4361),
    (0.2711, 0.3981), (0.2169, 0.3794), (0.1695, 0.3705),
    (0.3437, 0.5330), (0.2512, 0.5273), (0.1899, 0.5270),
    (0.1395, 0.5324), (0.3558, 0.6226), (0.2680, 0.6416),
    (0.2123, 0.6546), (0.1653, 0.6644), (0.3860, 0.7019),
    (0.3274, 0.7665), (0.2879, 0.8091), (0.2498, 0.8409),
]

CLOSED_FIST = [
    (0.4862, 0.6285), (0.3930, 0.5954), (0.3393, 0.5146),
    (0.3430, 0.4242), (0.3818, 0.4071), (0.4321, 0.4071),
    (0.4091, 0.2830), (0.3939, 0.3213), (0.4019, 0.3661),
    (0.4915, 0.3933), (0.4590, 0.2662), (0.4380, 0.3070),
    (0.4471, 0.3481), (0.5337, 0.3959), (0.5082, 0.2720),
    (0.4840, 0.3018), (0.4876, 0.3401), (0.5650, 0.4133),
    (0.5558, 0.3067), (0.5306, 0.3132), (0.5248, 0.3439),
]


class RealHand:
    """A hand track built from captured MediaPipe landmarks."""

    def __init__(self, points, dx=0.0, dy=0.0):
        self.landmark = [
            SimpleNamespace(x=x + dx, y=y + dy, z=0.0) for x, y in points
        ]


# ---------------------------------------------------------------------------
# Synthetic MediaPipe hand fixture
# ---------------------------------------------------------------------------
# Local coordinates: palm length ~= 1.0, fingers point towards -y (image up).
THUMB_EXTENDED = ((0.40, 0.30), (0.62, 0.12), (0.74, -0.02), (0.82, -0.16))
THUMB_TUCKED = ((0.30, 0.20), (0.45, -0.05), (0.30, -0.35), (0.05, -0.20))
WRIST = (0.0, 0.50)

# (mcp, pip, dip, tip, fan angle in degrees, finger length).  Lengths match real
# MediaPipe proportions: a finger is ~0.65-0.95 of the wrist->middle-MCP span.
FINGERS = (
    (5, 6, 7, 8, -6.0, 0.81),
    (9, 10, 11, 12, 0.0, 0.88),
    (13, 14, 15, 16, 6.0, 0.78),
    (17, 18, 19, 20, 14.0, 0.65),
)
MCP_POSITIONS = {
    5: (-0.28, -0.42),
    9: (0.00, -0.45),
    13: (0.26, -0.42),
    17: (0.50, -0.32),
}
# Landmarks 0/5/9/17 define the palm centre used for motion tracking.
_PALM_OFFSET = (
    sum(WRIST[0] if index == 0 else MCP_POSITIONS[index][0] for index in (0, 5, 9, 17)) / 4.0,
    sum(WRIST[1] if index == 0 else MCP_POSITIONS[index][1] for index in (0, 5, 9, 17)) / 4.0,
)


def build_hand(
    x,
    y,
    scale=0.18,
    fingers=(True, True, True, True),
    thumb=True,
    tip_spread=0.0,
    fan=1.0,
):
    """Return a 21-landmark MediaPipe-style hand whose palm centre is (x, y).

    ``fingers`` selects which of index/middle/ring/pinky are extended.
    ``tip_spread`` pushes the fingertips apart (simulating a splayed hand) and
    ``fan`` multiplies the natural finger fan angle.
    """
    points = [None] * 21
    points[0] = WRIST
    thumb_chain = THUMB_EXTENDED if thumb else THUMB_TUCKED
    points[1], points[2], points[3], points[4] = thumb_chain

    tip_offsets = (-1.5, -0.5, 0.5, 1.5)
    for slot, (mcp_index, pip_index, dip_index, tip_index, angle, length) in enumerate(FINGERS):
        mcp = MCP_POSITIONS[mcp_index]
        radians = math.radians(angle * fan)
        direction = (math.sin(radians), -math.cos(radians))
        extended = bool(fingers[slot])

        pip = (mcp[0] + 0.42 * length * direction[0], mcp[1] + 0.42 * length * direction[1])
        if extended:
            dip = (mcp[0] + 0.72 * length * direction[0], mcp[1] + 0.72 * length * direction[1])
            tip = (mcp[0] + length * direction[0], mcp[1] + length * direction[1])
            tip = (tip[0] + tip_offsets[slot] * tip_spread, tip[1])
        else:
            # Curled back towards the palm: the tip sits near the MCP joint.
            dip = (mcp[0] + 0.12 * length * direction[0], mcp[1] + 0.12 * length * direction[1])
            tip = (mcp[0] + 0.15 * length * direction[0], mcp[1] + 0.15 * length * direction[1])

        points[mcp_index] = mcp
        points[pip_index] = pip
        points[dip_index] = dip
        points[tip_index] = tip

    # Translate so that the tracked palm centre lands exactly on (x, y).
    shift_x = x - _PALM_OFFSET[0] * scale
    shift_y = y - _PALM_OFFSET[1] * scale
    landmarks = [
        SimpleNamespace(
            x=point[0] * scale + shift_x,
            y=point[1] * scale + shift_y,
            z=0.0,
        )
        for point in points
    ]
    return SimpleNamespace(landmark=landmarks)


class GlobalHandScrollControllerTests(unittest.TestCase):
    FRAME = 1.0 / 30.0

    def make_controller(self):
        events = []
        # The clock lets the publisher stamp each event with the frame time so
        # tests can assert when a direction was allowed to start scrolling.
        self.clock = {"now": 0.0}
        controller = ScrollController(
            event_publisher=lambda direction, amount: events.append({
                "direction": direction,
                "amount": amount,
                "time": self.clock["now"],
            })
        )
        return controller, events

    def drive(self, controller, positions, start=0.0, dt=FRAME, **hand_kwargs):
        """Feed one hand per position; return the timestamp of the last frame."""
        now = start
        for index, position in enumerate(positions):
            if isinstance(position, tuple) and len(position) == 2 and not isinstance(position[0], (list, tuple)):
                x, y = position
                # A list parameter is cycled frame by frame (used to wiggle
                # fingers without moving the palm); tuples stay intact because
                # ``fingers=(True, False, ...)`` is a per-hand pose spec.
                overrides = {
                    key: (value[index % len(value)] if isinstance(value, list) else value)
                    for key, value in hand_kwargs.items()
                }
                hand = build_hand(x, y, **overrides)
            else:
                hand = position
            self.clock["now"] = now
            controller.process_hand(hand, timestamp=now)
            now += dt
        return now - dt

    # ------------------------------------------------------------------
    # Pose gates
    # ------------------------------------------------------------------
    def test_open_palm_is_detected_and_other_poses_are_not(self):
        controller, _ = self.make_controller()

        self.assertTrue(controller.evaluate_open_palm(build_hand(0.5, 0.5))[0])

        for name, kwargs in (
            ("fist", {"fingers": (False, False, False, False), "thumb": False}),
            ("one finger", {"fingers": (True, False, False, False)}),
            ("two fingers", {"fingers": (True, True, False, False)}),
            ("three fingers", {"fingers": (True, True, True, False)}),
            ("splayed fingers", {"fan": 9.0}),
        ):
            with self.subTest(pose=name):
                self.assertFalse(controller.evaluate_open_palm(build_hand(0.5, 0.5, **kwargs))[0])

    def test_non_open_poses_never_scroll_even_when_moved_vertically(self):
        for name, kwargs in (
            ("fist", {"fingers": (False, False, False, False), "thumb": False}),
            ("one finger", {"fingers": (True, False, False, False)}),
            ("two fingers", {"fingers": (True, True, False, False)}),
        ):
            with self.subTest(pose=name):
                controller, events = self.make_controller()
                self.drive(
                    controller,
                    [(0.50, 0.62 - index * 0.03) for index in range(14)],
                    **kwargs,
                )
                self.assertEqual(events, [])

    def test_pose_must_be_stable_before_scrolling_starts(self):
        controller, events = self.make_controller()
        # Only two stable frames are available before the hand is removed, so
        # the gesture must never arm.
        self.drive(controller, [(0.50, 0.60), (0.50, 0.55)])
        self.assertEqual(events, [])

    # ------------------------------------------------------------------
    # Sideways / diagonal rejection
    # ------------------------------------------------------------------
    def test_sideways_movement_left_to_right_does_not_scroll(self):
        controller, events = self.make_controller()
        self.drive(controller, [(0.30 + index * 0.03, 0.50) for index in range(14)])
        self.assertEqual(events, [])

    def test_sideways_movement_right_to_left_does_not_scroll(self):
        controller, events = self.make_controller()
        self.drive(controller, [(0.70 - index * 0.03, 0.50) for index in range(14)])
        self.assertEqual(events, [])

    def test_large_sideways_sweep_does_not_scroll(self):
        controller, events = self.make_controller()
        self.drive(controller, [(0.15 + index * 0.05, 0.50) for index in range(12)])
        self.assertEqual(events, [])

    def test_diagonal_movement_does_not_scroll_when_horizontal_dominates(self):
        controller, events = self.make_controller()
        # Steep diagonal: horizontal travel is twice the vertical travel.
        self.drive(controller, [(0.30 + index * 0.04, 0.60 - index * 0.02) for index in range(12)])
        self.assertEqual(events, [])

    def test_diagonal_movement_does_not_scroll_when_vertical_is_not_dominant(self):
        controller, events = self.make_controller()
        # A 45 degree sweep: |dy| == |dx|, which is below the 1.5x dominance.
        self.drive(controller, [(0.35 + index * 0.02, 0.62 - index * 0.02) for index in range(14)])
        self.assertEqual(events, [])

    def test_diagonal_movement_scrolls_only_when_vertical_clearly_dominates(self):
        controller, events = self.make_controller()
        self.drive(controller, [(0.45 + index * 0.008, 0.62 - index * 0.03) for index in range(16)])
        self.assertTrue(events)
        self.assertTrue(all(event["direction"] == "up" for event in events))

    # ------------------------------------------------------------------
    # Vertical scrolling
    # ------------------------------------------------------------------
    def test_upward_open_palm_movement_scrolls_up(self):
        controller, events = self.make_controller()
        self.drive(controller, [(0.50, 0.62 - index * 0.03) for index in range(14)])

        self.assertTrue(events)
        self.assertTrue(all(event["direction"] == "up" for event in events))
        for event in events:
            self.assertGreaterEqual(event["amount"], 1)
            self.assertLessEqual(event["amount"], controller.max_amount)

    def test_downward_open_palm_movement_scrolls_down(self):
        controller, events = self.make_controller()
        self.drive(controller, [(0.50, 0.30 + index * 0.03) for index in range(14)])

        self.assertTrue(events)
        self.assertTrue(all(event["direction"] == "down" for event in events))

    def test_tiny_movement_does_not_scroll(self):
        controller, events = self.make_controller()
        positions = [(0.50, 0.50 + offset) for offset in (0.0, 0.004, -0.004, 0.005, -0.005, 0.004, 0.0)]
        self.drive(controller, positions * 4, dt=self.FRAME)
        self.assertEqual(events, [])

    def test_stationary_hand_does_not_scroll(self):
        controller, events = self.make_controller()
        self.drive(controller, [(0.50, 0.50)] * 40)
        self.assertEqual(events, [])

    def test_finger_only_movement_does_not_scroll(self):
        controller, events = self.make_controller()
        # Arm the pose, then wiggle the fingers without moving the palm.
        self.drive(controller, [(0.50, 0.50)] * 8)
        self.drive(
            controller,
            [(0.50, 0.50)] * 20,
            start=8 * self.FRAME,
            tip_spread=[0.0, 0.08],
        )
        self.assertEqual(events, [])

    def test_continuous_upward_movement_streams_smooth_events(self):
        controller, events = self.make_controller()
        self.drive(controller, [(0.50, 0.78 - index * 0.02) for index in range(30)])

        self.assertGreaterEqual(len(events), 3, "a continuous sweep must keep scrolling")
        self.assertTrue(all(event["direction"] == "up" for event in events))
        # Smooth: no single event may jump a large distance, and consecutive
        # events must be similar in size.
        for event in events:
            self.assertLessEqual(event["amount"], controller.max_amount)
        for previous, current in zip(events, events[1:]):
            ratio = current["amount"] / max(1.0, previous["amount"])
            self.assertLess(ratio, 4.0)
            self.assertGreater(ratio, 0.25)

    def test_continuous_downward_movement_streams_smooth_events(self):
        controller, events = self.make_controller()
        self.drive(controller, [(0.50, 0.20 + index * 0.02) for index in range(30)])

        self.assertGreaterEqual(len(events), 3)
        self.assertTrue(all(event["direction"] == "down" for event in events))

    def test_scrolling_stops_shortly_after_the_hand_stops(self):
        controller, events = self.make_controller()
        last = self.drive(controller, [(0.50, 0.62 - index * 0.03) for index in range(12)])
        moving_events = len(events)
        self.assertGreater(moving_events, 0)

        # Hold the final position: only a short easing tail is acceptable.
        self.drive(controller, [(0.50, 0.62 - 11 * 0.03)] * 30, start=last + self.FRAME)
        self.assertLessEqual(len(events) - moving_events, 3)

    def test_rate_limit_bounds_the_event_stream(self):
        controller, events = self.make_controller()
        duration = self.drive(controller, [(0.50, 0.85 - index * 0.02) for index in range(40)])
        maximum_events = int(duration / Config.SCROLL_MIN_EVENT_INTERVAL) + 2
        self.assertLessEqual(len(events), maximum_events)

    def test_direction_reversal_resets_the_baseline(self):
        controller, events = self.make_controller()
        reverse_start = 10 * self.FRAME
        last = self.drive(controller, [(0.50, 0.62 - index * 0.03) for index in range(10)])
        upward_events = len(events)
        self.assertGreater(upward_events, 0)

        # Reverse: the controller must re-arm the opposite direction before it
        # scrolls down instead of flipping instantly on the first frame.
        self.drive(controller, [(0.50, 0.32 + index * 0.03) for index in range(10)], start=last + self.FRAME)

        downward = [event for event in events if event["direction"] == "down"]
        self.assertTrue(downward, "downward scrolling resumes after re-arming")
        # The old direction must not continue indefinitely: every event after
        # the reversal plus the re-arm delay is downward.
        first_down = min(event["time"] for event in downward)
        self.assertGreater(
            first_down,
            reverse_start,
            "the opposite direction must restart its movement baseline first",
        )
        for event in events:
            if event["time"] > first_down:
                self.assertEqual(event["direction"], "down")

    def test_rapid_alternating_movement_does_not_jitter(self):
        controller, events = self.make_controller()
        positions = []
        for index in range(60):
            positions.append((0.50, 0.50 + (0.05 if index % 2 == 0 else -0.05)))
        self.drive(controller, positions)
        self.assertEqual(events, [], "rapid up/down flicker must not scroll")

    # ------------------------------------------------------------------
    # Sensitivity, bounds and reset behaviour
    # ------------------------------------------------------------------
    def test_sensitivity_levels_change_the_scroll_distance(self):
        totals = {}
        for level in ("low", "medium", "high"):
            controller, events = self.make_controller()
            controller.set_sensitivity(level)
            self.drive(controller, [(0.50, 0.78 - index * 0.02) for index in range(24)])
            totals[level] = sum(event["amount"] for event in events)

        self.assertGreater(totals["medium"], totals["low"])
        self.assertGreater(totals["high"], totals["medium"])

    def test_missing_hand_resets_the_gesture(self):
        controller, events = self.make_controller()
        self.drive(controller, [(0.50, 0.62 - index * 0.03) for index in range(10)])
        self.assertTrue(events)

        controller.process_hand(None, timestamp=9.0)
        events.clear()
        # A hand that reappears must re-arm before it can scroll again.
        self.drive(controller, [(0.50, 0.40), (0.50, 0.35)], start=9.1)
        self.assertEqual(events, [])

    def test_controller_never_emits_gesture_labels(self):
        controller, events = self.make_controller()
        self.drive(controller, [(0.50, 0.62 - index * 0.03) for index in range(14)])
        for event in events:
            self.assertEqual(set(event.keys()) - {"time"}, {"direction", "amount"})
            self.assertIn(event["direction"], {"up", "down"})

    # ------------------------------------------------------------------
    # Real MediaPipe landmark regression
    # ------------------------------------------------------------------
    def test_real_mediapipe_open_palms_are_accepted(self):
        controller, _ = self.make_controller()
        for name, points in (("raised", OPEN_PALM_RAISED), ("spread", OPEN_PALM_SPREAD)):
            with self.subTest(pose=name):
                valid, signature = controller.evaluate_open_palm(RealHand(points))
                self.assertTrue(valid, f"{name} open palm must arm the scroll gesture")
                self.assertEqual(len(signature), 6)

    def test_real_mediapipe_fist_is_rejected(self):
        controller, _ = self.make_controller()
        self.assertFalse(controller.evaluate_open_palm(RealHand(CLOSED_FIST))[0])

    def test_real_mediapipe_open_palm_scrolls_vertically_only(self):
        # Rigidly translate a real open palm down the frame (a clear vertical
        # sweep) and then across the frame (a sideways sweep).
        for label, steps, expected in (
            ("vertical", [(0.0, index * 0.030) for index in range(16)], True),
            ("sideways", [(index * 0.030, 0.0) for index in range(16)], False),
        ):
            with self.subTest(movement=label):
                controller, events = self.make_controller()
                now = 0.0
                for dx, dy in steps:
                    self.clock["now"] = now
                    controller.process_hand(RealHand(OPEN_PALM_RAISED, dx, dy), timestamp=now)
                    now += self.FRAME
                if expected:
                    self.assertTrue(events, "a real vertical palm sweep must scroll")
                    self.assertTrue(all(event["direction"] == "down" for event in events))
                else:
                    self.assertEqual(events, [], "a sideways palm sweep must not scroll")


if __name__ == "__main__":
    unittest.main()
