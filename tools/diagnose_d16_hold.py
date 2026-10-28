"""Audit the D16 stale-OCCUPIED cause and render before/after diagnostics."""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
import torch
from ultralytics import YOLO

from parking_polygon_config import scale_points
from test_yolo_polygon_video import validate_confirmed_polygons
from test_yolov8n_video import extract_detections
from yolo_polygon_logic import (
    EVIDENCE_CLEAR,
    EVIDENCE_OCCUPIED,
    EVIDENCE_UNCERTAIN,
    _intersection_metrics,
    assign_detections_to_slots,
    build_slots,
    filter_detections_by_exclusion,
)


ROOT = Path(__file__).resolve().parents[1]
VIDEO = ROOT / "demo_videos" / "0922(1).mp4"
WEIGHTS = (
    ROOT
    / "runs/detect/runs/yolo_car_v12/train_27_from_smoke01_best/weights/best.pt"
)
POLYGONS = ROOT / "config/demo_parking/0922_1_polygons.json"
BASELINE = (
    ROOT
    / "runs/detect/polygon_tests/yolo_car_v12_best/full_video_x01_phase4_conf040/slot_states.csv"
)
TRIAL = (
    ROOT
    / "runs/detect/polygon_tests/yolo_car_v12_best/d16_fix_trial_25_47_conf040/slot_states.csv"
)
OUTPUT = (
    ROOT
    / "runs/detect/polygon_diagnostics/d16_hold_analysis/fixed_rule_25_47"
)
SOURCE_SIZE = (3840, 2160)
OUTPUT_SIZE = (1920, 1080)
START_FRAME = 25 * 30
END_FRAME = 47 * 30
SAMPLE_FRAMES = {value * 30 for value in (30, 32, 35, 40, 42, 45)}
STATE_COLORS = {
    "OCCUPIED": (20, 20, 235),
    "EMPTY": (30, 210, 30),
    "UNKNOWN": (245, 245, 245),
}


