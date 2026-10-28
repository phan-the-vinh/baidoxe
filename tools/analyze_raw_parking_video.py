"""Analyze an immutable source video with YOLO + parking polygons.

This worker performs inference only. It writes structured analysis data and never
creates, transcodes, or annotates a video file.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import torch
from ultralytics import YOLO

from parking_polygon_config import load_config, scale_points
from yolo_polygon_logic import (
    EMPTY,
    OCCUPIED,
    UNKNOWN,
    TemporalSlotState,
    assign_detections_to_slots,
    build_slots,
    filter_detections_by_exclusion,
    seconds_to_frames,
)


APPROVED_EXCLUSION_ID = "X01"
APPROVED_EXCLUSION_POINTS = [
    [1436, 1360],
    [1748, 1360],
    [1748, 2150],
    [1436, 2150],
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--polygons", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--conf", type=float, default=0.40)
    parser.add_argument("--iou", type=float, default=0.50)
    parser.add_argument("--imgsz", type=int, default=512)
    parser.add_argument("--device", default="0")
    parser.add_argument("--analysis-width", type=int, default=1920)
    parser.add_argument("--bbox-overlap", type=float, default=0.45)
    parser.add_argument("--slot-overlap", type=float, default=0.12)
    parser.add_argument("--uncertain-overlap", type=float, default=0.10)
    parser.add_argument("--minimum-axis-elongation", type=float, default=1.25)
    parser.add_argument("--core-horizontal-inset", type=float, default=0.15)
    parser.add_argument("--core-top-inset", type=float, default=0.10)
    parser.add_argument("--core-bottom-inset", type=float, default=0.25)
    parser.add_argument("--border-margin-ratio", type=float, default=0.01)
    parser.add_argument("--exclusion-overlap", type=float, default=0.60)
    parser.add_argument("--occupied-seconds", type=float, default=0.75)
    parser.add_argument("--empty-seconds", type=float, default=1.50)
    parser.add_argument("--release-seconds", type=float, default=2.00)
    parser.add_argument("--ambiguous-seconds", type=float, default=0.25)
    return parser.parse_args()


def require_file(path: Path, description: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Missing {description}: {resolved}")
    return resolved


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_detections(result) -> list[dict[str, float | int]]:
    detections: list[dict[str, float | int]] = []
    if result.boxes is None:
        return detections
    for xyxy, confidence, class_id in zip(
        result.boxes.xyxy.cpu().numpy(),
        result.boxes.conf.cpu().numpy(),
        result.boxes.cls.cpu().numpy(),
    ):
        x1, y1, x2, y2 = xyxy.tolist()
        detections.append(
            {
                "class_id": int(class_id),
                "confidence": float(confidence),
                "x1": float(x1),
                "y1": float(y1),
                "x2": float(x2),
                "y2": float(y2),
            }
        )
    return detections


def validate_polygons(path: Path) -> dict[str, Any]:
    config = load_config(path)
    polygons = config["polygons"]
    parking = [item for item in polygons if item["type"] == "parking_space"]
    exclusions = [item for item in polygons if item["type"] == "exclusion_zone"]
    expected = [f"D{number:02d}" for number in range(1, 25)]
    if [item["id"] for item in parking] != expected:
        raise ValueError("Polygon IDs must be exactly D01-D24 in order")
    if config["coordinate_space"] != {"width": 3840, "height": 2160}:
        raise ValueError("Polygon coordinate space must be 3840x2160")
    if len(exclusions) != 1:
        raise ValueError("Exactly one X01 exclusion zone is required")
    exclusion = exclusions[0]
    if (
        exclusion["id"] != APPROVED_EXCLUSION_ID
        or exclusion["points"] != APPROVED_EXCLUSION_POINTS
    ):
        raise ValueError("X01 does not match the approved configuration")
    return config


def codec_name(capture: cv2.VideoCapture) -> str:
    code = int(capture.get(cv2.CAP_PROP_FOURCC))
    return "".join(chr((code >> (8 * index)) & 0xFF) for index in range(4)).strip()


def timeline_entry(
    frame_index: int,
    timestamp: float,
    slot_ids: list[str],
    states: dict[str, TemporalSlotState],
) -> dict[str, Any]:
    values = [states[slot_id].state for slot_id in slot_ids]
    return {
        "frame": frame_index,
        "time": round(timestamp, 6),
        "counts": {
            "occupied": values.count(OCCUPIED),
            "empty": values.count(EMPTY),
            "unknown": values.count(UNKNOWN),
        },
        "states": values,
    }


def main() -> None:
    args = parse_args()
    for name in (
        "conf",
        "iou",
        "bbox_overlap",
        "slot_overlap",
        "uncertain_overlap",
        "exclusion_overlap",
    ):
        value = float(getattr(args, name))
        if not 0 <= value <= 1:
            raise ValueError(f"--{name.replace('_', '-')} must be between 0 and 1")
    if args.analysis_width <= 0:
        raise ValueError("--analysis-width must be positive")
    if args.minimum_axis_elongation < 1:
        raise ValueError("--minimum-axis-elongation must be at least 1")
    for name in ("core_horizontal_inset", "core_top_inset", "core_bottom_inset"):
        value = float(getattr(args, name))
        if not 0 <= value < 0.5:
            raise ValueError(f"--{name.replace('_', '-')} must be between 0 and 0.5")
    if args.core_top_inset + args.core_bottom_inset >= 1:
        raise ValueError("Core top and bottom insets must sum to less than 1")
    if not 0 <= args.border_margin_ratio < 0.5:
        raise ValueError("--border-margin-ratio must be between 0 and 0.5")

    input_video = require_file(args.video, "source video")
    weights = require_file(args.model, "YOLO model")
    polygon_path = require_file(args.polygons, "polygon configuration")
    config = validate_polygons(polygon_path)
    output_dir = args.output.resolve()
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite output directory: {output_dir}")
    if args.device != "cpu" and not torch.cuda.is_available():
        raise RuntimeError("GPU requested but torch.cuda.is_available() is False")

    input_size_before = input_video.stat().st_size
    input_hash_before = sha256_file(input_video)
    capture = cv2.VideoCapture(str(input_video))
    if not capture.isOpened():
        raise RuntimeError(f"OpenCV cannot open source video: {input_video}")
    source_fps = float(capture.get(cv2.CAP_PROP_FPS))
    source_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    source_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    source_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    source_codec = codec_name(capture)
    if min(source_fps, source_frames, source_width, source_height) <= 0:
        capture.release()
        raise RuntimeError("Source video metadata is invalid")

    coordinate_size = (
        int(config["coordinate_space"]["width"]),
        int(config["coordinate_space"]["height"]),
    )
    polygon_aspect = coordinate_size[0] / coordinate_size[1]
    if abs(source_width / source_height - polygon_aspect) / polygon_aspect > 0.01:
        capture.release()
        raise ValueError(
            f"Video {source_width}x{source_height} is incompatible with "
            f"polygon space {coordinate_size[0]}x{coordinate_size[1]}"
        )

    analysis_width = args.analysis_width
    analysis_height = int(round(source_height * analysis_width / source_width))
    if analysis_height % 2:
        analysis_height += 1
    analysis_size = (analysis_width, analysis_height)
    slots = build_slots(config["polygons"], coordinate_size, analysis_size)
    if len(slots) != 24:
        capture.release()
        raise RuntimeError(f"Expected 24 parking slots, found {len(slots)}")
    slot_ids = [slot.identifier for slot in slots]
    exclusion = next(
        item for item in config["polygons"] if item["type"] == "exclusion_zone"
    )
    exclusion_points = np.asarray(
        scale_points(exclusion["points"], coordinate_size, analysis_size),
        dtype=np.float32,
    )

    occupied_frames = seconds_to_frames(args.occupied_seconds, source_fps)
    empty_frames = seconds_to_frames(args.empty_seconds, source_fps)
    release_frames = seconds_to_frames(args.release_seconds, source_fps)
    ambiguous_frames = seconds_to_frames(args.ambiguous_seconds, source_fps)
    states = {
        slot_id: TemporalSlotState(
            slot_id,
            occupied_frames=occupied_frames,
            empty_frames=empty_frames,
            release_frames=release_frames,
            ambiguous_frames=ambiguous_frames,
        )
        for slot_id in slot_ids
    }

    model = YOLO(str(weights))
    names = {int(key): str(value) for key, value in model.names.items()}
    if names.get(0) != "Car":
        capture.release()
        raise RuntimeError(f"Checkpoint class 0 is not Car: {names}")

    output_dir.mkdir(parents=True)
    states_csv = output_dir / "slot_states.csv"
    transitions_csv = output_dir / "state_transitions.csv"
    detections_csv = output_dir / "detections.csv"
    excluded_csv = output_dir / "excluded_detections.csv"
    timeline_json = output_dir / "timeline.json"
    summary_json = output_dir / "analysis_summary.json"

    state_fields = [
        "frame_index",
        "timestamp_seconds",
        "occupied_count",
        "empty_count",
        "unknown_count",
        *slot_ids,
    ]
    transition_fields = [
        "frame_index",
        "timestamp_seconds",
        "slot_id",
        "previous_state",
        "new_state",
        "evidence",
    ]
    detection_fields = [
        "frame_index",
        "timestamp_seconds",
        "detection_index",
        "confidence",
        "assigned_slot",
        "bbox_overlap",
        "slot_overlap",
        "core_overlap",
        "core_slot_overlap",
        "core_center_inside",
        "is_border_truncated",
        "core_x1",
        "core_y1",
        "core_x2",
        "core_y2",
        "x1",
        "y1",
        "x2",
        "y2",
    ]
    excluded_fields = [
        "frame_index",
        "timestamp_seconds",
        "detection_index",
        "confidence",
        "bbox_overlap",
        "assigned_slot_before_filter",
        "x1",
        "y1",
        "x2",
        "y2",
    ]

    timeline: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    previous_snapshot: tuple[str, ...] | None = None
    evidence_counts = {
        slot_id: Counter({"occupied": 0, "clear": 0, "uncertain": 0})
        for slot_id in slot_ids
    }
    excluded_count = 0
    excluded_frames = 0
    assignment_unique = True
    processed = 0
    inference_seconds = 0.0
    started = time.perf_counter()

    print("=== RAW VIDEO YOLO + POLYGON ANALYSIS ===", flush=True)
    print(f"Python: {sys.executable}", flush=True)
    print(f"CUDA: {torch.cuda.is_available()}", flush=True)
    print(f"Video: {input_video}", flush=True)
    print(f"Model: {weights}", flush=True)
    print(f"Output data: {output_dir}", flush=True)

    with (
        states_csv.open("w", newline="", encoding="utf-8") as state_file,
        transitions_csv.open("w", newline="", encoding="utf-8") as transition_file,
        detections_csv.open("w", newline="", encoding="utf-8") as detection_file,
        excluded_csv.open("w", newline="", encoding="utf-8") as excluded_file,
    ):
        state_writer = csv.DictWriter(state_file, fieldnames=state_fields)
        transition_writer = csv.DictWriter(
            transition_file, fieldnames=transition_fields
        )
        detection_writer = csv.DictWriter(detection_file, fieldnames=detection_fields)
        excluded_writer = csv.DictWriter(excluded_file, fieldnames=excluded_fields)
        state_writer.writeheader()
        transition_writer.writeheader()
        detection_writer.writeheader()
        excluded_writer.writeheader()

        try:
            for frame_index in range(source_frames):
                readable, frame = capture.read()
                if not readable or frame is None:
                    break
                if (source_width, source_height) != analysis_size:
                    frame = cv2.resize(frame, analysis_size, interpolation=cv2.INTER_AREA)

                inference_started = time.perf_counter()
                result = model.predict(
                    source=frame,
                    conf=args.conf,
                    iou=args.iou,
                    imgsz=args.imgsz,
                    device=args.device,
                    classes=[0],
                    verbose=False,
                )[0]
                inference_seconds += time.perf_counter() - inference_started
                raw_detections = extract_detections(result)
                raw_matches, _raw_evidence = assign_detections_to_slots(
                    raw_detections,
                    slots,
                    minimum_bbox_overlap=args.bbox_overlap,
                    minimum_slot_overlap=args.slot_overlap,
                    uncertain_overlap=args.uncertain_overlap,
                    minimum_axis_elongation=args.minimum_axis_elongation,
                    core_horizontal_inset=args.core_horizontal_inset,
                    core_top_inset=args.core_top_inset,
                    core_bottom_inset=args.core_bottom_inset,
                    frame_size=analysis_size,
                    border_margin_ratio=args.border_margin_ratio,
                )
                raw_assignment = {
                    int(match["detection_index"]): slot_id
                    for slot_id, match in raw_matches.items()
                }
                detections, excluded = filter_detections_by_exclusion(
                    raw_detections,
                    exclusion_points,
                    minimum_bbox_overlap=args.exclusion_overlap,
                )
                matches, evidence = assign_detections_to_slots(
                    detections,
                    slots,
                    minimum_bbox_overlap=args.bbox_overlap,
                    minimum_slot_overlap=args.slot_overlap,
                    uncertain_overlap=args.uncertain_overlap,
                    minimum_axis_elongation=args.minimum_axis_elongation,
                    core_horizontal_inset=args.core_horizontal_inset,
                    core_top_inset=args.core_top_inset,
                    core_bottom_inset=args.core_bottom_inset,
                    frame_size=analysis_size,
                    border_margin_ratio=args.border_margin_ratio,
                )
                timestamp = frame_index / source_fps

                if excluded:
                    excluded_frames += 1
                    excluded_count += len(excluded)
                for rejected in excluded:
                    raw_index = int(rejected["detection_index"])
                    assigned_before = raw_assignment.get(raw_index, "")
                    if assigned_before:
                        raise RuntimeError(
                            f"X01 rejected a detection assigned to {assigned_before} "
                            f"at frame {frame_index}"
                        )
                    x1, y1, x2, y2 = rejected["box"]
                    excluded_writer.writerow(
                        {
                            "frame_index": frame_index,
                            "timestamp_seconds": f"{timestamp:.6f}",
                            "detection_index": raw_index,
                            "confidence": f"{rejected['confidence']:.6f}",
                            "bbox_overlap": f"{rejected['bbox_overlap']:.6f}",
                            "assigned_slot_before_filter": assigned_before,
                            "x1": f"{x1:.3f}",
                            "y1": f"{y1:.3f}",
                            "x2": f"{x2:.3f}",
                            "y2": f"{y2:.3f}",
                        }
                    )

                match_indices = [
                    int(match["detection_index"]) for match in matches.values()
                ]
                if len(match_indices) != len(set(match_indices)):
                    assignment_unique = False
                    raise RuntimeError("One detection was assigned to multiple slots")
                match_by_detection = {
                    int(match["detection_index"]): (slot_id, match)
                    for slot_id, match in matches.items()
                }
                for detection_index, detection in enumerate(detections):
                    slot_id, match = match_by_detection.get(
                        detection_index, ("", None)
                    )
                    core_box = match["core_box"] if match else ["", "", "", ""]
                    detection_writer.writerow(
                        {
                            "frame_index": frame_index,
                            "timestamp_seconds": f"{timestamp:.6f}",
                            "detection_index": detection_index,
                            "confidence": f"{float(detection['confidence']):.6f}",
                            "assigned_slot": slot_id,
                            "bbox_overlap": (
                                f"{float(match['bbox_overlap']):.6f}" if match else ""
                            ),
                            "slot_overlap": (
                                f"{float(match['slot_overlap']):.6f}" if match else ""
                            ),
                            "core_overlap": (
                                f"{float(match['core_overlap']):.6f}" if match else ""
                            ),
                            "core_slot_overlap": (
                                f"{float(match['core_slot_overlap']):.6f}" if match else ""
                            ),
                            "core_center_inside": (
                                str(bool(match["core_center_inside"])).lower()
                                if match
                                else ""
                            ),
                            "is_border_truncated": (
                                str(bool(match["is_border_truncated"])).lower()
                                if match
                                else ""
                            ),
                            "core_x1": f"{float(core_box[0]):.3f}" if match else "",
                            "core_y1": f"{float(core_box[1]):.3f}" if match else "",
                            "core_x2": f"{float(core_box[2]):.3f}" if match else "",
                            "core_y2": f"{float(core_box[3]):.3f}" if match else "",
                            "x1": f"{float(detection['x1']):.3f}",
                            "y1": f"{float(detection['y1']):.3f}",
                            "x2": f"{float(detection['x2']):.3f}",
                            "y2": f"{float(detection['y2']):.3f}",
                        }
                    )

                for slot_id, value in evidence.items():
                    evidence_counts[slot_id][value] += 1
                for slot_id in slot_ids:
                    change = states[slot_id].update(evidence[slot_id])
                    if change is not None:
                        previous, current = change
                        row = {
                            "frame_index": frame_index,
                            "timestamp_seconds": f"{timestamp:.6f}",
                            "slot_id": slot_id,
                            "previous_state": previous,
                            "new_state": current,
                            "evidence": evidence[slot_id],
                        }
                        transitions.append(row)
                        transition_writer.writerow(row)

                values = tuple(states[slot_id].state for slot_id in slot_ids)
                counts = {
                    OCCUPIED: values.count(OCCUPIED),
                    EMPTY: values.count(EMPTY),
                    UNKNOWN: values.count(UNKNOWN),
                }
                if sum(counts.values()) != 24:
                    raise RuntimeError("OCCUPIED + EMPTY + UNKNOWN must equal 24")
                state_writer.writerow(
                    {
                        "frame_index": frame_index,
                        "timestamp_seconds": f"{timestamp:.6f}",
                        "occupied_count": counts[OCCUPIED],
                        "empty_count": counts[EMPTY],
                        "unknown_count": counts[UNKNOWN],
                        **{slot_id: states[slot_id].state for slot_id in slot_ids},
                    }
                )
                if values != previous_snapshot:
                    timeline.append(
                        timeline_entry(frame_index, timestamp, slot_ids, states)
                    )
                    previous_snapshot = values

                processed += 1
                if processed == 1 or processed % 100 == 0:
                    elapsed = time.perf_counter() - started
                    print(
                        f"Processed {processed}/{source_frames} frames "
                        f"({processed / elapsed:.2f} FPS)",
                        flush=True,
                    )
        finally:
            capture.release()

    if processed != source_frames:
        raise RuntimeError(f"Processing stopped at {processed}/{source_frames} frames")
    input_hash_after = sha256_file(input_video)
    input_unchanged = (
        input_hash_after == input_hash_before
        and input_video.stat().st_size == input_size_before
    )
    if not input_unchanged:
        raise RuntimeError("Source video changed while it was being analyzed")

    elapsed = time.perf_counter() - started
    final_counts = timeline[-1]["counts"]
    video_payload = {
        "width": source_width,
        "height": source_height,
        "fps": source_fps,
        "frames": source_frames,
        "duration_seconds": source_frames / source_fps,
        "codec": source_codec,
        "sha256": input_hash_before,
    }
    timeline_payload = {
        "schema_version": 1,
        "video": video_payload,
        "coordinate_space": config["coordinate_space"],
        "polygons": [
            {"id": item["id"], "points": item["points"]}
            for item in config["polygons"]
            if item["type"] == "parking_space"
        ],
        "slot_ids": slot_ids,
        "states": sorted([OCCUPIED, EMPTY, UNKNOWN]),
        "total_slots": 24,
        "total_frames": source_frames,
        "fps": source_fps,
        "duration_seconds": source_frames / source_fps,
        "timeline": timeline,
    }
    timeline_json.write_text(
        json.dumps(timeline_payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    summary = {
        "schema_version": 1,
        "mode": "inference_only_raw_video",
        "input_video": str(input_video),
        "input_sha256": input_hash_before,
        "input_unchanged": input_unchanged,
        "model": str(weights),
        "polygon_config": str(polygon_path),
        "video": video_payload,
        "analysis_space": {"width": analysis_width, "height": analysis_height},
        "processing": {
            "frames": processed,
            "elapsed_seconds": elapsed,
            "overall_fps": processed / elapsed if elapsed else 0.0,
            "inference_fps": processed / inference_seconds if inference_seconds else 0.0,
            "device": args.device,
            "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
            "imgsz": args.imgsz,
            "training_performed": False,
            "annotated_video_created": False,
        },
        "thresholds": {
            "confidence": args.conf,
            "nms_iou": args.iou,
            "minimum_bbox_overlap": args.bbox_overlap,
            "minimum_slot_overlap": args.slot_overlap,
            "uncertain_overlap": args.uncertain_overlap,
            "minimum_axis_elongation": args.minimum_axis_elongation,
            "core_horizontal_inset": args.core_horizontal_inset,
            "core_top_inset": args.core_top_inset,
            "core_bottom_inset": args.core_bottom_inset,
            "border_margin_ratio": args.border_margin_ratio,
            "exclusion_bbox_overlap": args.exclusion_overlap,
            "occupied_seconds": args.occupied_seconds,
            "empty_seconds": args.empty_seconds,
            "release_seconds": args.release_seconds,
            "ambiguous_seconds": args.ambiguous_seconds,
            "occupied_frames": occupied_frames,
            "empty_frames": empty_frames,
            "release_frames": release_frames,
            "ambiguous_frames": ambiguous_frames,
        },
        "final_counts": {
            "OCCUPIED": final_counts["occupied"],
            "EMPTY": final_counts["empty"],
            "UNKNOWN": final_counts["unknown"],
        },
        "final_states": {
            slot_id: states[slot_id].state for slot_id in slot_ids
        },
        "transitions": transitions,
        "exclusion": {
            "id": APPROVED_EXCLUSION_ID,
            "rule": "center_inside AND bbox_overlap >= 0.60",
            "excluded_detections": excluded_count,
            "frames_with_exclusions": excluded_frames,
        },
        "evidence_counts": {
            slot_id: dict(counts) for slot_id, counts in evidence_counts.items()
        },
        "checks": {
            "state_count_invariant": True,
            "one_detection_assigned_to_at_most_one_slot": assignment_unique,
            "source_video_unchanged": input_unchanged,
            "annotated_video_absent": True,
        },
        "output_files": [
            states_csv.name,
            transitions_csv.name,
            detections_csv.name,
            excluded_csv.name,
            timeline_json.name,
            summary_json.name,
        ],
    }
    summary_json.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("=== ANALYSIS COMPLETE (DATA ONLY) ===", flush=True)
    print(
        json.dumps(
            {
                "frames": processed,
                "final_counts": summary["final_counts"],
                "transitions": len(transitions),
                "annotated_video_created": False,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
