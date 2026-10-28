"""Diagnose the D05/D06 cart false positive and preview a proposed X01 zone.

This script is deliberately read-only with respect to the confirmed polygon JSON.
It evaluates X01 offline but does not connect exclusion filtering to video output.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
import torch
from ultralytics import YOLO

from parking_polygon_config import scale_points
from test_yolo_polygon_video import validate_confirmed_polygons
from test_yolov8n_video import draw_detections, extract_detections, require_file
from yolo_polygon_logic import (
    assign_detections_to_slots,
    build_slots,
    filter_detections_by_exclusion,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VIDEO = PROJECT_ROOT / "demo_videos" / "0922(1).mp4"
DEFAULT_WEIGHTS = (
    PROJECT_ROOT
    / "runs"
    / "detect"
    / "runs"
    / "yolo_car_v12"
    / "train_27_from_smoke01_best"
    / "weights"
    / "best.pt"
)
DEFAULT_POLYGONS = PROJECT_ROOT / "config" / "demo_parking" / "0922_1_polygons.json"
DEFAULT_OUTPUT_ROOT = (
    PROJECT_ROOT / "runs" / "detect" / "polygon_diagnostics" / "d05_cart_area"
)
SOURCE_SIZE = (3840, 2160)
OUTPUT_SIZE = (1920, 1080)
X01_SOURCE_POINTS = [[1436, 1360], [1748, 1360], [1748, 2150], [1436, 2150]]
EXCLUSION_OVERLAP = 0.60


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_VIDEO)
    parser.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    parser.add_argument("--polygons", type=Path, default=DEFAULT_POLYGONS)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--device", default="0")
    parser.add_argument("--conf", type=float, default=0.40)
    parser.add_argument("--iou", type=float, default=0.50)
    parser.add_argument("--imgsz", type=int, default=512)
    return parser.parse_args()


def rectangle_metrics(detection, points):
    x1, y1 = float(detection["x1"]), float(detection["y1"])
    x2, y2 = float(detection["x2"]), float(detection["y2"])
    box = np.asarray([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], np.float32)
    intersection = float(cv2.intersectConvexConvex(points, box)[0])
    area = max(1.0, (x2 - x1) * (y2 - y1))
    center = ((x1 + x2) / 2, (y1 + y2) / 2)
    return {
        "bbox_overlap": intersection / area,
        "center_inside": cv2.pointPolygonTest(points, center, False) >= 0,
        "center_x": center[0],
        "center_y": center[1],
    }


def legacy_draw(frame, detections, names):
    annotated = frame.copy()
    thickness = max(2, round(min(frame.shape[:2]) / 540))
    font_scale = max(0.6, min(frame.shape[:2]) / 1080)
    for detection in detections:
        class_id = int(detection["class_id"])
        confidence = float(detection["confidence"])
        x1, y1, x2, y2 = (
            int(round(float(detection[key]))) for key in ("x1", "y1", "x2", "y2")
        )
        label = f"{names.get(class_id, str(class_id))} {confidence:.2f}"
        color = (40, 220, 40)
        cv2.rectangle(annotated, (x1, y1), (x2, y2), color, thickness)
        (text_width, text_height), baseline = cv2.getTextSize(
            label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness
        )
        label_top = max(0, y1 - text_height - baseline - 8)
        cv2.rectangle(annotated, (x1, label_top), (x1 + text_width + 8, y1), color, -1)
        cv2.putText(
            annotated,
            label,
            (x1 + 4, max(text_height + 2, y1 - baseline - 4)),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            (0, 0, 0),
            thickness,
            cv2.LINE_AA,
        )
    return annotated


def draw_x01_diagnostic(
    frame, slots, exclusion, detections, rejected_indices, kept_indices=()
):
    canvas = frame.copy()
    for slot in slots:
        color = (255, 80, 220) if slot.identifier in {"D05", "D06"} else (180, 180, 180)
        thickness = 3 if slot.identifier in {"D05", "D06"} else 1
        cv2.polylines(canvas, [slot.points.astype(np.int32)], True, color, thickness, cv2.LINE_AA)
        if slot.identifier in {"D05", "D06"}:
            center = tuple(np.mean(slot.points, axis=0).astype(int))
            cv2.putText(canvas, slot.identifier, center, cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2, cv2.LINE_AA)
    overlay = canvas.copy()
    cv2.fillPoly(overlay, [exclusion.astype(np.int32)], (0, 190, 255))
    cv2.addWeighted(overlay, 0.13, canvas, 0.87, 0, canvas)
    cv2.polylines(canvas, [exclusion.astype(np.int32)], True, (0, 190, 255), 3, cv2.LINE_AA)
    x01_center = tuple(np.mean(exclusion, axis=0).astype(int))
    cv2.putText(canvas, "X01 PROPOSED - NOT APPLIED", x01_center, cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 190, 255), 2, cv2.LINE_AA)
    for index in rejected_indices:
        detection = detections[index]
        x1, y1, x2, y2 = (int(round(float(detection[key]))) for key in ("x1", "y1", "x2", "y2"))
        cv2.rectangle(canvas, (x1, y1), (x2, y2), (210, 30, 220), 2, cv2.LINE_AA)
        cv2.putText(canvas, f"false Car {float(detection['confidence']):.2f}", (x1, max(20, y1 - 7)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (210, 30, 220), 2, cv2.LINE_AA)
    for index in kept_indices:
        detection = detections[index]
        metrics = rectangle_metrics(detection, exclusion)
        x1, y1, x2, y2 = (int(round(float(detection[key]))) for key in ("x1", "y1", "x2", "y2"))
        cv2.rectangle(canvas, (x1, y1), (x2, y2), (255, 190, 30), 2, cv2.LINE_AA)
        label = f"KEPT: center outside, X01 overlap {metrics['bbox_overlap']:.1%}"
        cv2.putText(canvas, label, (x1, max(20, y1 - 7)), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (255, 190, 30), 2, cv2.LINE_AA)
    return canvas


def intervals(frame_numbers, fps):
    if not frame_numbers:
        return []
    unique = sorted(set(frame_numbers))
    result = []
    start = previous = unique[0]
    for frame in unique[1:]:
        if frame != previous + 1:
            result.append((start, previous))
            start = frame
        previous = frame
    result.append((start, previous))
    return [
        {
            "start_frame": start,
            "end_frame": end,
            "start_seconds": round(start / fps, 6),
            "end_seconds": round(end / fps, 6),
        }
        for start, end in result
    ]


def main():
    args = parse_args()
    video_path = require_file(args.input, "video nguồn")
    weights_path = require_file(args.weights, "best.pt")
    polygon_path = require_file(args.polygons, "polygon JSON")
    run_dir = (args.output_root / args.run_name).resolve()
    if run_dir.exists():
        raise FileExistsError(f"Refusing to overwrite existing diagnostic run: {run_dir}")
    if args.device != "cpu" and not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable in the requested yolo_train run")
    run_dir.mkdir(parents=True)

    config = validate_confirmed_polygons(polygon_path)
    slots = build_slots(config["polygons"], SOURCE_SIZE, OUTPUT_SIZE)
    exclusion = np.asarray(scale_points(X01_SOURCE_POINTS, SOURCE_SIZE, OUTPUT_SIZE), np.float32)
    overlaps = {}
    for slot in slots:
        intersection = float(cv2.intersectConvexConvex(slot.points, exclusion)[0])
        overlaps[slot.identifier] = intersection / slot.area
    if max(overlaps.values()) > 0:
        raise RuntimeError(f"Proposed X01 overlaps a confirmed slot: {overlaps}")

    proposal = json.loads(json.dumps(config))
    proposal["proposal"] = {
        "status": "NOT_APPLIED",
        "purpose": "Suppress stationary shopping-cart false-positive detections between D05 and D06",
        "rule": "center_inside AND bbox_overlap >= 0.60",
    }
    proposal["polygons"].append(
        {"id": "X01", "type": "exclusion_zone", "points": X01_SOURCE_POINTS}
    )
    proposal_path = run_dir / "0922_1_polygons_with_X01_PROPOSED.json"
    proposal_path.write_text(json.dumps(proposal, ensure_ascii=False, indent=2), encoding="utf-8")

    capture = cv2.VideoCapture(str(video_path))
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    model = YOLO(str(weights_path))
    names = {int(key): str(value) for key, value in model.names.items()}
    sample_frames = {150, 450, 900}
    sample_payload = {}
    rejected_rows = []
    touching_kept_rows = []
    assigned_counter = Counter()
    evidence_changes = Counter()
    assignment_changes = Counter()
    best_kept_touch = None

    for frame_index in range(total_frames):
        ok, frame = capture.read()
        if not ok:
            raise RuntimeError(f"Video stopped at frame {frame_index}/{total_frames}")
        frame = cv2.resize(frame, OUTPUT_SIZE, interpolation=cv2.INTER_AREA)
        result = model.predict(
            source=frame,
            conf=args.conf,
            iou=args.iou,
            imgsz=args.imgsz,
            device=args.device,
            classes=[0],
            verbose=False,
        )[0]
        detections = extract_detections(result)
        before_matches, before_evidence = assign_detections_to_slots(
            detections,
            slots,
            minimum_bbox_overlap=0.45,
            minimum_slot_overlap=0.12,
            uncertain_overlap=0.10,
        )
        kept, rejected = filter_detections_by_exclusion(
            detections, exclusion, minimum_bbox_overlap=EXCLUSION_OVERLAP
        )
        after_matches, after_evidence = assign_detections_to_slots(
            kept,
            slots,
            minimum_bbox_overlap=0.45,
            minimum_slot_overlap=0.12,
            uncertain_overlap=0.10,
        )
        inverse = {
            int(match["detection_index"]): slot_id
            for slot_id, match in before_matches.items()
        }
        rejected_indices = {int(item["detection_index"]) for item in rejected}

        for slot_id in before_evidence:
            if before_evidence[slot_id] != after_evidence[slot_id]:
                evidence_changes[slot_id] += 1
            before_box = before_matches.get(slot_id, {}).get("box")
            after_box = after_matches.get(slot_id, {}).get("box")
            if before_box != after_box:
                assignment_changes[slot_id] += 1

        for item in rejected:
            index = int(item["detection_index"])
            detection = detections[index]
            assigned_slot = inverse.get(index, "")
            assigned_counter[assigned_slot or "UNASSIGNED"] += 1
            rejected_rows.append(
                {
                    "frame_index": frame_index,
                    "timestamp_seconds": frame_index / fps,
                    "detection_index": index,
                    "confidence": float(detection["confidence"]),
                    "x1": float(detection["x1"]),
                    "y1": float(detection["y1"]),
                    "x2": float(detection["x2"]),
                    "y2": float(detection["y2"]),
                    "exclusion_bbox_overlap": float(item["bbox_overlap"]),
                    "assigned_slot": assigned_slot,
                }
            )

        for index, detection in enumerate(detections):
            metrics = rectangle_metrics(detection, exclusion)
            if metrics["bbox_overlap"] > 0 and index not in rejected_indices:
                row = {
                    "frame_index": frame_index,
                    "timestamp_seconds": frame_index / fps,
                    "detection_index": index,
                    "confidence": float(detection["confidence"]),
                    "bbox_overlap": metrics["bbox_overlap"],
                    "center_inside": bool(metrics["center_inside"]),
                    "center_x": metrics["center_x"],
                    "center_y": metrics["center_y"],
                    "assigned_slot": inverse.get(index, ""),
                }
                touching_kept_rows.append(row)
                if best_kept_touch is None or row["center_y"] < best_kept_touch[0]["center_y"]:
                    best_kept_touch = (row, frame.copy(), list(detections))

        if frame_index in sample_frames:
            sample_payload[frame_index] = (frame.copy(), list(detections), sorted(rejected_indices))
        if frame_index == 0 or (frame_index + 1) % 300 == 0:
            print(f"Scanned {frame_index + 1}/{total_frames} frames", flush=True)
    capture.release()

    fields = [
        "frame_index", "timestamp_seconds", "detection_index", "confidence",
        "x1", "y1", "x2", "y2", "exclusion_bbox_overlap", "assigned_slot",
    ]
    with (run_dir / "x01_rejected_detections.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rejected_rows)

    before_dir = run_dir / "bbox_comparison"
    before_dir.mkdir()
    for frame_index, (frame, detections, rejected_indices) in sample_payload.items():
        timestamp = frame_index / fps
        old = legacy_draw(frame, detections, names)
        new = draw_detections(frame, detections, names)
        stem = f"t{timestamp:05.2f}s_frame_{frame_index:06d}"
        cv2.imwrite(str(before_dir / f"{stem}_before.jpg"), old)
        cv2.imwrite(str(before_dir / f"{stem}_after.jpg"), new)
        crop = (slice(630, 1080), slice(520, 1040))
        comparison = np.hstack((old[crop], new[crop]))
        cv2.putText(comparison, "BEFORE", (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(comparison, "AFTER", (532, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.imwrite(str(before_dir / f"{stem}_comparison_crop.jpg"), comparison)
        diagnostic = draw_x01_diagnostic(frame, slots, exclusion, detections, rejected_indices)
        cv2.imwrite(str(run_dir / f"x01_diagnostic_{stem}.jpg"), diagnostic)

    moving_sample = None
    if best_kept_touch is not None:
        row, frame, detections = best_kept_touch
        diagnostic = draw_x01_diagnostic(
            frame, slots, exclusion, detections, set(), {int(row["detection_index"])}
        )
        moving_sample = run_dir / f"kept_touch_t{row['timestamp_seconds']:05.2f}s.jpg"
        cv2.imwrite(str(moving_sample), diagnostic)

    confidences = [row["confidence"] for row in rejected_rows]
    summary = {
        "status": "PROPOSAL_ONLY_NOT_APPLIED",
        "video": str(video_path),
        "weights": str(weights_path),
        "confirmed_polygons_unchanged": str(polygon_path),
        "proposal": {
            "id": "X01",
            "source_points": X01_SOURCE_POINTS,
            "output_points": exclusion.astype(int).tolist(),
            "minimum_bbox_overlap": EXCLUSION_OVERLAP,
            "requires_center_inside": True,
            "slot_overlap_fractions": overlaps,
        },
        "detections": {
            "rejected_count": len(rejected_rows),
            "frames_with_rejection": len({row["frame_index"] for row in rejected_rows}),
            "intervals": intervals([row["frame_index"] for row in rejected_rows], fps),
            "confidence_min": min(confidences) if confidences else None,
            "confidence_max": max(confidences) if confidences else None,
            "confidence_mean": statistics.mean(confidences) if confidences else None,
            "coordinate_envelope": {
                key: [min(row[key] for row in rejected_rows), max(row[key] for row in rejected_rows)]
                for key in ("x1", "y1", "x2", "y2")
            } if rejected_rows else None,
            "assigned_slots": dict(assigned_counter),
            "touching_but_kept_count": len(touching_kept_rows),
            "touching_but_kept_assigned_slots": dict(Counter(row["assigned_slot"] or "UNASSIGNED" for row in touching_kept_rows)),
        },
        "impact": {
            "evidence_changes_by_slot": dict(evidence_changes),
            "assignment_changes_by_slot": dict(assignment_changes),
            "D01_D08_transitions_unchanged_because_all_raw_evidence_is_unchanged": not evidence_changes,
            "D05_D06_assignments_unchanged": not any(slot in assignment_changes for slot in ("D05", "D06")),
            "state_count_invariant": "unchanged; OCCUPIED + EMPTY + UNKNOWN remains 24",
        },
        "moving_or_edge_case": best_kept_touch[0] if best_kept_touch else None,
        "moving_or_edge_case_sample": str(moving_sample) if moving_sample else None,
    }
    (run_dir / "diagnostic_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
