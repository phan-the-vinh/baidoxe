"""Read-only access to the preprocessed YOLO + polygon demo artifacts."""

import csv
import hashlib
import json
from functools import lru_cache
from pathlib import Path

from ..config import ROOT


DEMO_OUTPUT_DIR = (
    ROOT
    / "runs"
    / "detect"
    / "polygon_tests"
    / "yolo_car_v12_best"
    / "full_video_d16_logic_fix_conf040"
)
DEMO_VIDEO_PATH = DEMO_OUTPUT_DIR / "yolo_polygon_annotated_h264.mp4"
DEMO_STATES_PATH = DEMO_OUTPUT_DIR / "slot_states.csv"
DEMO_TRANSITIONS_PATH = DEMO_OUTPUT_DIR / "state_transitions.csv"
DEMO_SUMMARY_PATH = DEMO_OUTPUT_DIR / "summary.json"

SLOT_IDS = tuple(f"D{number:02d}" for number in range(1, 25))
VALID_STATES = frozenset({"OCCUPIED", "EMPTY", "UNKNOWN"})
CSV_PREFIX = (
    "frame_index",
    "timestamp_seconds",
    "occupied_count",
    "empty_count",
    "unknown_count",
)


class DemoPlaybackDataError(RuntimeError):
    """The immutable demo artifacts are missing or inconsistent."""


def _integer(row, name, line_number):
    try:
        return int(row[name])
    except (KeyError, TypeError, ValueError) as exc:
        raise DemoPlaybackDataError(
            f"Dòng {line_number}: cột {name} không phải số nguyên hợp lệ."
        ) from exc


