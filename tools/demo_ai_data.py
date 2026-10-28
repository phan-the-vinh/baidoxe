from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_DIR = ROOT / (
    "runs/detect/polygon_tests/yolo_car_v12_best/"
    "full_video_d16_logic_fix_conf040"
)
SLOT_IDS = tuple(f"D{number:02d}" for number in range(1, 25))
STATES = frozenset({"EMPTY", "OCCUPIED", "UNKNOWN"})
EVIDENCE_VALUES = frozenset({"occupied", "clear", "uncertain"})
SIX_PLACES = Decimal("0.000001")


@dataclass(frozen=True)
class DemoData:
    run_dir: Path
    summary: dict
    state_rows: list[dict]
    transition_rows: list[dict]
    hashes: dict[str, str]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _decimal(value: str, field: str) -> Decimal:
    try:
        return Decimal(value)
    except Exception as exc:
        raise ValueError(f"{field} không phải số hợp lệ: {value!r}") from exc


def validate_demo_data(run_dir: Path = DEFAULT_RUN_DIR) -> DemoData:
    run_dir = run_dir.resolve()
    states_path = run_dir / "slot_states.csv"
    transitions_path = run_dir / "state_transitions.csv"
    summary_path = run_dir / "summary.json"
    video_path = run_dir / "yolo_polygon_annotated_h264.mp4"
    for path in (states_path, transitions_path, summary_path, video_path):
        if not path.is_file():
            raise ValueError(f"Thiếu tệp đầu vào: {path}")

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    fps = Decimal(str(summary["source"]["fps"]))
    expected_frames = int(summary["source"]["frames"])
    if fps <= 0 or expected_frames <= 0:
        raise ValueError("FPS hoặc số frame trong summary.json không hợp lệ.")

    expected_state_header = [
        "frame_index",
        "timestamp_seconds",
        "occupied_count",
        "empty_count",
        "unknown_count",
        *SLOT_IDS,
    ]
    state_rows: list[dict] = []
    derived_transitions: list[tuple] = []
    previous = dict(summary.get("initial_states") or {slot: "UNKNOWN" for slot in SLOT_IDS})
    if set(previous) != set(SLOT_IDS) or any(value not in STATES for value in previous.values()):
        raise ValueError("initial_states không chứa đúng D01-D24 hoặc có trạng thái sai.")

    with states_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != expected_state_header:
            raise ValueError("Header slot_states.csv không đúng cấu trúc đã xác nhận.")
        for expected_index, raw in enumerate(reader):
            frame_index = int(raw["frame_index"])
            timestamp = _decimal(raw["timestamp_seconds"], "timestamp_seconds")
            if frame_index != expected_index:
                raise ValueError(f"Frame bị thiếu/trùng tại vị trí {expected_index}: {frame_index}")
            expected_time = (Decimal(frame_index) / fps).quantize(SIX_PLACES, ROUND_HALF_UP)
            if timestamp != expected_time:
                raise ValueError(
                    f"Timestamp frame {frame_index} sai: {timestamp} != {expected_time}"
                )
            states = {slot: raw[slot] for slot in SLOT_IDS}
            invalid = {slot: value for slot, value in states.items() if value not in STATES}
            if invalid:
                raise ValueError(f"Trạng thái không hợp lệ tại frame {frame_index}: {invalid}")
            counts = {state: sum(value == state for value in states.values()) for state in STATES}
            declared = {
                "OCCUPIED": int(raw["occupied_count"]),
                "EMPTY": int(raw["empty_count"]),
                "UNKNOWN": int(raw["unknown_count"]),
            }
            if counts != declared or sum(declared.values()) != len(SLOT_IDS):
                raise ValueError(f"Tổng trạng thái sai tại frame {frame_index}.")
            for slot in SLOT_IDS:
                if states[slot] != previous[slot]:
                    derived_transitions.append(
                        (frame_index, timestamp, slot, previous[slot], states[slot])
                    )
            previous = states
            state_rows.append(
                {
                    "frame_index": frame_index,
                    "timestamp_seconds": timestamp,
                    "states": states,
                }
            )

    if len(state_rows) != expected_frames:
        raise ValueError(f"Số frame CSV {len(state_rows)} != summary {expected_frames}.")

    expected_transition_header = [
        "frame_index",
        "timestamp_seconds",
        "slot_id",
        "previous_state",
        "new_state",
        "evidence",
    ]
    transition_rows: list[dict] = []
    with transitions_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != expected_transition_header:
            raise ValueError("Header state_transitions.csv không đúng cấu trúc đã xác nhận.")
        for raw in reader:
            row = {
                "frame_index": int(raw["frame_index"]),
                "timestamp_seconds": _decimal(raw["timestamp_seconds"], "timestamp_seconds"),
                "slot_code": raw["slot_id"],
                "previous_state": raw["previous_state"],
                "new_state": raw["new_state"],
                "evidence": raw["evidence"],
            }
            if row["slot_code"] not in SLOT_IDS:
                raise ValueError(f"Mã ô transition sai: {row['slot_code']}")
            if row["previous_state"] not in STATES or row["new_state"] not in STATES:
                raise ValueError("Transition chứa trạng thái không hợp lệ.")
            if row["previous_state"] == row["new_state"]:
                raise ValueError("Transition không thay đổi trạng thái.")
            if row["evidence"] not in EVIDENCE_VALUES:
                raise ValueError(f"Evidence không có trong tập cho phép: {row['evidence']}")
            transition_rows.append(row)

    actual_transitions = [
        (
            row["frame_index"],
            row["timestamp_seconds"],
            row["slot_code"],
            row["previous_state"],
            row["new_state"],
        )
        for row in transition_rows
    ]
    if actual_transitions != derived_transitions:
        raise ValueError("state_transitions.csv không khớp thay đổi suy ra từ slot_states.csv.")

    hashes = {
        "states_csv": sha256_file(states_path),
        "transitions_csv": sha256_file(transitions_path),
        "processed_video": sha256_file(video_path),
    }
    return DemoData(run_dir, summary, state_rows, transition_rows, hashes)

