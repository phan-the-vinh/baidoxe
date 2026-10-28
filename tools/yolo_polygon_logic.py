"""Geometry matching and temporal state logic for the YOLO + polygon demo."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from parking_polygon_config import scale_points


OCCUPIED = "OCCUPIED"
EMPTY = "EMPTY"
UNKNOWN = "UNKNOWN"
EVIDENCE_OCCUPIED = "occupied"
EVIDENCE_CLEAR = "clear"
EVIDENCE_UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class SlotGeometry:
    identifier: str
    points: np.ndarray
    area: float


@dataclass
class TemporalSlotState:
    identifier: str
    occupied_frames: int
    empty_frames: int
    release_frames: int
    ambiguous_frames: int = 8
    state: str = UNKNOWN
    occupied_streak: int = 0
    clear_streak: int = 0
    ambiguous_streak: int = 0

    def update(self, evidence: str) -> tuple[str, str] | None:
        """Update a slot and return (previous, current) when state changes."""
        previous = self.state
        if evidence == EVIDENCE_OCCUPIED:
            self.occupied_streak += 1
            self.clear_streak = 0
            self.ambiguous_streak = 0
            if self.occupied_streak >= self.occupied_frames:
                self.state = OCCUPIED
        elif evidence == EVIDENCE_CLEAR:
            self.clear_streak += 1
            self.occupied_streak = 0
            self.ambiguous_streak = 0
            required = self.release_frames if self.state == OCCUPIED else self.empty_frames
            if self.clear_streak >= required:
                self.state = EMPTY
        elif evidence == EVIDENCE_UNCERTAIN:
            self.occupied_streak = 0
            self.clear_streak = 0
            self.ambiguous_streak += 1
            # A confirmed occupied slot is deliberately held until clear
            # evidence satisfies the release hysteresis. For an EMPTY slot,
            # sustained ambiguous geometry means the truthful state is UNKNOWN.
            if (
                self.state == EMPTY
                and self.ambiguous_streak >= self.ambiguous_frames
            ):
                self.state = UNKNOWN
        else:
            raise ValueError(f"Unsupported evidence: {evidence}")
        if self.state != previous:
            return previous, self.state
        return None


def vehicle_core_box(
    detection: dict[str, float | int],
    *,
    horizontal_inset: float,
    top_inset: float,
    bottom_inset: float,
) -> list[float]:
    """Return an inner box used as a conservative vehicle-body footprint."""
    for name, value in (
        ("horizontal_inset", horizontal_inset),
        ("top_inset", top_inset),
        ("bottom_inset", bottom_inset),
    ):
        if not 0 <= value < 0.5:
            raise ValueError(f"{name} must be between 0 and 0.5")
    if top_inset + bottom_inset >= 1:
        raise ValueError("top_inset + bottom_inset must be less than 1")
    x1 = float(detection["x1"])
    y1 = float(detection["y1"])
    x2 = float(detection["x2"])
    y2 = float(detection["y2"])
    width = max(1.0, x2 - x1)
    height = max(1.0, y2 - y1)
    return [
        x1 + width * horizontal_inset,
        y1 + height * top_inset,
        x2 - width * horizontal_inset,
        y2 - height * bottom_inset,
    ]


def is_border_truncated(
    detection: dict[str, float | int],
    frame_size: tuple[int, int],
    *,
    margin_ratio: float = 0.01,
) -> bool:
    """Return whether a detector box touches the outer frame margin."""
    if not 0 <= margin_ratio < 0.5:
        raise ValueError("margin_ratio must be between 0 and 0.5")
    frame_width, frame_height = frame_size
    if frame_width <= 0 or frame_height <= 0:
        raise ValueError("frame_size must be positive")
    margin_x = frame_width * margin_ratio
    margin_y = frame_height * margin_ratio
    return (
        float(detection["x1"]) <= margin_x
        or float(detection["y1"]) <= margin_y
        or float(detection["x2"]) >= frame_width - margin_x
        or float(detection["y2"]) >= frame_height - margin_y
    )


def seconds_to_frames(seconds: float, fps: float) -> int:
    if seconds <= 0 or fps <= 0:
        raise ValueError("Seconds and FPS must be positive")
    return max(1, int(math.ceil(seconds * fps)))


def build_slots(
    polygons: list[dict[str, Any]],
    source_size: tuple[int, int],
    target_size: tuple[int, int],
) -> list[SlotGeometry]:
    slots: list[SlotGeometry] = []
    for polygon in polygons:
        if polygon["type"] != "parking_space":
            continue
        points = np.asarray(
            scale_points(polygon["points"], source_size, target_size),
            dtype=np.float32,
        )
        if not cv2.isContourConvex(points.astype(np.int32)):
            raise ValueError(f"Polygon {polygon['id']} must be convex for intersection")
        area = float(cv2.contourArea(points))
        if area <= 0:
            raise ValueError(f"Polygon {polygon['id']} has invalid scaled area")
        slots.append(SlotGeometry(str(polygon["id"]), points, area))
    return slots


def _intersection_metrics(
    detection: dict[str, float | int],
    slot: SlotGeometry,
    *,
    core_horizontal_inset: float = 0.15,
    core_top_inset: float = 0.10,
    core_bottom_inset: float = 0.25,
) -> dict[str, Any]:
    x1 = float(detection["x1"])
    y1 = float(detection["y1"])
    x2 = float(detection["x2"])
    y2 = float(detection["y2"])
    box = np.asarray([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], np.float32)
    intersection = float(cv2.intersectConvexConvex(slot.points, box)[0])
    box_area = max(1.0, (x2 - x1) * (y2 - y1))
    box_width = max(1.0, x2 - x1)
    box_height = max(1.0, y2 - y1)
    slot_width = max(1.0, float(np.ptp(slot.points[:, 0])))
    slot_height = max(1.0, float(np.ptp(slot.points[:, 1])))
    # A parked vehicle should be elongated along the dominant axis of its
    # parking space. This filters compact false positives at a slot edge while
    # leaving the detector's bounding box unchanged.
    axis_elongation = (
        box_height / box_width
        if slot_height >= slot_width
        else box_width / box_height
    )
    center_inside = (
        cv2.pointPolygonTest(
            slot.points,
            ((x1 + x2) / 2.0, (y1 + y2) / 2.0),
            False,
        )
        >= 0
    )
    core_x1, core_y1, core_x2, core_y2 = vehicle_core_box(
        detection,
        horizontal_inset=core_horizontal_inset,
        top_inset=core_top_inset,
        bottom_inset=core_bottom_inset,
    )
    core = np.asarray(
        [
            [core_x1, core_y1],
            [core_x2, core_y1],
            [core_x2, core_y2],
            [core_x1, core_y2],
        ],
        np.float32,
    )
    core_intersection = float(cv2.intersectConvexConvex(slot.points, core)[0])
    core_area = max(1.0, (core_x2 - core_x1) * (core_y2 - core_y1))
    core_center_inside = (
        cv2.pointPolygonTest(
            slot.points,
            ((core_x1 + core_x2) / 2.0, (core_y1 + core_y2) / 2.0),
            False,
        )
        >= 0
    )
    return {
        "center_inside": center_inside,
        "bbox_overlap": intersection / box_area,
        "slot_overlap": intersection / slot.area,
        "intersection_area": intersection,
        "core_center_inside": core_center_inside,
        "core_overlap": core_intersection / core_area,
        "core_slot_overlap": core_intersection / slot.area,
        "core_intersection_area": core_intersection,
        "core_box": [core_x1, core_y1, core_x2, core_y2],
        "axis_elongation": axis_elongation,
    }


def assign_detections_to_slots(
    detections: list[dict[str, float | int]],
    slots: list[SlotGeometry],
    *,
    minimum_bbox_overlap: float,
    minimum_slot_overlap: float,
    uncertain_overlap: float,
    minimum_axis_elongation: float = 1.25,
    core_horizontal_inset: float = 0.15,
    core_top_inset: float = 0.10,
    core_bottom_inset: float = 0.25,
    frame_size: tuple[int, int] | None = None,
    border_margin_ratio: float = 0.01,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Assign each detection to at most one slot and produce raw slot evidence."""
    if not 0 <= uncertain_overlap <= 1:
        raise ValueError("uncertain_overlap must be between 0 and 1")
    if not 0 <= minimum_bbox_overlap <= 1 or not 0 <= minimum_slot_overlap <= 1:
        raise ValueError("Overlap thresholds must be between 0 and 1")
    if minimum_axis_elongation < 1:
        raise ValueError("minimum_axis_elongation must be at least 1")
    if not 0 <= border_margin_ratio < 0.5:
        raise ValueError("border_margin_ratio must be between 0 and 0.5")

    evidence = {slot.identifier: EVIDENCE_CLEAR for slot in slots}
    matches: dict[str, dict[str, Any]] = {}

    for detection_index, detection in enumerate(detections):
        border_truncated = (
            is_border_truncated(
                detection,
                frame_size,
                margin_ratio=border_margin_ratio,
            )
            if frame_size is not None
            else False
        )
        eligible: list[tuple[float, SlotGeometry, dict[str, Any]]] = []
        for slot in slots:
            metrics = _intersection_metrics(
                detection,
                slot,
                core_horizontal_inset=core_horizontal_inset,
                core_top_inset=core_top_inset,
                core_bottom_inset=core_bottom_inset,
            )
            bbox_overlap = float(metrics["bbox_overlap"])
            slot_overlap = float(metrics["slot_overlap"])
            core_overlap = float(metrics["core_overlap"])
            core_slot_overlap = float(metrics["core_slot_overlap"])
            axis_eligible = (
                float(metrics["axis_elongation"]) >= minimum_axis_elongation
            )
            ambiguous_contact = (
                bbox_overlap >= uncertain_overlap
                and slot_overlap >= uncertain_overlap
            )
            if not axis_eligible:
                if ambiguous_contact:
                    evidence[slot.identifier] = EVIDENCE_UNCERTAIN
                continue
            # Preserve the established uncertainty rule. In particular, weak
            # edge evidence intentionally delays EMPTY confirmation; replacing
            # this with core-only evidence makes D16 release too early.
            if bool(metrics["center_inside"]) or (
                bbox_overlap >= uncertain_overlap
                and slot_overlap >= uncertain_overlap
            ):
                evidence[slot.identifier] = EVIDENCE_UNCERTAIN
            baseline_occupied = (
                bool(metrics["center_inside"])
                and bbox_overlap >= minimum_bbox_overlap
                and slot_overlap >= minimum_slot_overlap
            )
            # Core is only a secondary veto for frame-border detections, where
            # a clipped box is most likely to contribute edge/background area.
            # It is not a global replacement for the validated bbox geometry.
            core_supports_border_box = (
                bool(metrics["core_center_inside"])
                and core_overlap >= minimum_bbox_overlap
                and core_slot_overlap >= minimum_slot_overlap
            )
            if baseline_occupied and (
                not border_truncated or core_supports_border_box
            ):
                score = (
                    bbox_overlap
                    + slot_overlap
                    + 0.05 * float(detection["confidence"])
                )
                eligible.append((score, slot, metrics))

        if not eligible:
            continue
        score, slot, metrics = max(eligible, key=lambda item: item[0])
        candidate = {
            "detection_index": detection_index,
            "score": score,
            "confidence": float(detection["confidence"]),
            "bbox_overlap": float(metrics["bbox_overlap"]),
            "slot_overlap": float(metrics["slot_overlap"]),
            "core_overlap": float(metrics["core_overlap"]),
            "core_slot_overlap": float(metrics["core_slot_overlap"]),
            "core_center_inside": bool(metrics["core_center_inside"]),
            "core_box": list(metrics["core_box"]),
            "axis_elongation": float(metrics["axis_elongation"]),
            "center_inside": bool(metrics["center_inside"]),
            "is_border_truncated": border_truncated,
            "box": [
                float(detection["x1"]),
                float(detection["y1"]),
                float(detection["x2"]),
                float(detection["y2"]),
            ],
        }
        current = matches.get(slot.identifier)
        if current is None or candidate["score"] > current["score"]:
            matches[slot.identifier] = candidate

    for identifier in matches:
        evidence[identifier] = EVIDENCE_OCCUPIED
    return matches, evidence