def _seconds(row, line_number):
    try:
        value = float(row["timestamp_seconds"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DemoPlaybackDataError(
            f"Dòng {line_number}: timestamp_seconds không hợp lệ."
        ) from exc
    if value < 0:
        raise DemoPlaybackDataError(
            f"Dòng {line_number}: timestamp_seconds không được âm."
        )
    return value


def _timeline_entry(frame_index, timestamp, states):
    occupied = states.count("OCCUPIED")
    empty = states.count("EMPTY")
    unknown = states.count("UNKNOWN")
    return {
        "frame": frame_index,
        "time": timestamp,
        "counts": {
            "occupied": occupied,
            "empty": empty,
            "unknown": unknown,
        },
        # The slot order is declared once in slot_ids to keep the response compact.
        "states": list(states),
    }


@lru_cache(maxsize=4)
def _load_timeline_cached(path_text, size, modified_ns):
    del size, modified_ns  # They are part of the cache key and intentionally unused below.
    path = Path(path_text)
    expected_headers = CSV_PREFIX + SLOT_IDS
    timeline = []
    row_count = 0
    previous_timestamp = -1.0
    previous_states = None
    first_timestamp = None
    last_timestamp = None

    try:
        source = path.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise DemoPlaybackDataError(f"Không thể đọc dữ liệu demo: {path}") from exc

    with source:
        reader = csv.DictReader(source)
        if tuple(reader.fieldnames or ()) != expected_headers:
            raise DemoPlaybackDataError(
                "Cấu trúc slot_states.csv không đúng 5 cột thông tin và D01–D24."
            )

        for line_number, row in enumerate(reader, start=2):
            frame_index = _integer(row, "frame_index", line_number)
            if frame_index != row_count:
                raise DemoPlaybackDataError(
                    f"Dòng {line_number}: frame_index phải liên tục từ 0."
                )

            timestamp = _seconds(row, line_number)
            if timestamp < previous_timestamp:
                raise DemoPlaybackDataError(
                    f"Dòng {line_number}: thời gian không được giảm."
                )

            states = tuple(row[slot_id] for slot_id in SLOT_IDS)
            invalid = [state for state in states if state not in VALID_STATES]
            if invalid:
                raise DemoPlaybackDataError(
                    f"Dòng {line_number}: trạng thái ô đỗ không hợp lệ."
                )

            actual_counts = (
                states.count("OCCUPIED"),
                states.count("EMPTY"),
                states.count("UNKNOWN"),
            )
            declared_counts = tuple(
                _integer(row, name, line_number)
                for name in CSV_PREFIX[2:]
            )
            if actual_counts != declared_counts or sum(actual_counts) != len(SLOT_IDS):
                raise DemoPlaybackDataError(
                    f"Dòng {line_number}: tổng/trạng thái D01–D24 không khớp các cột đếm."
                )

            if states != previous_states:
                timeline.append(_timeline_entry(frame_index, timestamp, states))
                previous_states = states

            if first_timestamp is None:
                first_timestamp = timestamp
            last_timestamp = timestamp
            previous_timestamp = timestamp
            row_count += 1

    if not row_count:
        raise DemoPlaybackDataError("slot_states.csv không có dữ liệu.")
    if timeline[0]["states"] != ["UNKNOWN"] * len(SLOT_IDS):
        raise DemoPlaybackDataError("Khung hình đầu tiên phải có 24 ô ở trạng thái UNKNOWN.")

    frame_step = (
        (last_timestamp - first_timestamp) / (row_count - 1)
        if row_count > 1
        else 0.0
    )
    fps = 1.0 / frame_step if frame_step > 0 else None
    duration = last_timestamp + frame_step
    return {
        "slot_ids": list(SLOT_IDS),
        "states": sorted(VALID_STATES),
        "total_slots": len(SLOT_IDS),
        "total_frames": row_count,
        "fps": round(fps, 6) if fps else None,
        "duration_seconds": round(duration, 6),
        "timeline": timeline,
    }


def load_demo_timeline(path=None):
    """Validate the complete CSV and return a compact, deterministic timeline."""
    source = Path(path) if path else DEMO_STATES_PATH
    try:
        stat = source.stat()
    except OSError as exc:
        raise DemoPlaybackDataError(f"Không tìm thấy dữ liệu demo: {source}") from exc
    return _load_timeline_cached(
        str(source.resolve()), stat.st_size, stat.st_mtime_ns
    )


def _validate_transitions(payload):
    expected_header = (
        "frame_index",
        "timestamp_seconds",
        "slot_id",
        "previous_state",
        "new_state",
        "evidence",
    )
    actual = []
    try:
        source = DEMO_TRANSITIONS_PATH.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise DemoPlaybackDataError(
            f"Không thể đọc transition demo: {DEMO_TRANSITIONS_PATH}"
        ) from exc
    with source:
        reader = csv.DictReader(source)
        if tuple(reader.fieldnames or ()) != expected_header:
            raise DemoPlaybackDataError("Cấu trúc state_transitions.csv không hợp lệ.")
        for line_number, row in enumerate(reader, start=2):
            try:
                frame = int(row["frame_index"])
                timestamp = round(float(row["timestamp_seconds"]), 6)
            except (TypeError, ValueError) as exc:
                raise DemoPlaybackDataError(
                    f"Dòng {line_number}: frame/timestamp transition không hợp lệ."
                ) from exc
            slot_id = row["slot_id"]
            previous = row["previous_state"]
            current = row["new_state"]
            if (
                slot_id not in SLOT_IDS
                or previous not in VALID_STATES
                or current not in VALID_STATES
                or previous == current
                or row["evidence"] not in {"occupied", "clear", "uncertain"}
            ):
                raise DemoPlaybackDataError(
                    f"Dòng {line_number}: transition không hợp lệ."
                )
            actual.append((frame, timestamp, slot_id, previous, current))

    derived = []
    previous_states = ["UNKNOWN"] * len(SLOT_IDS)
    for snapshot in payload["timeline"]:
        for index, slot_id in enumerate(SLOT_IDS):
            current = snapshot["states"][index]
            previous = previous_states[index]
            if current != previous:
                derived.append(
                    (
                        snapshot["frame"],
                        round(snapshot["time"], 6),
                        slot_id,
                        previous,
                        current,
                    )
                )
        previous_states = list(snapshot["states"])
    if actual != derived:
        raise DemoPlaybackDataError(
            "state_transitions.csv không khớp các thay đổi trong slot_states.csv."
        )


@lru_cache(maxsize=4)
def _load_demo_bundle_cached(signatures):
    del signatures
    payload = load_demo_timeline()
    _validate_transitions(payload)
    try:
        summary = json.loads(DEMO_SUMMARY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DemoPlaybackDataError("Không thể đọc summary.json của demo.") from exc
    source = summary.get("source") or {}
    final_counts = summary.get("final_counts") or {}
    if (
        int(source.get("frames", 0)) != payload["total_frames"]
        or abs(float(source.get("fps", 0)) - float(payload["fps"])) > 0.001
        or final_counts
        != {"OCCUPIED": 22, "EMPTY": 2, "UNKNOWN": 0}
    ):
        raise DemoPlaybackDataError("summary.json không khớp bộ demo cuối.")

    try:
        import cv2

        capture = cv2.VideoCapture(str(DEMO_VIDEO_PATH))
        if not capture.isOpened():
            raise DemoPlaybackDataError("Không mở được video H.264 của demo.")
        frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        capture.release()
    except DemoPlaybackDataError:
        raise
    except Exception as exc:
        raise DemoPlaybackDataError("Không đọc được metadata video demo.") from exc
    if (
        frames != payload["total_frames"]
        or abs(fps - float(payload["fps"])) > 0.001
        or (width, height) != (1920, 1080)
    ):
        raise DemoPlaybackDataError(
            "Video H.264 không khớp frame/FPS/độ phân giải của slot_states.csv."
        )

    version_source = "|".join(
        f"{path.name}:{path.stat().st_size}:{path.stat().st_mtime_ns}"
        for path in (
            DEMO_VIDEO_PATH,
            DEMO_STATES_PATH,
            DEMO_TRANSITIONS_PATH,
            DEMO_SUMMARY_PATH,
        )
    )
    result = dict(payload)
    result["artifact_version"] = hashlib.sha256(
        version_source.encode("utf-8")
    ).hexdigest()[:16]
    result["video"] = {
        "frames": frames,
        "fps": round(fps, 6),
        "duration_seconds": round(frames / fps, 6),
        "width": width,
        "height": height,
    }
    return result


def load_demo_bundle():
    paths = (
        DEMO_VIDEO_PATH,
        DEMO_STATES_PATH,
        DEMO_TRANSITIONS_PATH,
        DEMO_SUMMARY_PATH,
    )
    try:
        signatures = tuple(
            (str(path.resolve()), path.stat().st_size, path.stat().st_mtime_ns)
            for path in paths
        )
    except OSError as exc:
        raise DemoPlaybackDataError("Bộ tệp demo cuối không đầy đủ.") from exc
    return _load_demo_bundle_cached(signatures)


def timeline_state_at(payload, seconds):
    """Return the last known state at a playback time (same rule used in JS)."""
    timeline = payload["timeline"]
    target = max(0.0, float(seconds))
    low, high = 0, len(timeline) - 1
    match = 0
    while low <= high:
        middle = (low + high) // 2
        if timeline[middle]["time"] <= target:
            match = middle
            low = middle + 1
        else:
            high = middle - 1
    return timeline[match]
