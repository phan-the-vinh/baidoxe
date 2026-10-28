"""Unit tests for YOLO-to-polygon assignment and temporal state confirmation."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from yolo_polygon_logic import (  # noqa: E402
    EMPTY,
    EVIDENCE_CLEAR,
    EVIDENCE_OCCUPIED,
    EVIDENCE_UNCERTAIN,
    OCCUPIED,
    UNKNOWN,
    TemporalSlotState,
    assign_detections_to_slots,
    build_slots,
    filter_detections_by_exclusion,
    is_border_truncated,
    vehicle_core_box,
)
from test_yolov8n_video import draw_detections  # noqa: E402


class YoloPolygonLogicTests(unittest.TestCase):
    def setUp(self) -> None:
        polygons = [
            {
                "id": "D01",
                "type": "parking_space",
                "points": [[0, 0], [100, 0], [100, 200], [0, 200]],
            },
            {
                "id": "D02",
                "type": "parking_space",
                "points": [[100, 0], [200, 0], [200, 200], [100, 200]],
            },
        ]
        self.slots = build_slots(polygons, (200, 200), (200, 200))

    @staticmethod
    def detection(x1: float, y1: float, x2: float, y2: float) -> dict:
        return {
            "class_id": 0,
            "confidence": 0.8,
            "x1": x1,
            "y1": y1,
            "x2": x2,
            "y2": y2,
        }

    def test_one_detection_is_assigned_to_only_one_slot(self) -> None:
        matches, evidence = assign_detections_to_slots(
            [self.detection(60, 20, 140, 180)],
            self.slots,
            minimum_bbox_overlap=0.45,
            minimum_slot_overlap=0.12,
            uncertain_overlap=0.10,
        )
        self.assertEqual(len(matches), 1)
        self.assertEqual(sum(value == EVIDENCE_OCCUPIED for value in evidence.values()), 1)

    def test_edge_overlap_is_uncertain_not_occupied(self) -> None:
        matches, evidence = assign_detections_to_slots(
            [self.detection(-52, 10, 48, 190)],
            self.slots,
            minimum_bbox_overlap=0.45,
            minimum_slot_overlap=0.20,
            uncertain_overlap=0.10,
        )
        self.assertEqual(matches, {})
        self.assertEqual(evidence["D01"], EVIDENCE_UNCERTAIN)
        self.assertEqual(evidence["D02"], EVIDENCE_CLEAR)

    def test_small_slot_edge_contact_from_center_outside_is_clear(self) -> None:
        matches, evidence = assign_detections_to_slots(
            [self.detection(10, -80, 90, 20)],
            self.slots,
            minimum_bbox_overlap=0.45,
            minimum_slot_overlap=0.20,
            uncertain_overlap=0.10,
        )
        self.assertEqual(matches, {})
        self.assertEqual(evidence["D01"], EVIDENCE_CLEAR)

    def test_compact_false_positive_is_ambiguous_not_occupied(self) -> None:
        matches, evidence = assign_detections_to_slots(
            [self.detection(10, 5, 90, 85)],
            self.slots,
            minimum_bbox_overlap=0.45,
            minimum_slot_overlap=0.20,
            uncertain_overlap=0.10,
            minimum_axis_elongation=1.25,
        )
        self.assertEqual(matches, {})
        self.assertEqual(evidence["D01"], EVIDENCE_UNCERTAIN)

    def test_elongated_detection_remains_eligible_for_vertical_slot(self) -> None:
        matches, evidence = assign_detections_to_slots(
            [self.detection(20, 10, 80, 190)],
            self.slots,
            minimum_bbox_overlap=0.45,
            minimum_slot_overlap=0.20,
            uncertain_overlap=0.10,
            minimum_axis_elongation=1.25,
        )
        self.assertEqual(set(matches), {"D01"})
        self.assertEqual(evidence["D01"], EVIDENCE_OCCUPIED)

    def test_vehicle_core_is_inset_without_changing_detection(self) -> None:
        detection = self.detection(10, 20, 110, 220)
        original = dict(detection)
        core = vehicle_core_box(
            detection,
            horizontal_inset=0.15,
            top_inset=0.10,
            bottom_inset=0.25,
        )
        self.assertEqual(core, [25.0, 40.0, 95.0, 170.0])
        self.assertEqual(detection, original)

    def test_edge_only_bbox_contact_does_not_create_occupied_evidence(self) -> None:
        matches, evidence = assign_detections_to_slots(
            [self.detection(-120, 10, 20, 190)],
            self.slots,
            minimum_bbox_overlap=0.10,
            minimum_slot_overlap=0.05,
            uncertain_overlap=0.05,
            minimum_axis_elongation=1.0,
            core_horizontal_inset=0.20,
            core_top_inset=0.10,
            core_bottom_inset=0.10,
        )
        self.assertEqual(matches, {})
        self.assertEqual(evidence["D01"], EVIDENCE_UNCERTAIN)

    def test_border_truncated_box_requires_secondary_core_support(self) -> None:
        polygons = [
            {
                "id": "D01",
                "type": "parking_space",
                "points": [[0, 45], [40, 45], [40, 200], [0, 200]],
            }
        ]
        slots = build_slots(polygons, (200, 200), (200, 200))
        detection = self.detection(0, 0, 40, 90)
        self.assertTrue(is_border_truncated(detection, (200, 200)))
        matches, evidence = assign_detections_to_slots(
            [detection],
            slots,
            minimum_bbox_overlap=0.45,
            minimum_slot_overlap=0.12,
            uncertain_overlap=0.10,
            minimum_axis_elongation=1.25,
            frame_size=(200, 200),
        )
        self.assertEqual(matches, {})
        self.assertEqual(evidence["D01"], EVIDENCE_UNCERTAIN)

    def test_temporal_confirmation_and_release(self) -> None:
        state = TemporalSlotState("D01", 3, 4, 5)
        self.assertEqual(state.state, UNKNOWN)
        state.update(EVIDENCE_OCCUPIED)
        state.update(EVIDENCE_OCCUPIED)
        self.assertEqual(state.state, UNKNOWN)
        transition = state.update(EVIDENCE_OCCUPIED)
        self.assertEqual(transition, (UNKNOWN, OCCUPIED))

        for _ in range(4):
            state.update(EVIDENCE_CLEAR)
        self.assertEqual(state.state, OCCUPIED)
        transition = state.update(EVIDENCE_CLEAR)
        self.assertEqual(transition, (OCCUPIED, EMPTY))

    def test_uncertain_evidence_holds_confirmed_state(self) -> None:
        state = TemporalSlotState("D01", 1, 1, 2)
        state.update(EVIDENCE_OCCUPIED)
        self.assertEqual(state.state, OCCUPIED)
        for _ in range(10):
            state.update(EVIDENCE_UNCERTAIN)
        self.assertEqual(state.state, OCCUPIED)

    def test_sustained_ambiguity_changes_empty_to_unknown(self) -> None:
        state = TemporalSlotState("D01", 3, 2, 4, ambiguous_frames=3)
        state.update(EVIDENCE_CLEAR)
        state.update(EVIDENCE_CLEAR)
        self.assertEqual(state.state, EMPTY)
        state.update(EVIDENCE_UNCERTAIN)
        state.update(EVIDENCE_UNCERTAIN)
        self.assertEqual(state.state, EMPTY)
        transition = state.update(EVIDENCE_UNCERTAIN)
        self.assertEqual(transition, (EMPTY, UNKNOWN))
        state.update(EVIDENCE_OCCUPIED)
        self.assertEqual(state.state, UNKNOWN)

    def test_unknown_requires_full_confirmation_for_occupied(self) -> None:
        state = TemporalSlotState("D01", 3, 2, 4, ambiguous_frames=1)
        state.update(EVIDENCE_CLEAR)
        state.update(EVIDENCE_CLEAR)
        state.update(EVIDENCE_UNCERTAIN)
        self.assertEqual(state.state, UNKNOWN)
        state.update(EVIDENCE_OCCUPIED)
        state.update(EVIDENCE_OCCUPIED)
        self.assertEqual(state.state, UNKNOWN)
        transition = state.update(EVIDENCE_OCCUPIED)
        self.assertEqual(transition, (UNKNOWN, OCCUPIED))

    def test_exclusion_rejects_centered_majority_overlap(self) -> None:
        exclusion = np.asarray([[110, 0], [190, 0], [190, 200], [110, 200]])
        detections = [self.detection(120, 20, 180, 180)]
        kept, rejected = filter_detections_by_exclusion(detections, exclusion)
        self.assertEqual(kept, [])
        self.assertEqual(len(rejected), 1)
        self.assertGreaterEqual(rejected[0]["bbox_overlap"], 0.60)

    def test_exclusion_keeps_light_edge_contact(self) -> None:
        exclusion = np.asarray([[110, 0], [190, 0], [190, 200], [110, 200]])
        detections = [self.detection(60, 20, 120, 180)]
        kept, rejected = filter_detections_by_exclusion(detections, exclusion)
        self.assertEqual(kept, detections)
        self.assertEqual(rejected, [])

    def test_exclusion_keeps_box_with_center_outside(self) -> None:
        exclusion = np.asarray([[110, 0], [190, 0], [190, 200], [110, 200]])
        detections = [self.detection(20, 20, 150, 180)]
        kept, rejected = filter_detections_by_exclusion(detections, exclusion)
        self.assertEqual(kept, detections)
        self.assertEqual(rejected, [])

    def test_detection_drawing_keeps_interior_unfilled(self) -> None:
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        detection = self.detection(100, 200, 300, 500)
        annotated = draw_detections(frame, [detection], {0: "Car"})
        self.assertTrue(np.any(annotated[350, 100]))
        self.assertTrue(np.all(annotated[350, 200] == 0))
        self.assertTrue(np.all(frame == 0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