def filter_detections_by_exclusion(
    detections: list[dict[str, float | int]],
    exclusion_points: np.ndarray,
    *,
    minimum_bbox_overlap: float = 0.60,
) -> tuple[list[dict[str, float | int]], list[dict[str, Any]]]:
    """Evaluate one proposed exclusion without changing parking-slot geometry.

    A detection is rejected only when both its center is inside the exclusion and
    most of its bounding-box area overlaps it. This deliberately keeps boxes that
    merely touch the boundary. The video processor does not call this function
    until an exclusion proposal has been reviewed and accepted.
    """
    if not 0 <= minimum_bbox_overlap <= 1:
        raise ValueError("minimum_bbox_overlap must be between 0 and 1")
    points = np.asarray(exclusion_points, dtype=np.float32)
    if len(points) < 3 or not cv2.isContourConvex(points.astype(np.int32)):
        raise ValueError("Exclusion polygon must be convex and have at least 3 points")
    area = float(cv2.contourArea(points))
    if area <= 0:
        raise ValueError("Exclusion polygon area must be positive")
    exclusion = SlotGeometry("EXCLUSION", points, area)
    kept: list[dict[str, float | int]] = []
    rejected: list[dict[str, Any]] = []
    for detection_index, detection in enumerate(detections):
        metrics = _intersection_metrics(detection, exclusion)
        should_reject = (
            bool(metrics["center_inside"])
            and float(metrics["bbox_overlap"]) >= minimum_bbox_overlap
        )
        if should_reject:
            rejected.append(
                {
                    "detection_index": detection_index,
                    "confidence": float(detection["confidence"]),
                    "bbox_overlap": float(metrics["bbox_overlap"]),
                    "center_inside": True,
                    "box": [
                        float(detection["x1"]),
                        float(detection["y1"]),
                        float(detection["x2"]),
                        float(detection["y2"]),
                    ],
                }
            )
        else:
            kept.append(detection)
    return kept, rejected
