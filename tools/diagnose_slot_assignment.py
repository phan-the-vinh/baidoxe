"""Audit detector boxes intersecting one parking polygon without rerunning YOLO."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import cv2
import numpy as np

from parking_polygon_config import load_config
from yolo_polygon_logic import _intersection_metrics, build_slots, is_border_truncated


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detections", required=True, type=Path)
    parser.add_argument("--video", type=Path, default=PROJECT_ROOT / "demo_videos" / "0922(1).mp4")
    parser.add_argument("--polygons", type=Path, default=PROJECT_ROOT / "config" / "demo_parking" / "0922_1_polygons.json")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--slot", default="D13")
    parser.add_argument("--start", type=float, default=3.0)
    parser.add_argument("--end", type=float, default=5.0)
    parser.add_argument("--sample", type=float, default=3.8)
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--bbox-overlap", type=float, default=0.45)
    parser.add_argument("--slot-overlap", type=float, default=0.12)
    parser.add_argument("--minimum-axis-elongation", type=float, default=1.25)
    parser.add_argument("--core-horizontal-inset", type=float, default=0.15)
    parser.add_argument("--core-top-inset", type=float, default=0.10)
    parser.add_argument("--core-bottom-inset", type=float, default=0.25)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(args.polygons.resolve())
    source_size = (
        int(config["coordinate_space"]["width"]),
        int(config["coordinate_space"]["height"]),
    )
    capture = cv2.VideoCapture(str(args.video.resolve()))
    if not capture.isOpened():
        raise RuntimeError(f"Cannot open video: {args.video.resolve()}")
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    source_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    source_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    target_size = (args.width, round(source_height * args.width / source_width))
    slots = build_slots(config["polygons"], source_size, target_size)
    slot = next((item for item in slots if item.identifier == args.slot), None)
    if slot is None:
        raise ValueError(f"Unknown slot: {args.slot}")

    first_frame = round(args.start * fps)
    last_frame = round(args.end * fps)
    sample_frame = round(args.sample * fps)
    rows: list[dict[str, object]] = []
    sample_boxes: list[dict[str, object]] = []
    with args.detections.resolve().open(newline="", encoding="utf-8") as source:
        for row in csv.DictReader(source):
            frame_index = int(row["frame_index"])
            if frame_index < first_frame or frame_index > last_frame:
                continue
            detection = {
                "confidence": float(row["confidence"]),
                "x1": float(row["x1"]),
                "y1": float(row["y1"]),
                "x2": float(row["x2"]),
                "y2": float(row["y2"]),
            }
            metrics = _intersection_metrics(
                detection,
                slot,
                core_horizontal_inset=args.core_horizontal_inset,
                core_top_inset=args.core_top_inset,
                core_bottom_inset=args.core_bottom_inset,
            )
            bbox_area = max(
                1.0,
                (detection["x2"] - detection["x1"])
                * (detection["y2"] - detection["y1"]),
            )
            intersection_area = float(metrics["bbox_overlap"]) * bbox_area
            if intersection_area <= 0:
                continue
            old_eligible = (
                bool(metrics["center_inside"])
                and float(metrics["bbox_overlap"]) >= args.bbox_overlap
                and float(metrics["slot_overlap"]) >= args.slot_overlap
            )
            core_eligible = (
                bool(metrics["core_center_inside"])
                and float(metrics["core_overlap"]) >= args.bbox_overlap
                and float(metrics["core_slot_overlap"]) >= args.slot_overlap
            )
            border_truncated = is_border_truncated(detection, target_size)
            fixed_eligible = (
                old_eligible
                and float(metrics["axis_elongation"])
                >= args.minimum_axis_elongation
                and (not border_truncated or core_eligible)
            )
            core_x1, core_y1, core_x2, core_y2 = metrics["core_box"]
            item = {
                "frame_index": frame_index,
                "timestamp_seconds": f"{frame_index / fps:.6f}",
                "detection_index": int(row["detection_index"]),
                "confidence": f"{detection['confidence']:.6f}",
                "x1": f"{detection['x1']:.3f}",
                "y1": f"{detection['y1']:.3f}",
                "x2": f"{detection['x2']:.3f}",
                "y2": f"{detection['y2']:.3f}",
                "bbox_area": f"{bbox_area:.3f}",
                "intersection_area": f"{intersection_area:.3f}",
                "intersection_over_polygon": f"{float(metrics['slot_overlap']):.6f}",
                "intersection_over_bbox": f"{float(metrics['bbox_overlap']):.6f}",
                "core_x1": f"{float(core_x1):.3f}",
                "core_y1": f"{float(core_y1):.3f}",
                "core_x2": f"{float(core_x2):.3f}",
                "core_y2": f"{float(core_y2):.3f}",
                "core_intersection_area": f"{float(metrics['core_intersection_area']):.3f}",
                "intersection_over_core": f"{float(metrics['core_overlap']):.6f}",
                "core_intersection_over_polygon": f"{float(metrics['core_slot_overlap']):.6f}",
                "center_inside": bool(metrics["center_inside"]),
                "core_center_inside": bool(metrics["core_center_inside"]),
                "is_border_truncated": border_truncated,
                "axis_elongation": f"{float(metrics['axis_elongation']):.6f}",
                "eligible_before_fix": old_eligible,
                "eligible_by_core": core_eligible,
                "eligible_after_fix": fixed_eligible,
            }
            rows.append(item)
            if frame_index == sample_frame:
                sample_boxes.append({"detection": detection, "metrics": metrics})

    args.output.mkdir(parents=True, exist_ok=False)
    fields = list(rows[0]) if rows else ["frame_index"]
    with (args.output / "detections_intersecting_slot.csv").open(
        "w", newline="", encoding="utf-8"
    ) as target:
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    capture.set(cv2.CAP_PROP_POS_FRAMES, sample_frame)
    readable, frame = capture.read()
    capture.release()
    if not readable or frame is None:
        raise RuntimeError(f"Cannot read sample frame {sample_frame}")
    frame = cv2.resize(frame, target_size, interpolation=cv2.INTER_AREA)
    original = frame.copy()
    cv2.polylines(frame, [slot.points.astype("int32")], True, (255, 0, 255), 3)
    for item in sample_boxes:
        detection = item["detection"]
        metrics = item["metrics"]
        x1, y1, x2, y2 = (
            round(float(detection[key])) for key in ("x1", "y1", "x2", "y2")
        )
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 2)
        core_x1, core_y1, core_x2, core_y2 = (
            round(float(value)) for value in metrics["core_box"]
        )
        cv2.rectangle(
            frame,
            (core_x1, core_y1),
            (core_x2, core_y2),
            (255, 255, 0),
            2,
        )
        core_polygon = np.asarray(
            [
                [core_x1, core_y1],
                [core_x2, core_y1],
                [core_x2, core_y2],
                [core_x1, core_y2],
            ],
            dtype=np.float32,
        )
        _, intersection = cv2.intersectConvexConvex(slot.points, core_polygon)
        if intersection is not None and len(intersection) >= 3:
            overlay = frame.copy()
            cv2.fillPoly(overlay, [intersection.astype(np.int32)], (0, 165, 255))
            frame = cv2.addWeighted(overlay, 0.35, frame, 0.65, 0)
        label = (
            f"conf={float(detection['confidence']):.3f} "
            f"box={float(metrics['bbox_overlap']):.3f} "
            f"core={float(metrics['core_overlap']):.3f} "
            f"coreSlot={float(metrics['core_slot_overlap']):.3f} "
            f"axis={float(metrics['axis_elongation']):.3f}"
        )
        cv2.putText(frame, label, (max(0, x1), max(18, y2 + 22)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2, cv2.LINE_AA)
    cv2.imwrite(str(args.output / f"frame_{sample_frame:06d}_original.jpg"), original)
    cv2.imwrite(str(args.output / f"frame_{sample_frame:06d}_{args.slot}_overlay.jpg"), frame)
    print(f"audited_rows={len(rows)} sample_boxes={len(sample_boxes)} output={args.output.resolve()}")


if __name__ == "__main__":
    main()
