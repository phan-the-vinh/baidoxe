"""Replay saved YOLO detections to compare vehicle-core geometry candidates."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

from parking_polygon_config import load_config
from yolo_polygon_logic import TemporalSlotState, assign_detections_to_slots, build_slots


CANDIDATES = {
    "h10_t10_b10": (0.10, 0.10, 0.10),
    "h15_t10_b20": (0.15, 0.10, 0.20),
    "h15_t10_b25": (0.15, 0.10, 0.25),
    "h20_t10_b25": (0.20, 0.10, 0.25),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detections", required=True, type=Path)
    parser.add_argument("--polygons", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--frames", type=int, default=1623)
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite: {output}")

    config = load_config(args.polygons.resolve())
    source_size = (
        int(config["coordinate_space"]["width"]),
        int(config["coordinate_space"]["height"]),
    )
    slots = build_slots(config["polygons"], source_size, (args.width, args.height))
    slot_ids = [slot.identifier for slot in slots]
    by_frame: dict[int, list[dict[str, float | int]]] = defaultdict(list)
    reference_assignments: dict[int, dict[int, str]] = defaultdict(dict)
    with args.detections.resolve().open(newline="", encoding="utf-8") as source:
        for row in csv.DictReader(source):
            frame_index = int(row["frame_index"])
            detection_index = int(row["detection_index"])
            by_frame[frame_index].append(
                {
                    "class_id": 0,
                    "confidence": float(row["confidence"]),
                    "x1": float(row["x1"]),
                    "y1": float(row["y1"]),
                    "x2": float(row["x2"]),
                    "y2": float(row["y2"]),
                }
            )
            if row["assigned_slot"]:
                reference_assignments[frame_index][detection_index] = row["assigned_slot"]

    results: dict[str, object] = {
        "source_detections": str(args.detections.resolve()),
        "frames": args.frames,
        "fps": args.fps,
        "candidates": {},
    }
    sample_frames = {"3.0": 90, "3.8": 114, "5.0": 150, "19.5": 585, "33.433333": 1003}
    for name, (horizontal, top, bottom) in CANDIDATES.items():
        states = {
            slot_id: TemporalSlotState(slot_id, 23, 45, 60, 8)
            for slot_id in slot_ids
        }
        transitions: list[dict[str, object]] = []
        samples: dict[str, dict[str, str]] = {}
        assigned_frames = {slot_id: 0 for slot_id in slot_ids}
        assignment_differences: list[dict[str, object]] = []
        candidate_assignment_count = 0
        for frame_index in range(args.frames):
            matches, evidence = assign_detections_to_slots(
                by_frame.get(frame_index, []),
                slots,
                minimum_bbox_overlap=0.45,
                minimum_slot_overlap=0.12,
                uncertain_overlap=0.10,
                minimum_axis_elongation=1.25,
                core_horizontal_inset=horizontal,
                core_top_inset=top,
                core_bottom_inset=bottom,
                frame_size=(args.width, args.height),
                border_margin_ratio=0.01,
            )
            for slot_id in matches:
                assigned_frames[slot_id] += 1
            candidate_assignments = {
                int(match["detection_index"]): slot_id
                for slot_id, match in matches.items()
            }
            candidate_assignment_count += len(candidate_assignments)
            reference = reference_assignments.get(frame_index, {})
            for detection_index in sorted(set(reference) | set(candidate_assignments)):
                before = reference.get(detection_index, "")
                after = candidate_assignments.get(detection_index, "")
                if before != after:
                    assignment_differences.append(
                        {
                            "frame": frame_index,
                            "time": round(frame_index / args.fps, 6),
                            "detection_index": detection_index,
                            "before": before,
                            "after": after,
                        }
                    )
            for slot_id in slot_ids:
                change = states[slot_id].update(evidence[slot_id])
                if change:
                    transitions.append(
                        {
                            "frame": frame_index,
                            "time": round(frame_index / args.fps, 6),
                            "slot": slot_id,
                            "from": change[0],
                            "to": change[1],
                        }
                    )
            for label, sample_frame in sample_frames.items():
                if frame_index == sample_frame:
                    samples[label] = {
                        slot_id: states[slot_id].state for slot_id in slot_ids
                    }
        results["candidates"][name] = {
            "core_insets": {
                "horizontal": horizontal,
                "top": top,
                "bottom": bottom,
            },
            "transition_count": len(transitions),
            "transitions": transitions,
            "samples": samples,
            "assigned_frames": assigned_frames,
            "reference_assignment_count": sum(
                len(items) for items in reference_assignments.values()
            ),
            "candidate_assignment_count": candidate_assignment_count,
            "assignment_difference_count": len(assignment_differences),
            "assignment_differences": assignment_differences,
            "final_states": {
                slot_id: states[slot_id].state for slot_id in slot_ids
            },
        }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