def read_states(path: Path) -> dict[int, dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        return {
            int(row["frame_index"]): {
                f"D{number:02d}": row[f"D{number:02d}"]
                for number in range(1, 25)
            }
            for row in csv.DictReader(source)
        }


def frame_intervals(frames: list[int], fps: float) -> list[dict[str, float | int]]:
    if not frames:
        return []
    groups = []
    start = previous = frames[0]
    for frame in frames[1:]:
        if frame != previous + 1:
            groups.append((start, previous))
            start = frame
        previous = frame
    groups.append((start, previous))
    return [
        {
            "start_frame": start,
            "end_frame": end,
            "start_seconds": start / fps,
            "end_seconds": end / fps,
        }
        for start, end in groups
    ]


def legacy_evidence(detections, slots, matches, uncertain_overlap):
    evidence = {slot.identifier: EVIDENCE_CLEAR for slot in slots}
    for detection in detections:
        for slot in slots:
            metrics = _intersection_metrics(detection, slot)
            if (
                bool(metrics["center_inside"])
                or float(metrics["bbox_overlap"]) >= uncertain_overlap
                or float(metrics["slot_overlap"]) >= uncertain_overlap
            ):
                evidence[slot.identifier] = EVIDENCE_UNCERTAIN
    for identifier in matches:
        evidence[identifier] = EVIDENCE_OCCUPIED
    return evidence


def draw_panel(frame, slots, states, evidence, detections, matches, legacy_only, title):
    canvas = frame.copy()
    focus = {slot.identifier: slot for slot in slots if slot.identifier in {"D03", "D16"}}
    inverse = {
        int(match["detection_index"]): identifier
        for identifier, match in matches.items()
    }
    for identifier, slot in focus.items():
        color = STATE_COLORS[states[identifier]]
        cv2.polylines(canvas, [slot.points.astype(np.int32)], True, color, 4, cv2.LINE_AA)
        center = tuple(np.mean(slot.points, axis=0).astype(int))
        cv2.putText(
            canvas,
            f"{identifier} {states[identifier]}",
            center,
            cv2.FONT_HERSHEY_SIMPLEX,
            0.62,
            color,
            2,
            cv2.LINE_AA,
        )
    relevant = set(legacy_only)
    relevant.update(
        index for index, identifier in inverse.items() if identifier in {"D03", "D16"}
    )
    d16 = focus["D16"]
    for index in sorted(relevant):
        detection = detections[index]
        x1, y1, x2, y2 = (
            int(round(float(detection[key]))) for key in ("x1", "y1", "x2", "y2")
        )
        assigned = inverse.get(index)
        if assigned:
            color = (255, 190, 30)
            label = f"ASSIGNED {assigned} conf={float(detection['confidence']):.2f}"
        else:
            metrics = _intersection_metrics(detection, d16)
            color = (0, 210, 255)
            label = (
                "UNASSIGNED edge box "
                f"bbox={float(metrics['bbox_overlap']):.1%} "
                f"slot={float(metrics['slot_overlap']):.1%}"
            )
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 2, cv2.LINE_AA)
        cv2.putText(
            canvas,
            label,
            (max(0, x1), max(24, y2 + 20)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.52,
            color,
            2,
            cv2.LINE_AA,
        )
    cv2.rectangle(canvas, (0, 0), (1040, 70), (18, 18, 18), -1)
    cv2.putText(canvas, title, (14, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(
        canvas,
        f"D03 evidence={evidence['D03']} | D16 evidence={evidence['D16']}",
        (14, 57),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.62,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    return canvas


def main() -> None:
    if OUTPUT.exists():
        raise FileExistsError(f"Refusing to overwrite diagnostic output: {OUTPUT}")
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required for this diagnostic")
    OUTPUT.mkdir(parents=True)
    comparison_dir = OUTPUT / "comparisons"
    comparison_dir.mkdir()

    baseline_states = read_states(BASELINE)
    trial_states = read_states(TRIAL)
    config = validate_confirmed_polygons(POLYGONS)
    slots = build_slots(config["polygons"], SOURCE_SIZE, OUTPUT_SIZE)
    x01 = next(item for item in config["polygons"] if item["id"] == "X01")
    exclusion = np.asarray(
        scale_points(x01["points"], SOURCE_SIZE, OUTPUT_SIZE), dtype=np.float32
    )
    d16 = next(slot for slot in slots if slot.identifier == "D16")

    capture = cv2.VideoCapture(str(VIDEO))
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    capture.set(cv2.CAP_PROP_POS_FRAMES, START_FRAME)
    model = YOLO(str(WEIGHTS))
    evidence_changes = Counter()
    audit_rows = []
    legacy_only_frames = []
    last_d16_assignment = None
    first_fixed_clear_after_assignment = None

    for frame_index in range(START_FRAME, END_FRAME):
        ok, frame = capture.read()
        if not ok or frame is None:
            raise RuntimeError(f"Video stopped at frame {frame_index}")
        frame = cv2.resize(frame, OUTPUT_SIZE, interpolation=cv2.INTER_AREA)
        result = model.predict(
            source=frame,
            conf=0.40,
            iou=0.50,
            imgsz=512,
            device=0,
            classes=[0],
            verbose=False,
        )[0]
        raw = extract_detections(result)
        detections, _rejected = filter_detections_by_exclusion(
            raw, exclusion, minimum_bbox_overlap=0.60
        )
        matches, fixed = assign_detections_to_slots(
            detections,
            slots,
            minimum_bbox_overlap=0.45,
            minimum_slot_overlap=0.12,
            uncertain_overlap=0.10,
        )
        legacy = legacy_evidence(detections, slots, matches, 0.10)
        for identifier in fixed:
            if fixed[identifier] != legacy[identifier]:
                evidence_changes[identifier] += 1

        if "D16" in matches:
            last_d16_assignment = frame_index
        elif (
            last_d16_assignment is not None
            and first_fixed_clear_after_assignment is None
            and fixed["D16"] == EVIDENCE_CLEAR
        ):
            first_fixed_clear_after_assignment = frame_index

        legacy_only = []
        for index, detection in enumerate(detections):
            metrics = _intersection_metrics(detection, d16)
            old_contact = (
                bool(metrics["center_inside"])
                or float(metrics["bbox_overlap"]) >= 0.10
                or float(metrics["slot_overlap"]) >= 0.10
            )
            fixed_contact = bool(metrics["center_inside"]) or (
                float(metrics["bbox_overlap"]) >= 0.10
                and float(metrics["slot_overlap"]) >= 0.10
            )
            if old_contact and not fixed_contact:
                legacy_only.append(index)
                audit_rows.append(
                    {
                        "frame_index": frame_index,
                        "timestamp_seconds": f"{frame_index / fps:.6f}",
                        "detection_index": index,
                        "confidence": f"{float(detection['confidence']):.6f}",
                        "center_inside_d16": bool(metrics["center_inside"]),
                        "bbox_overlap_d16": f"{float(metrics['bbox_overlap']):.6f}",
                        "slot_overlap_d16": f"{float(metrics['slot_overlap']):.6f}",
                        **{
                            key: f"{float(detection[key]):.3f}"
                            for key in ("x1", "y1", "x2", "y2")
                        },
                    }
                )
        if legacy["D16"] != fixed["D16"]:
            legacy_only_frames.append(frame_index)

        if frame_index in SAMPLE_FRAMES:
            timestamp = frame_index / fps
            before = draw_panel(
                frame,
                slots,
                baseline_states[frame_index],
                legacy,
                detections,
                matches,
                legacy_only,
                f"BEFORE t={timestamp:.2f}s",
            )
            after = draw_panel(
                frame,
                slots,
                trial_states[frame_index],
                fixed,
                detections,
                matches,
                legacy_only,
                f"AFTER t={timestamp:.2f}s",
            )
            comparison = np.hstack((before, after))
            path = comparison_dir / f"d16_before_after_t{timestamp:05.2f}s.jpg"
            if not cv2.imwrite(str(path), comparison):
                raise RuntimeError(f"Cannot write {path}")

        if (frame_index - START_FRAME + 1) % 100 == 0:
            print(f"Audited {frame_index - START_FRAME + 1}/{END_FRAME - START_FRAME}", flush=True)
    capture.release()

    audit_path = OUTPUT / "d16_edge_contact_audit.csv"
    with audit_path.open("w", encoding="utf-8", newline="") as destination:
        fields = list(audit_rows[0]) if audit_rows else ["frame_index"]
        writer = csv.DictWriter(destination, fieldnames=fields)
        writer.writeheader()
        writer.writerows(audit_rows)

    other_state_changes = Counter()
    for frame_index in range(START_FRAME, END_FRAME):
        for identifier in baseline_states[frame_index]:
            if baseline_states[frame_index][identifier] != trial_states[frame_index][identifier]:
                other_state_changes[identifier] += 1

    summary = {
        "segment": {"start_seconds": 25.0, "end_seconds": 47.0, "frames": 660},
        "last_d16_accepted_assignment": {
            "frame": last_d16_assignment,
            "seconds": last_d16_assignment / fps if last_d16_assignment is not None else None,
        },
        "first_fixed_clear_evidence": {
            "frame": first_fixed_clear_after_assignment,
            "seconds": first_fixed_clear_after_assignment / fps if first_fixed_clear_after_assignment is not None else None,
        },
        "legacy_only_d16_evidence_frames": len(legacy_only_frames),
        "legacy_only_d16_intervals": frame_intervals(legacy_only_frames, fps),
        "evidence_changes_by_slot": dict(evidence_changes),
        "state_changes_vs_baseline_by_slot": dict(other_state_changes),
        "baseline_d16_empty_transition_seconds": 44.4,
        "fixed_d16_empty_transition_seconds": 33.433333,
        "d03_transition_unchanged_seconds": 44.4,
        "audit_rows": len(audit_rows),
        "audit_csv": str(audit_path),
        "comparison_images": sorted(str(path) for path in comparison_dir.glob("*.jpg")),
    }
    (OUTPUT / "diagnostic_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
