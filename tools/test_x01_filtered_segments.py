"""Create short before/after previews for the approved X01 trial only."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import torch
from ultralytics import YOLO

from diagnose_d05_cart_false_positive import legacy_draw, rectangle_metrics
from parking_polygon_config import load_config, scale_points
from test_yolo_polygon_video import draw_slot_fills, draw_slot_outlines
from test_yolov8n_video import draw_detections, extract_detections, require_file
from transcode_demo_video_h264 import make_faststart, top_level_boxes
from yolo_polygon_logic import (
    assign_detections_to_slots,
    build_slots,
    filter_detections_by_exclusion,
)


ROOT = Path(__file__).resolve().parents[1]
VIDEO = ROOT / "demo_videos" / "0922(1).mp4"
WEIGHTS = ROOT / "runs" / "detect" / "runs" / "yolo_car_v12" / "train_27_from_smoke01_best" / "weights" / "best.pt"
PROPOSAL = ROOT / "runs" / "detect" / "polygon_diagnostics" / "d05_cart_area" / "phase4a_x01_proposal_conf040" / "0922_1_polygons_with_X01_PROPOSED.json"
BASELINE_STATES = ROOT / "runs" / "detect" / "polygon_tests" / "yolo_car_v12_best" / "full_video_phase3_conf040" / "slot_states.csv"
OUTPUT_ROOT = ROOT / "runs" / "detect" / "polygon_tests" / "yolo_car_v12_best" / "x01_trials"
EXPECTED_X01 = [[1436, 1360], [1748, 1360], [1748, 2150], [1436, 2150]]
WINDOWS = [(4.0, 6.0), (14.0, 16.0), (20.466667, 22.466667), (29.0, 31.0)]
SAMPLE_TIMES = [5.0, 15.0, 21.466667, 30.0]
TRANSITION_AUDIT_FRAMES = [98, 99, 371, 372, 385, 386, 720, 721]


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--device", default="0")
    return parser.parse_args()


def open_h264_writer(path, fps, size):
    writer = cv2.VideoWriter(
        str(path), cv2.CAP_MSMF, cv2.VideoWriter_fourcc(*"avc1"), fps, size
    )
    if not writer.isOpened():
        raise RuntimeError("Windows Media Foundation H.264 writer is unavailable")
    return writer


def finish_faststart(encoded_path, output_path):
    make_faststart(encoded_path, output_path)
    if encoded_path.exists():
        encoded_path.unlink()


def load_states(path):
    with path.open("r", newline="", encoding="utf-8-sig") as source:
        rows = list(csv.DictReader(source))
    slot_ids = [f"D{number:02d}" for number in range(1, 25)]
    return {
        int(row["frame_index"]): {slot_id: row[slot_id] for slot_id in slot_ids}
        for row in rows
    }


def state_views(states):
    return {slot_id: SimpleNamespace(state=state) for slot_id, state in states.items()}


def add_x01_outline(frame, exclusion):
    cv2.polylines(frame, [exclusion.astype(np.int32)], True, (0, 190, 255), 2, cv2.LINE_AA)
    point = tuple(exclusion[0].astype(int))
    cv2.putText(frame, "X01 TEST", (point[0] + 4, point[1] + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (0, 190, 255), 1, cv2.LINE_AA)
    return frame


def render(frame, detections, slots, states, timestamp, *, legacy=False, x01=None):
    canvas = draw_slot_fills(frame, slots, states)
    canvas = legacy_draw(canvas, detections, {0: "Car"}) if legacy else draw_detections(canvas, detections, {0: "Car"})
    canvas = draw_slot_outlines(canvas, slots, states, timestamp)
    return add_x01_outline(canvas, x01) if x01 is not None else canvas


def panel(frame, title):
    resized = cv2.resize(frame, (640, 360), interpolation=cv2.INTER_AREA)
    cv2.rectangle(resized, (0, 0), (640, 30), (15, 20, 20), -1)
    cv2.putText(resized, title, (10, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
    return resized


def same_assignments(before, after):
    if set(before) != set(after):
        return False
    for slot_id in before:
        if before[slot_id]["box"] != after[slot_id]["box"]:
            return False
    return True


def main():
    args = parse_args()
    video_path = require_file(VIDEO, "video nguồn")
    weights_path = require_file(WEIGHTS, "best.pt")
    proposal_path = require_file(PROPOSAL, "cấu hình X01 đề xuất")
    baseline_path = require_file(BASELINE_STATES, "slot_states.csv")
    run_dir = (OUTPUT_ROOT / args.run_name).resolve()
    if run_dir.exists():
        raise FileExistsError(f"Refusing to overwrite trial directory: {run_dir}")
    if args.device != "cpu" and not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable")
    run_dir.mkdir(parents=True)
    samples_dir = run_dir / "samples"
    samples_dir.mkdir()

    proposal = load_config(proposal_path)
    parking = [item for item in proposal["polygons"] if item["type"] == "parking_space"]
    exclusions = [item for item in proposal["polygons"] if item["type"] == "exclusion_zone"]
    if [item["id"] for item in parking] != [f"D{number:02d}" for number in range(1, 25)]:
        raise RuntimeError("Proposal does not preserve D01-D24")
    if len(exclusions) != 1 or exclusions[0]["id"] != "X01" or exclusions[0]["points"] != EXPECTED_X01:
        raise RuntimeError("Proposal X01 coordinates differ from the approved coordinates")

    slots = build_slots(parking, (3840, 2160), (1920, 1080))
    exclusion = np.asarray(scale_points(EXPECTED_X01, (3840, 2160), (1920, 1080)), np.float32)
    baseline = load_states(baseline_path)
    capture = cv2.VideoCapture(str(video_path))
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    model = YOLO(str(weights_path))

    filtered_tmp = run_dir / "x01_filtered_preview.encoding.tmp.mp4"
    comparison_tmp = run_dir / "x01_comparison_triptych.encoding.tmp.mp4"
    filtered_output = run_dir / "x01_filtered_preview_h264.mp4"
    comparison_output = run_dir / "x01_comparison_triptych_h264.mp4"
    filtered_writer = open_h264_writer(filtered_tmp, fps, (1920, 1080))
    comparison_writer = open_h264_writer(comparison_tmp, fps, (1920, 360))

    video_frames = []
    for start, end in WINDOWS:
        video_frames.extend(range(round(start * fps), round(end * fps)))
    sample_frames = {round(value * fps): value for value in SAMPLE_TIMES}
    removed_rows = []
    per_slot_evidence_changes = {slot.identifier: 0 for slot in slots}
    per_slot_assignment_changes = {slot.identifier: 0 for slot in slots}
    uniqueness_ok = True
    count_invariant_ok = True
    real_vehicle_check = None

    def infer(frame_index):
        capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, source_frame = capture.read()
        if not ok:
            raise RuntimeError(f"Cannot read source frame {frame_index}")
        frame = cv2.resize(source_frame, (1920, 1080), interpolation=cv2.INTER_AREA)
        result = model.predict(source=frame, conf=0.40, iou=0.50, imgsz=512, device=args.device, classes=[0], verbose=False)[0]
        return frame, extract_detections(result)

    def evaluate(frame_index, frame, detections):
        nonlocal uniqueness_ok, count_invariant_ok, real_vehicle_check
        before_matches, before_evidence = assign_detections_to_slots(
            detections, slots, minimum_bbox_overlap=0.45, minimum_slot_overlap=0.12, uncertain_overlap=0.10
        )
        filtered, rejected = filter_detections_by_exclusion(
            detections, exclusion, minimum_bbox_overlap=0.60
        )
        after_matches, after_evidence = assign_detections_to_slots(
            filtered, slots, minimum_bbox_overlap=0.45, minimum_slot_overlap=0.12, uncertain_overlap=0.10
        )
        inverse = {int(match["detection_index"]): slot_id for slot_id, match in before_matches.items()}
        indices = [int(match["detection_index"]) for match in after_matches.values()]
        uniqueness_ok = uniqueness_ok and len(indices) == len(set(indices))
        for slot in slots:
            slot_id = slot.identifier
            if before_evidence[slot_id] != after_evidence[slot_id]:
                per_slot_evidence_changes[slot_id] += 1
            if not same_assignments(
                {slot_id: before_matches[slot_id]} if slot_id in before_matches else {},
                {slot_id: after_matches[slot_id]} if slot_id in after_matches else {},
            ):
                per_slot_assignment_changes[slot_id] += 1
        states = baseline[frame_index]
        count_invariant_ok = count_invariant_ok and len(states) == 24 and sum(
            state in {"OCCUPIED", "EMPTY", "UNKNOWN"} for state in states.values()
        ) == 24
        for item in rejected:
            index = int(item["detection_index"])
            detection = detections[index]
            removed_rows.append({
                "frame_index": frame_index,
                "timestamp_seconds": frame_index / fps,
                "detection_index": index,
                "confidence": float(detection["confidence"]),
                "x1": float(detection["x1"]), "y1": float(detection["y1"]),
                "x2": float(detection["x2"]), "y2": float(detection["y2"]),
                "exclusion_bbox_overlap": float(item["bbox_overlap"]),
                "assigned_slot": inverse.get(index, ""),
            })
        if frame_index == 644:
            rejected_indices = {int(item["detection_index"]) for item in rejected}
            candidates = []
            for index, detection in enumerate(detections):
                metrics = rectangle_metrics(detection, exclusion)
                area = (float(detection["x2"]) - float(detection["x1"])) * (float(detection["y2"]) - float(detection["y1"]))
                if metrics["bbox_overlap"] > 0 and not metrics["center_inside"] and index not in rejected_indices:
                    candidates.append((area, index, detection, metrics))
            if candidates:
                _area, index, detection, metrics = max(candidates)
                real_vehicle_check = {
                    "frame_index": frame_index,
                    "timestamp_seconds": frame_index / fps,
                    "detection_index": index,
                    "confidence": float(detection["confidence"]),
                    "bbox_overlap": float(metrics["bbox_overlap"]),
                    "center_inside": bool(metrics["center_inside"]),
                    "filtered": False,
                }
        return filtered, state_views(states)

    try:
        for position, frame_index in enumerate(video_frames):
            frame, detections = infer(frame_index)
            filtered, states = evaluate(frame_index, frame, detections)
            timestamp = frame_index / fps
            old_view = render(frame, detections, slots, states, timestamp, legacy=True)
            new_view = render(frame, detections, slots, states, timestamp)
            filtered_view = render(frame, filtered, slots, states, timestamp, x01=exclusion)
            filtered_writer.write(filtered_view)
            comparison_writer.write(np.hstack((
                panel(old_view, "OLD YOLO"),
                panel(new_view, "NEW CYAN"),
                panel(filtered_view, "NEW + X01 FILTER"),
            )))
            if frame_index in sample_frames:
                stem = f"t{sample_frames[frame_index]:05.2f}s_frame_{frame_index:06d}"
                cv2.imwrite(str(samples_dir / f"{stem}_old.jpg"), old_view)
                cv2.imwrite(str(samples_dir / f"{stem}_new.jpg"), new_view)
                cv2.imwrite(str(samples_dir / f"{stem}_x01_filtered.jpg"), filtered_view)
                cv2.imwrite(str(samples_dir / f"{stem}_comparison.jpg"), np.hstack((
                    panel(old_view, "OLD YOLO"), panel(new_view, "NEW CYAN"), panel(filtered_view, "NEW + X01 FILTER")
                )))
            if (position + 1) % 60 == 0:
                print(f"Rendered {position + 1}/{len(video_frames)} preview frames", flush=True)

        preview_removed_count = len(removed_rows)
        audited = {}
        for frame_index in TRANSITION_AUDIT_FRAMES:
            frame, detections = infer(frame_index)
            _filtered, _states = evaluate(frame_index, frame, detections)
            audited[str(frame_index)] = baseline[frame_index]
    finally:
        capture.release()
        filtered_writer.release()
        comparison_writer.release()

    finish_faststart(filtered_tmp, filtered_output)
    finish_faststart(comparison_tmp, comparison_output)

    fields = ["frame_index", "timestamp_seconds", "detection_index", "confidence", "x1", "y1", "x2", "y2", "exclusion_bbox_overlap", "assigned_slot"]
    with (run_dir / "removed_detections.csv").open("w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader(); writer.writerows(removed_rows)

    unexpected_evidence = {key: value for key, value in per_slot_evidence_changes.items() if value}
    unexpected_assignments = {key: value for key, value in per_slot_assignment_changes.items() if value}
    removed_by_window = {
        f"{start:.2f}-{end:.2f}": sum(
            round(start * fps) <= int(row["frame_index"]) < round(end * fps)
            for row in removed_rows
        )
        for start, end in WINDOWS
    }
    if unexpected_evidence or unexpected_assignments:
        raise RuntimeError(f"X01 changed valid slot logic: {unexpected_evidence}, {unexpected_assignments}")
    if any(row["assigned_slot"] for row in removed_rows):
        raise RuntimeError("X01 removed a detection assigned to a parking slot")
    if not uniqueness_ok or not count_invariant_ok or real_vehicle_check is None:
        raise RuntimeError("Trial safety validation failed")

    summary = {
        "status": "SHORT_TRIAL_ONLY",
        "windows_seconds": WINDOWS,
        "processed_preview_frames": len(video_frames),
        "transition_audit_frames": TRANSITION_AUDIT_FRAMES,
        "removed_detections_in_preview": preview_removed_count,
        "removed_detections_by_window": removed_by_window,
        "removed_detections_in_transition_audit": len(removed_rows) - preview_removed_count,
        "removed_detections_total_evaluated": len(removed_rows),
        "removed_assigned_detections": 0,
        "real_vehicle_near_x01": real_vehicle_check,
        "state_changes_by_slot": unexpected_evidence,
        "assignment_changes_by_slot": unexpected_assignments,
        "D05_D06_unchanged": True,
        "D01_D08_transition_frames_unchanged": True,
        "one_detection_assigned_to_at_most_one_slot": uniqueness_ok,
        "state_count_invariant": count_invariant_ok,
        "videos": {
            "filtered": str(filtered_output),
            "comparison": str(comparison_output),
        },
        "video_boxes": {
            path.name: [box[0].decode("ascii", "replace") for box in top_level_boxes(path)]
            for path in (filtered_output, comparison_output)
        },
    }
    (run_dir / "trial_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
