"""Run the trained car detector with confirmed demo parking polygons."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import torch
from ultralytics import YOLO

from parking_polygon_config import load_config, scale_points
from test_yolov8n_video import draw_detections, extract_detections, require_file
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
DEFAULT_POLYGONS = (
    PROJECT_ROOT / "config" / "demo_parking" / "0922_1_polygons.json"
)
DEFAULT_OUTPUT_ROOT = (
    PROJECT_ROOT / "runs" / "detect" / "polygon_tests" / "yolo_car_v12_best"
)
STATE_COLORS = {
    OCCUPIED: (20, 20, 235),
    EMPTY: (30, 210, 30),
    UNKNOWN: (245, 245, 245),
}
APPROVED_EXCLUSION_ID = "X01"
APPROVED_EXCLUSION_POINTS = [
    [1436, 1360],
    [1748, 1360],
    [1748, 2150],
    [1436, 2150],
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_VIDEO)
    parser.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    parser.add_argument("--polygons", type=Path, default=DEFAULT_POLYGONS)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--start", type=float, default=0.0)
    parser.add_argument(
        "--seconds",
        type=float,
        default=None,
        help="Segment duration; omit to process from --start through the final frame.",
    )
    parser.add_argument("--conf", type=float, default=0.40)
    parser.add_argument("--iou", type=float, default=0.50)
    parser.add_argument("--imgsz", type=int, default=512)
    parser.add_argument("--device", default="0")
    parser.add_argument("--output-width", type=int, default=1920)
    parser.add_argument(
        "--initial-states-csv",
        type=Path,
        default=None,
        help="Optionally seed slot states at --start from an existing verified timeline.",
    )
    parser.add_argument("--bbox-overlap", type=float, default=0.45)
    parser.add_argument("--slot-overlap", type=float, default=0.12)
    parser.add_argument("--uncertain-overlap", type=float, default=0.10)
    parser.add_argument("--minimum-axis-elongation", type=float, default=1.25)
    parser.add_argument("--exclusion-overlap", type=float, default=0.60)
    parser.add_argument("--occupied-seconds", type=float, default=0.75)
    parser.add_argument("--empty-seconds", type=float, default=1.50)
    parser.add_argument("--release-seconds", type=float, default=2.00)
    parser.add_argument(
        "--sample-times",
        type=float,
        nargs="*",
        default=None,
        help="Absolute video timestamps for sample images.",
    )
    return parser.parse_args()


def validate_confirmed_polygons(path: Path) -> dict[str, Any]:
    config = load_config(path)
    expected_ids = [f"D{number:02d}" for number in range(1, 25)]
    polygons = config["polygons"]
    parking_polygons = [
        item for item in polygons if item["type"] == "parking_space"
    ]
    exclusion_polygons = [
        item for item in polygons if item["type"] == "exclusion_zone"
    ]
    identifiers = [item["id"] for item in parking_polygons]
    if identifiers != expected_ids:
        raise ValueError(
            "Polygon IDs must be exactly D01-D24 in order; "
            f"received: {identifiers}"
        )
    if len(set(identifier.casefold() for identifier in identifiers)) != 24:
        raise ValueError("Polygon IDs are not unique")
    if config["coordinate_space"] != {"width": 3840, "height": 2160}:
        raise ValueError(
            "Expected polygon coordinate space 3840x2160; received "
            f"{config['coordinate_space']}"
        )
    if len(set(item["id"].casefold() for item in polygons)) != len(polygons):
        raise ValueError("All polygon and exclusion IDs must be unique")
    if len(exclusion_polygons) != 1:
        raise ValueError("The full-video run requires exactly one X01 exclusion zone")
    exclusion = exclusion_polygons[0]
    if (
        exclusion["id"] != APPROVED_EXCLUSION_ID
        or exclusion["points"] != APPROVED_EXCLUSION_POINTS
    ):
        raise ValueError("X01 does not match the approved coordinates")
    unexpected_types = {
        item["type"]
        for item in polygons
        if item["type"] not in {"parking_space", "exclusion_zone"}
    }
    if unexpected_types:
        raise ValueError(f"Unsupported polygon types: {sorted(unexpected_types)}")
    return config


def draw_slot_fills(
    frame: np.ndarray,
    slots,
    states: dict[str, TemporalSlotState],
) -> np.ndarray:
    canvas = frame.copy()
    overlay = canvas.copy()
    for slot in slots:
        color = STATE_COLORS[states[slot.identifier].state]
        cv2.fillPoly(overlay, [slot.points.astype(np.int32)], color)
    cv2.addWeighted(overlay, 0.13, canvas, 0.87, 0, canvas)
    return canvas


def draw_slot_outlines(
    frame: np.ndarray,
    slots,
    states: dict[str, TemporalSlotState],
    timestamp: float,
) -> np.ndarray:
    canvas = frame
    counts = {state: 0 for state in (OCCUPIED, EMPTY, UNKNOWN)}
    for slot in slots:
        state = states[slot.identifier].state
        counts[state] += 1
        color = STATE_COLORS[state]
        points = slot.points.astype(np.int32)
        thickness = 4 if slot.identifier == "D01" else 2
        cv2.polylines(canvas, [points], True, color, thickness, cv2.LINE_AA)
        center = tuple(np.mean(points, axis=0).astype(int))
        label = f"{slot.identifier} {state[0]}"
        cv2.putText(
            canvas,
            label,
            center,
            cv2.FONT_HERSHEY_SIMPLEX,
            0.52,
            color,
            2,
            cv2.LINE_AA,
        )

    if sum(counts.values()) != len(slots):
        raise RuntimeError("Slot-state count invariant failed")
    text = (
        f"t={timestamp:05.2f}s  OCCUPIED={counts[OCCUPIED]}  "
        f"EMPTY={counts[EMPTY]}  UNKNOWN={counts[UNKNOWN]}  TOTAL={len(slots)}"
    )
    cv2.rectangle(canvas, (0, 0), (min(canvas.shape[1], 930), 48), (20, 20, 20), -1)
    cv2.putText(
        canvas,
        text,
        (14, 33),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.76,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    return canvas


def verify_output_video(
    path: Path,
    *,
    expected_frames: int,
    expected_fps: float,
    expected_size: tuple[int, int],
) -> dict[str, Any]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"OpenCV cannot reopen output video: {path}")
    try:
        frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        first_ok, _first = capture.read()
        capture.set(cv2.CAP_PROP_POS_FRAMES, max(0, frames - 1))
        last_ok, _last = capture.read()
    finally:
        capture.release()
    if frames != expected_frames:
        raise RuntimeError(f"Output has {frames} frames; expected {expected_frames}")
    if not np.isclose(fps, expected_fps, atol=1e-3):
        raise RuntimeError(f"Output FPS is {fps}; expected {expected_fps}")
    if (width, height) != expected_size or not first_ok or not last_ok:
        raise RuntimeError("Output size or first/last-frame readability check failed")
    return {
        "frames": frames,
        "fps": fps,
        "duration_seconds": frames / fps,
        "width": width,
        "height": height,
        "first_frame_readable": first_ok,
        "last_frame_readable": last_ok,
    }


def load_initial_states(path: Path, frame_index: int, slot_ids: list[str]) -> dict[str, str]:
    valid_states = {OCCUPIED, EMPTY, UNKNOWN}
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if not set(slot_ids).issubset(reader.fieldnames or []):
            raise ValueError("Initial-state CSV does not contain D01-D24")
        for row in reader:
            if int(row["frame_index"]) != frame_index:
                continue
            result = {identifier: row[identifier] for identifier in slot_ids}
            if any(state not in valid_states for state in result.values()):
                raise ValueError("Initial-state CSV contains an invalid slot state")
            return result
    raise ValueError(f"Initial-state CSV has no frame {frame_index}")


def extract_transition_samples(
    video_path: Path,
    sample_dir: Path,
    *,
    transitions: list[dict[str, Any]],
    start_frame: int,
    processed_frames: int,
    fps: float,
) -> list[str]:
    """Save annotated frames before/at/after each distinct transition time."""
    wanted: dict[int, set[str]] = defaultdict(set)
    for row in transitions:
        absolute_frame = int(row["frame_index"])
        for offset_frames, label in (
            (-round(0.5 * fps), "before"),
            (0, "at"),
            (round(0.5 * fps), "after"),
        ):
            output_index = absolute_frame + offset_frames - start_frame
            if 0 <= output_index < processed_frames:
                wanted[output_index].add(f"{row['slot_id']}_{label}")
    if not wanted:
        return []

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Cannot reopen output for transition samples: {video_path}")
    paths: list[str] = []
    try:
        for output_index in sorted(wanted):
            capture.set(cv2.CAP_PROP_POS_FRAMES, output_index)
            ok, frame = capture.read()
            if not ok or frame is None:
                raise RuntimeError(f"Cannot read output frame {output_index}")
            absolute_frame = start_frame + output_index
            timestamp = absolute_frame / fps
            reason_items = sorted(wanted[output_index])
            reasons = (
                "-".join(reason_items)
                if len(reason_items) <= 5
                else f"{len(reason_items)}events"
            )
            path = sample_dir / (
                f"transition_frame_{absolute_frame:06d}_t{timestamp:06.2f}s_"
                f"{reasons}_annotated.jpg"
            )
            if not cv2.imwrite(str(path), frame):
                raise RuntimeError(f"Cannot write transition sample: {path}")
            paths.append(str(path))
    finally:
        capture.release()
    return paths


def main() -> None:
    args = parse_args()
    if args.start < 0 or (args.seconds is not None and args.seconds <= 0):
        raise ValueError("--start must be non-negative and --seconds must be positive")
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
    if args.minimum_axis_elongation < 1:
        raise ValueError("--minimum-axis-elongation must be at least 1")
    input_video = require_file(args.input, "input video")
    weights = require_file(args.weights, "best.pt")
    polygon_path = require_file(args.polygons, "confirmed polygon JSON")
    config = validate_confirmed_polygons(polygon_path)
    if args.device != "cpu" and not torch.cuda.is_available():
        raise RuntimeError("GPU requested but torch.cuda.is_available() is False")

    run_dir = (args.output_root / args.run_name).resolve()
    if run_dir.exists():
        raise FileExistsError(f"Refusing to overwrite existing output: {run_dir}")

    capture = cv2.VideoCapture(str(input_video))
    if not capture.isOpened():
        raise RuntimeError(f"OpenCV cannot open video: {input_video}")
    source_fps = float(capture.get(cv2.CAP_PROP_FPS))
    source_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    source_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    source_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if min(source_fps, source_frames, source_width, source_height) <= 0:
        capture.release()
        raise RuntimeError("Invalid video metadata")
    coordinate_size = (
        int(config["coordinate_space"]["width"]),
        int(config["coordinate_space"]["height"]),
    )
    source_aspect = source_width / source_height
    polygon_aspect = coordinate_size[0] / coordinate_size[1]
    if abs(source_aspect - polygon_aspect) / polygon_aspect > 0.01:
        capture.release()
        raise ValueError(
            f"Video aspect ratio {source_width}x{source_height} is incompatible "
            f"with polygon space {coordinate_size[0]}x{coordinate_size[1]}"
        )

    start_frame = int(round(args.start * source_fps))
    requested_frames = (
        source_frames - start_frame
        if args.seconds is None
        else int(round(args.seconds * source_fps))
    )
    end_frame = min(source_frames, start_frame + requested_frames)
    if start_frame >= source_frames or end_frame <= start_frame:
        capture.release()
        raise ValueError("Requested segment is outside the video")
    segment_frames = end_frame - start_frame
    output_width = args.output_width if args.output_width > 0 else source_width
    output_height = int(round(source_height * output_width / source_width))
    if output_height % 2:
        output_height += 1
    slots = build_slots(
        config["polygons"],
        coordinate_size,
        (output_width, output_height),
    )
    if len(slots) != 24:
        capture.release()
        raise RuntimeError(f"Expected 24 scaled slots, received {len(slots)}")
    exclusion = next(
        item for item in config["polygons"] if item["type"] == "exclusion_zone"
    )
    exclusion_points = np.asarray(
        scale_points(
            exclusion["points"],
            coordinate_size,
            (output_width, output_height),
        ),
        dtype=np.float32,
    )

    occupied_frames = seconds_to_frames(args.occupied_seconds, source_fps)
    empty_frames = seconds_to_frames(args.empty_seconds, source_fps)
    release_frames = seconds_to_frames(args.release_seconds, source_fps)
    states = {
        slot.identifier: TemporalSlotState(
            slot.identifier,
            occupied_frames=occupied_frames,
            empty_frames=empty_frames,
            release_frames=release_frames,
        )
        for slot in slots
    }
    seeded_states = None
    if args.initial_states_csv is not None:
        initial_states_path = require_file(
            args.initial_states_csv, "initial slot-state CSV"
        )
        seeded_states = load_initial_states(
            initial_states_path,
            start_frame,
            [slot.identifier for slot in slots],
        )
        for identifier, state in seeded_states.items():
            states[identifier].state = state

    run_dir.mkdir(parents=True)
    sample_dir = run_dir / "samples"
    sample_dir.mkdir()
    output_video = run_dir / "yolo_polygon_annotated.mp4"
    states_csv = run_dir / "slot_states.csv"
    assignments_csv = run_dir / "assignments.csv"
    excluded_csv = run_dir / "excluded_detections.csv"
    transitions_csv = run_dir / "state_transitions.csv"
    summary_json = run_dir / "summary.json"
    writer = cv2.VideoWriter(
        str(output_video),
        cv2.VideoWriter_fourcc(*"mp4v"),
        source_fps,
        (output_width, output_height),
    )
    if not writer.isOpened():
        capture.release()
        raise RuntimeError(f"Cannot create output video: {output_video}")

    model = YOLO(str(weights))
    names = {int(key): str(value) for key, value in model.names.items()}
    if names.get(0) != "Car":
        capture.release()
        writer.release()
        raise RuntimeError(f"Checkpoint class 0 is not Car: {names}")

    sample_frames = {
        min(end_frame - 1, max(start_frame, int(round(value * source_fps))))
        for value in (args.sample_times or [])
        if args.start <= value <= end_frame / source_fps
    }
    if not sample_frames:
        sample_frames = {
            int(index)
            for index in np.linspace(start_frame, end_frame - 1, min(9, segment_frames))
        }

    print("=== YOLO + POLYGON PREFLIGHT ===", flush=True)
    print(f"Python: {sys.executable}", flush=True)
    print(f"CUDA: {torch.cuda.is_available()}", flush=True)
    print(
        f"GPU: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU'}",
        flush=True,
    )
    print(f"Video: {input_video}", flush=True)
    print(f"Weights: {weights}", flush=True)
    print(f"Polygons: {polygon_path} (24 slots, 3840x2160)", flush=True)
    print(
        f"Exclusion: {APPROVED_EXCLUSION_ID}, overlap={args.exclusion_overlap}",
        flush=True,
    )
    print(
        f"Segment: {start_frame / source_fps:.3f}s to {end_frame / source_fps:.3f}s "
        f"({segment_frames} frames)",
        flush=True,
    )
    print(
        f"Thresholds: conf={args.conf}, IoU={args.iou}, "
        f"bbox_overlap={args.bbox_overlap}, slot_overlap={args.slot_overlap}, "
        f"uncertain_overlap={args.uncertain_overlap}",
        flush=True,
    )
    print(
        f"Temporal frames: occupied={occupied_frames}, empty={empty_frames}, "
        f"release={release_frames}",
        flush=True,
    )
    print(f"Output: {run_dir}", flush=True)

    capture.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    processed = 0
    inference_seconds = 0.0
    started = time.perf_counter()
    transition_rows: list[dict[str, Any]] = []
    d01_evidence: list[tuple[float, str, str]] = []
    initial_states: dict[str, str] | None = None
    initial_evidence: dict[str, str] | None = None
    evidence_counts = {
        slot.identifier: Counter({"occupied": 0, "clear": 0, "uncertain": 0})
        for slot in slots
    }
    assignment_uniqueness_verified = True
    excluded_detection_count = 0
    excluded_frame_count = 0
    exclusion_changed_evidence_frames: list[int] = []
    excluded_assigned_count = 0
    state_fields = [
        "frame_index",
        "timestamp_seconds",
        "occupied_count",
        "empty_count",
        "unknown_count",
        *[slot.identifier for slot in slots],
    ]
    assignment_fields = [
        "frame_index",
        "timestamp_seconds",
        "slot_id",
        "detection_index",
        "confidence",
        "bbox_overlap",
        "slot_overlap",
        "center_inside",
        "x1",
        "y1",
        "x2",
        "y2",
    ]
    transition_fields = [
        "frame_index",
        "timestamp_seconds",
        "slot_id",
        "previous_state",
        "new_state",
        "evidence",
    ]
    excluded_fields = [
        "frame_index",
        "timestamp_seconds",
        "detection_index",
        "confidence",
        "bbox_overlap",
        "center_inside",
        "assigned_slot_before_filter",
        "x1",
        "y1",
        "x2",
        "y2",
    ]

    with (
        states_csv.open("w", newline="", encoding="utf-8") as state_file,
        assignments_csv.open("w", newline="", encoding="utf-8") as assignment_file,
        excluded_csv.open("w", newline="", encoding="utf-8") as excluded_file,
        transitions_csv.open("w", newline="", encoding="utf-8") as transition_file,
    ):
        state_writer = csv.DictWriter(state_file, fieldnames=state_fields)
        assignment_writer = csv.DictWriter(assignment_file, fieldnames=assignment_fields)
        excluded_writer = csv.DictWriter(excluded_file, fieldnames=excluded_fields)
        transition_writer = csv.DictWriter(transition_file, fieldnames=transition_fields)
        state_writer.writeheader()
        assignment_writer.writeheader()
        excluded_writer.writeheader()
        transition_writer.writeheader()

        try:
            for absolute_frame in range(start_frame, end_frame):
                readable, frame = capture.read()
                if not readable or frame is None:
                    print(f"Warning: stopped early at frame {absolute_frame}", flush=True)
                    break
                if (source_width, source_height) != (output_width, output_height):
                    frame = cv2.resize(
                        frame,
                        (output_width, output_height),
                        interpolation=cv2.INTER_AREA,
                    )

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
                raw_matches, raw_evidence = assign_detections_to_slots(
                    raw_detections,
                    slots,
                    minimum_bbox_overlap=args.bbox_overlap,
                    minimum_slot_overlap=args.slot_overlap,
                    uncertain_overlap=args.uncertain_overlap,
                    minimum_axis_elongation=args.minimum_axis_elongation,
                )
                raw_assignment_by_detection = {
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
                )
                timestamp = absolute_frame / source_fps
                if excluded:
                    excluded_frame_count += 1
                    excluded_detection_count += len(excluded)
                for rejected in excluded:
                    raw_index = int(rejected["detection_index"])
                    assigned_slot = raw_assignment_by_detection.get(raw_index, "")
                    if assigned_slot:
                        excluded_assigned_count += 1
                        raise RuntimeError(
                            f"X01 rejected detection {raw_index} assigned to "
                            f"{assigned_slot} at frame {absolute_frame}"
                        )
                    x1, y1, x2, y2 = rejected["box"]
                    excluded_writer.writerow(
                        {
                            "frame_index": absolute_frame,
                            "timestamp_seconds": f"{timestamp:.6f}",
                            "detection_index": raw_index,
                            "confidence": f"{rejected['confidence']:.6f}",
                            "bbox_overlap": f"{rejected['bbox_overlap']:.6f}",
                            "center_inside": rejected["center_inside"],
                            "assigned_slot_before_filter": assigned_slot,
                            "x1": f"{x1:.3f}",
                            "y1": f"{y1:.3f}",
                            "x2": f"{x2:.3f}",
                            "y2": f"{y2:.3f}",
                        }
                    )
                if evidence != raw_evidence:
                    exclusion_changed_evidence_frames.append(absolute_frame)
                assigned_detection_indices = [
                    int(match["detection_index"]) for match in matches.values()
                ]
                if len(assigned_detection_indices) != len(
                    set(assigned_detection_indices)
                ):
                    assignment_uniqueness_verified = False
                    raise RuntimeError("One detection was assigned to multiple slots")
                for identifier, raw_evidence in evidence.items():
                    evidence_counts[identifier][raw_evidence] += 1

                for slot in slots:
                    transition = states[slot.identifier].update(evidence[slot.identifier])
                    if transition is not None:
                        previous, current = transition
                        row = {
                            "frame_index": absolute_frame,
                            "timestamp_seconds": f"{timestamp:.6f}",
                            "slot_id": slot.identifier,
                            "previous_state": previous,
                            "new_state": current,
                            "evidence": evidence[slot.identifier],
                        }
                        transition_rows.append(row)
                        transition_writer.writerow(row)

                counts = {
                    state: sum(item.state == state for item in states.values())
                    for state in (OCCUPIED, EMPTY, UNKNOWN)
                }
                if sum(counts.values()) != 24:
                    raise RuntimeError("OCCUPIED + EMPTY + UNKNOWN is not 24")
                if initial_states is None:
                    initial_states = {
                        identifier: state.state
                        for identifier, state in states.items()
                    }
                    initial_evidence = dict(evidence)
                state_writer.writerow(
                    {
                        "frame_index": absolute_frame,
                        "timestamp_seconds": f"{timestamp:.6f}",
                        "occupied_count": counts[OCCUPIED],
                        "empty_count": counts[EMPTY],
                        "unknown_count": counts[UNKNOWN],
                        **{
                            identifier: state.state
                            for identifier, state in states.items()
                        },
                    }
                )
                for slot_id, match in matches.items():
                    x1, y1, x2, y2 = match["box"]
                    assignment_writer.writerow(
                        {
                            "frame_index": absolute_frame,
                            "timestamp_seconds": f"{timestamp:.6f}",
                            "slot_id": slot_id,
                            "detection_index": match["detection_index"],
                            "confidence": f"{match['confidence']:.6f}",
                            "bbox_overlap": f"{match['bbox_overlap']:.6f}",
                            "slot_overlap": f"{match['slot_overlap']:.6f}",
                            "center_inside": match["center_inside"],
                            "x1": f"{x1:.3f}",
                            "y1": f"{y1:.3f}",
                            "x2": f"{x2:.3f}",
                            "y2": f"{y2:.3f}",
                        }
                    )
                d01_evidence.append((timestamp, evidence["D01"], states["D01"].state))

                annotated = draw_slot_fills(frame, slots, states)
                annotated = draw_detections(annotated, detections, names)
                annotated = draw_slot_outlines(annotated, slots, states, timestamp)
                writer.write(annotated)
                if absolute_frame in sample_frames:
                    stem = f"frame_{absolute_frame:06d}_t{timestamp:06.2f}s"
                    cv2.imwrite(str(sample_dir / f"{stem}_annotated.jpg"), annotated)
                    cv2.imwrite(str(sample_dir / f"{stem}_original.jpg"), frame)

                processed += 1
                if processed == 1 or processed % 100 == 0:
                    elapsed = time.perf_counter() - started
                    print(
                        f"Processed {processed}/{segment_frames} frames "
                        f"({processed / elapsed:.2f} FPS)",
                        flush=True,
                    )
        finally:
            capture.release()
            writer.release()

    elapsed = time.perf_counter() - started
    if processed != segment_frames:
        raise RuntimeError(
            f"Processing stopped at {processed}/{segment_frames} requested frames"
        )
    output_validation = verify_output_video(
        output_video,
        expected_frames=processed,
        expected_fps=source_fps,
        expected_size=(output_width, output_height),
    )
    transition_samples = extract_transition_samples(
        output_video,
        sample_dir,
        transitions=transition_rows,
        start_frame=start_frame,
        processed_frames=processed,
        fps=source_fps,
    )

    transitions_by_slot: dict[str, list[float]] = defaultdict(list)
    for row in transition_rows:
        transitions_by_slot[str(row["slot_id"])].append(
            float(row["timestamp_seconds"])
        )
    rapid_transitions = []
    for identifier, timestamps in transitions_by_slot.items():
        for previous, current in zip(timestamps, timestamps[1:]):
            if current - previous < 0.5:
                rapid_transitions.append(
                    {
                        "slot_id": identifier,
                        "first_seconds": previous,
                        "second_seconds": current,
                    }
                )
    warnings: list[str] = []
    if rapid_transitions:
        warnings.append("Rapid state transitions under 0.5 seconds were detected")
    uncertain_slots = {
        identifier: counts["uncertain"]
        for identifier, counts in evidence_counts.items()
        if counts["uncertain"] / processed >= 0.10
    }
    if uncertain_slots:
        warnings.append(
            "Some slots had uncertain geometric evidence in at least 10% of frames"
        )
    final_counts = {
        state: sum(item.state == state for item in states.values())
        for state in (OCCUPIED, EMPTY, UNKNOWN)
    }
    if final_counts[UNKNOWN]:
        warnings.append("One or more slots remained UNKNOWN at the final frame")

    summary = {
        "input_video": str(input_video),
        "weights": str(weights),
        "polygon_config": str(polygon_path),
        "output_video": str(output_video),
        "source": {
            "width": source_width,
            "height": source_height,
            "fps": source_fps,
            "frames": source_frames,
            "duration_seconds": source_frames / source_fps,
        },
        "segment": {
            "start_seconds": start_frame / source_fps,
            "end_seconds": (start_frame + processed) / source_fps,
            "frames": processed,
            "fps": source_fps,
        },
        "processing": {
            "output_size": [output_width, output_height],
            "elapsed_seconds": elapsed,
            "overall_fps": processed / elapsed if elapsed else 0.0,
            "inference_fps": processed / inference_seconds if inference_seconds else 0.0,
            "device": args.device,
            "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
            "imgsz": args.imgsz,
            "seeded_states": seeded_states,
        },
        "output_validation": output_validation,
        "tracked_slots": len(slots),
        "thresholds": {
            "confidence": args.conf,
            "nms_iou": args.iou,
            "minimum_bbox_overlap": args.bbox_overlap,
            "minimum_slot_overlap": args.slot_overlap,
            "uncertain_overlap": args.uncertain_overlap,
            "minimum_axis_elongation": args.minimum_axis_elongation,
            "exclusion_bbox_overlap": args.exclusion_overlap,
            "occupied_seconds": args.occupied_seconds,
            "empty_seconds": args.empty_seconds,
            "release_seconds": args.release_seconds,
            "occupied_frames": occupied_frames,
            "empty_frames": empty_frames,
            "release_frames": release_frames,
        },
        "initial_states": initial_states,
        "initial_evidence": initial_evidence,
        "final_counts": final_counts,
        "final_states": {
            identifier: state.state for identifier, state in states.items()
        },
        "d01": {
            "initial_state": d01_evidence[0][2] if d01_evidence else None,
            "initial_evidence": d01_evidence[0][1] if d01_evidence else None,
            "final_state": d01_evidence[-1][2] if d01_evidence else None,
            "final_evidence": d01_evidence[-1][1] if d01_evidence else None,
            "transitions": [
                row for row in transition_rows if row["slot_id"] == "D01"
            ],
        },
        "exclusion": {
            "id": APPROVED_EXCLUSION_ID,
            "points_source_3840x2160": APPROVED_EXCLUSION_POINTS,
            "rule": "center_inside AND bbox_overlap >= threshold",
            "excluded_detections": excluded_detection_count,
            "frames_with_exclusions": excluded_frame_count,
            "excluded_detections_assigned_before_filter": excluded_assigned_count,
            "frames_where_slot_evidence_changed": exclusion_changed_evidence_frames,
            "audit_csv": str(excluded_csv),
        },
        "important_slot_transitions": {
            identifier: [
                row for row in transition_rows if row["slot_id"] == identifier
            ]
            for identifier in ("D01", "D08", "D13", "D19", "D20")
        },
        "transitions_after_15_seconds": [
            row
            for row in transition_rows
            if float(row["timestamp_seconds"]) >= 15.0
        ],
        "evidence_counts": {
            identifier: dict(counts)
            for identifier, counts in evidence_counts.items()
        },
        "checks": {
            "state_count_invariant": "verified for every processed frame",
            "one_detection_assigned_to_at_most_one_slot": assignment_uniqueness_verified,
            "rapid_transitions_under_0_5_seconds": rapid_transitions,
            "uncertain_slots_at_least_10_percent": uncertain_slots,
        },
        "warnings": warnings,
        "limitations": [
            "No ground-truth slot labels are available for metric calculation.",
            "EMPTY is inferred from sustained absence of an accepted detection; it is not proof that a slot is physically empty.",
            "YOLO false-positive boxes outside configured slots may still be visible but do not affect slot counts.",
        ],
        "transition_samples": transition_samples,
        "samples": sorted(str(path) for path in sample_dir.glob("*_annotated.jpg")),
    }
    summary_json.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("=== YOLO + POLYGON COMPLETE ===", flush=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
