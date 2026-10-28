"""Asynchronous, data-only YOLO + polygon analysis for raw /vision videos."""

from __future__ import annotations

import csv
import hashlib
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from functools import lru_cache
from pathlib import Path

import cv2

from ..config import ROOT
from .common import BusinessError


MAX_VIDEO_BYTES = 250 * 1024 * 1024
ALLOWED_EXTENSIONS = {".mp4", ".webm", ".mov", ".m4v"}
EXPECTED_ASPECT = 3840 / 2160
SLOT_IDS = tuple(f"D{number:02d}" for number in range(1, 25))
VALID_STATES = {"OCCUPIED", "EMPTY", "UNKNOWN"}

WEIGHTS_PATH = (
    ROOT
    / "runs/detect/runs/yolo_car_v12/train_27_from_smoke01_best/weights/best.pt"
)
POLYGON_PATH = ROOT / "config/demo_parking/0922_1_polygons.json"
PROCESSOR_PATH = ROOT / "tools/analyze_raw_parking_video.py"

# Video checksums verified to match config/demo_parking/0922_1_polygons.json camera angle & layout
COMPATIBLE_POLYGON_SOURCES: dict[str, Path] = {
    "6619fb5d58ac6c6032409ac68b7d971ca1cd71d5ef8d5cb9db7c9ddcd47f72f9": POLYGON_PATH,
}


def is_polygon_compatible(job: dict | None) -> bool:
    if not job:
        return False
    sha = (job.get("input_sha256") or "").lower().strip()
    return sha in COMPATIBLE_POLYGON_SOURCES

# Deliberately separate from the removed annotated-video /vision job namespace.
UPLOAD_ROOT = ROOT / "instance/parking_analysis_uploads"
RESULT_ROOT = ROOT / "instance/parking_analysis_results"
MANIFEST_ROOT = ROOT / "instance/parking_analysis_manifests"

_jobs: dict[str, dict] = {}
_jobs_lock = threading.RLock()
_gpu_gate = threading.BoundedSemaphore(1)
_progress_pattern = re.compile(r"Processed\s+(\d+)/(\d+)\s+frames")


def _yolo_python() -> Path:
    configured = (os.getenv("YOLO_TRAIN_PYTHON") or "").strip().strip('"\'')
    if configured:
        return Path(configured).resolve()
    for name in ("AIbaidoxe", "yolo_train"):
        expected = Path(rf"D:\miniconda\envs\{name}\python.exe")
        if expected.is_file():
            return expected
    current = Path(sys.executable).resolve()
    for name in ("AIbaidoxe", "yolo_train"):
        candidate = current.parents[1] / name / current.name
        if candidate.is_file():
            return candidate
    return current.parents[1] / "yolo_train" / current.name


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=16)
def _cached_sha256(path_text: str, size: int, modified_ns: int) -> str:
    del size, modified_ns
    return _sha256(Path(path_text))


def _manifest_path(job_id: str) -> Path:
    return MANIFEST_ROOT / f"{job_id}.json"


def _persist(job: dict) -> None:
    MANIFEST_ROOT.mkdir(parents=True, exist_ok=True)
    path = _manifest_path(job["id"])
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _set_job(job: dict, **changes) -> None:
    with _jobs_lock:
        job.update(changes)
        job["updated_at"] = time.time()
        _persist(job)


def _load_job(job_id: str) -> dict | None:
    if not re.fullmatch(r"[0-9a-f]{32}", job_id or ""):
        return None
    with _jobs_lock:
        if job_id in _jobs:
            return _jobs[job_id]
        path = _manifest_path(job_id)
        if not path.is_file():
            return None
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if job.get("status") in {"queued", "processing"}:
            job["status"] = "failed"
            job["error"] = (
                "Flask đã khởi động lại khi video đang được phân tích. "
                "Hãy tải lại video để chạy một job mới."
            )
            _persist(job)
        _jobs[job_id] = job
        return job


def _validate_polygon_config() -> dict:
    payload = json.loads(POLYGON_PATH.read_text(encoding="utf-8"))
    if payload.get("coordinate_space") != {"width": 3840, "height": 2160}:
        raise ValueError("Hệ tọa độ Polygon phải là 3840×2160.")
    polygons = payload.get("polygons") or []
    parking = [item for item in polygons if item.get("type") == "parking_space"]
    if [item.get("id") for item in parking] != list(SLOT_IDS):
        raise ValueError("Cấu hình phải có đúng 24 Polygon D01–D24 theo thứ tự.")
    exclusions = [item for item in polygons if item.get("type") == "exclusion_zone"]
    expected_exclusion = {
        "id": "X01",
        "type": "exclusion_zone",
        "points": [[1436, 1360], [1748, 1360], [1748, 2150], [1436, 2150]],
    }
    if exclusions != [expected_exclusion]:
        raise ValueError("Vùng loại trừ X01 không đúng cấu hình đã duyệt.")
    return payload


def status() -> dict:
    checks = {
        "weights": WEIGHTS_PATH.is_file(),
        "polygons": POLYGON_PATH.is_file(),
        "processor": PROCESSOR_PATH.is_file(),
        "yolo_python": _yolo_python().is_file(),
    }
    error = None
    if checks["polygons"]:
        try:
            _validate_polygon_config()
        except (OSError, ValueError, KeyError) as exc:
            checks["polygons"] = False
            error = str(exc)
    ready = all(checks.values())
    if ready:
        message = (
            "YOLOv8n inference, 24 Polygon D01–D24 và X01 đã sẵn sàng. "
            "Video gốc chỉ được đọc; kết quả là dữ liệu CSV/JSON và không ghi SQL."
        )
    else:
        missing = ", ".join(name for name, available in checks.items() if not available)
        message = error or f"Thiếu thành phần phân tích video: {missing}."
    return {
        "ready": ready,
        "message": message,
        "checks": checks,
        "weights": str(WEIGHTS_PATH),
        "polygons": str(POLYGON_PATH),
        "worker": str(PROCESSOR_PATH),
        "training": False,
        "annotated_video": False,
    }


def _fourcc(capture: cv2.VideoCapture) -> str:
    code = int(capture.get(cv2.CAP_PROP_FOURCC))
    return "".join(chr((code >> (8 * index)) & 0xFF) for index in range(4)).strip()


def _video_metadata(path: Path) -> dict:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise BusinessError("Không đọc được video đã tải lên.", 400)
    try:
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        codec = _fourcc(capture)
        first_ok, _frame = capture.read()
    finally:
        capture.release()
    if min(width, height, fps, frames) <= 0 or not first_ok:
        raise BusinessError(
            "Video thiếu metadata hoặc không đọc được khung hình đầu.", 400
        )
    if abs(width / height - EXPECTED_ASPECT) / EXPECTED_ASPECT > 0.01:
        raise BusinessError(
            "Video phải có tỷ lệ 16:9 để dùng đúng Polygon camera cố định.", 400
        )
    return {
        "width": width,
        "height": height,
        "fps": fps,
        "frames": frames,
        "duration_seconds": frames / fps,
        "codec": codec,
    }


def _public(job: dict) -> dict:
    payload = {
        key: job.get(key)
        for key in (
            "id",
            "status",
            "progress_frames",
            "total_frames",
            "error",
            "metadata",
            "input_sha256",
            "created_at",
            "updated_at",
        )
    }
    payload["has_compatible_polygons"] = is_polygon_compatible(job)
    return payload


def get_job(job_id: str | None, owner_id: int) -> dict | None:
    job = _load_job(job_id or "")
    if job is None or int(job.get("owner_id", -1)) != int(owner_id):
        return None
    return job


def public_job(job_id: str | None, owner_id: int) -> dict | None:
    job = get_job(job_id, owner_id)
    return _public(job) if job else None


def _run_worker(job: dict, command: list[str]) -> None:
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creation_flags,
    )
    tail: list[str] = []
    assert process.stdout is not None
    for raw_line in process.stdout:
        line = raw_line.strip()
        if line:
            tail.append(line)
            tail = tail[-12:]
        match = _progress_pattern.search(line)
        if match:
            _set_job(
                job,
                progress_frames=int(match.group(1)),
                total_frames=int(match.group(2)),
            )
    return_code = process.wait()
    if return_code:
        detail = tail[-1] if tail else f"mã thoát {return_code}"
        raise RuntimeError(f"Phân tích video thất bại: {detail}")


def _validate_timeline(path: Path, expected_frames: int) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("Không đọc được timeline.json của job.") from exc
    if payload.get("slot_ids") != list(SLOT_IDS):
        raise RuntimeError("timeline.json không có đúng D01–D24.")
    if payload.get("total_frames") != expected_frames:
        raise RuntimeError("Số frame trong timeline không khớp video nguồn.")
    if payload.get("total_slots") != 24 or not payload.get("timeline"):
        raise RuntimeError("timeline.json thiếu dữ liệu 24 ô.")
    if payload.get("coordinate_space") != {"width": 3840, "height": 2160}:
        raise RuntimeError("timeline.json có hệ tọa độ Polygon không hợp lệ.")
    if [item.get("id") for item in payload.get("polygons", [])] != list(SLOT_IDS):
        raise RuntimeError("timeline.json thiếu Polygon D01–D24.")
    for entry in payload["timeline"]:
        states = entry.get("states") or []
        counts = entry.get("counts") or {}
        if len(states) != 24 or any(state not in VALID_STATES for state in states):
            raise RuntimeError("timeline.json chứa trạng thái ô không hợp lệ.")
        total = sum(
            int(counts.get(key, -1000)) for key in ("occupied", "empty", "unknown")
        )
        if total != 24:
            raise RuntimeError("Tổng trạng thái trong timeline.json không bằng 24.")
    return payload


def _process(job: dict) -> None:
    try:
        with _gpu_gate:
            _set_job(job, status="processing", progress_frames=0, error=None)
            run_dir = RESULT_ROOT / job["id"]
            command = [
                str(_yolo_python()),
                "-u",
                str(PROCESSOR_PATH),
                "--video",
                job["input_path"],
                "--model",
                str(WEIGHTS_PATH),
                "--polygons",
                str(POLYGON_PATH),
                "--output",
                str(run_dir),
                "--conf",
                "0.40",
                "--iou",
                "0.50",
                "--imgsz",
                "512",
                "--device",
                "0",
                "--analysis-width",
                "1920",
                "--bbox-overlap",
                "0.45",
                "--slot-overlap",
                "0.12",
                "--uncertain-overlap",
                "0.10",
                "--minimum-axis-elongation",
                "1.25",
                "--core-horizontal-inset",
                "0.15",
                "--core-top-inset",
                "0.10",
                "--core-bottom-inset",
                "0.25",
                "--border-margin-ratio",
                "0.01",
                "--exclusion-overlap",
                "0.60",
                "--occupied-seconds",
                "0.75",
                "--empty-seconds",
                "1.50",
                "--release-seconds",
                "2.00",
                "--ambiguous-seconds",
                "0.25",
            ]
            _run_worker(job, command)

            video_artifacts = list(run_dir.glob("*.mp4")) + list(run_dir.glob("*.webm"))
            if video_artifacts:
                raise RuntimeError("Worker đã tạo video kết quả ngoài thiết kế data-only.")
            timeline_path = run_dir / "timeline.json"
            _validate_timeline(timeline_path, int(job["total_frames"]))
            summary_path = run_dir / "analysis_summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            if summary.get("input_sha256") != job["input_sha256"]:
                raise RuntimeError("Checksum video nguồn thay đổi trong quá trình phân tích.")
            if summary.get("processing", {}).get("annotated_video_created") is not False:
                raise RuntimeError("Worker không xác nhận chế độ không tạo video annotated.")
            _set_job(
                job,
                status="complete",
                progress_frames=job["total_frames"],
                timeline_path=str(timeline_path.resolve()),
                states_path=str((run_dir / "slot_states.csv").resolve()),
                transitions_path=str((run_dir / "state_transitions.csv").resolve()),
                summary_path=str(summary_path.resolve()),
            )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        _set_job(job, status="failed", error=str(exc))


def create_job(upload, owner_id: int) -> dict:
    readiness = status()
    if not readiness["ready"]:
        raise BusinessError(readiness["message"], 503)
    if not upload or not upload.filename:
        raise BusinessError("Hãy chọn video gốc cần phân tích.")
    extension = Path(upload.filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise BusinessError("Video phải là MP4, WebM, MOV hoặc M4V.")

    job_id = uuid.uuid4().hex
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    RESULT_ROOT.mkdir(parents=True, exist_ok=True)
    destination = UPLOAD_ROOT / f"{job_id}{extension}"
    temporary = destination.with_suffix(destination.suffix + ".uploading")
    size = 0
    try:
        with temporary.open("xb") as output:
            while True:
                chunk = upload.stream.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_VIDEO_BYTES:
                    raise BusinessError("Video tối đa 250 MB.", 413)
                output.write(chunk)
        if size == 0:
            raise BusinessError("Video tải lên rỗng.")
        temporary.replace(destination)
        metadata = _video_metadata(destination)
        checksum = _sha256(destination)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        if destination.exists():
            destination.unlink()
        raise

    now = time.time()
    job = {
        "id": job_id,
        "owner_id": int(owner_id),
        "status": "queued",
        "progress_frames": 0,
        "total_frames": metadata["frames"],
        "error": None,
        "metadata": metadata,
        "original_filename": Path(upload.filename).name,
        "input_path": str(destination.resolve()),
        "input_size": size,
        "input_sha256": checksum,
        "timeline_path": None,
        "states_path": None,
        "transitions_path": None,
        "summary_path": None,
        "created_at": now,
        "updated_at": now,
    }
    with _jobs_lock:
        _jobs[job_id] = job
        _persist(job)
    threading.Thread(
        target=_process,
        args=(job,),
        name=f"parking-analysis-{job_id[:8]}",
        daemon=True,
    ).start()
    return _public(job)


def source_video(job_id: str | None, owner_id: int) -> tuple[Path, str]:
    job = get_job(job_id, owner_id)
    if not job or job.get("status") != "complete":
        raise BusinessError("Video chưa phân tích xong.", 404)
    path = Path(job["input_path"])
    if not path.is_file():
        raise BusinessError("Không tìm thấy video gốc của job.", 404)
    stat = path.stat()
    actual_hash = _cached_sha256(str(path.resolve()), stat.st_size, stat.st_mtime_ns)
    if stat.st_size != int(job["input_size"]) or actual_hash != job["input_sha256"]:
        raise BusinessError("Video gốc không còn khớp checksum lúc tải lên.", 409)
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return path, mime


def timeline(job_id: str | None, owner_id: int) -> dict:
    job = get_job(job_id, owner_id)
    if not job or job.get("status") != "complete":
        raise BusinessError("Dữ liệu phân tích video chưa sẵn sàng.", 404)
    payload = _validate_timeline(Path(job["timeline_path"]), int(job["total_frames"]))
    payload["vision_job_id"] = job["id"]
    payload["has_compatible_polygons"] = is_polygon_compatible(job)
    return payload


def get_job_detections(job_id: str | None, owner_id: int) -> dict:
    job = get_job(job_id, owner_id)
    if not job or job.get("status") != "complete":
        raise BusinessError("Dữ liệu nhận diện video chưa sẵn sàng.", 404)

    metadata = job.get("metadata") or {}
    fps = float(metadata.get("fps") or 30.0)
    total_frames = int(metadata.get("frames") or job.get("total_frames") or 0)
    compatible = is_polygon_compatible(job)

    run_dir = RESULT_ROOT / job["id"]
    detections_csv = run_dir / "detections.csv"

    frames: dict[str, list[dict]] = {}
    total_detections = 0

    if detections_csv.is_file():
        try:
            with detections_csv.open("r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    frame_idx = str(int(row["frame_index"]))
                    x1 = round(float(row["x1"]), 2)
                    y1 = round(float(row["y1"]), 2)
                    x2 = round(float(row["x2"]), 2)
                    y2 = round(float(row["y2"]), 2)
                    conf = round(float(row["confidence"]), 3)
                    assigned_slot = (row.get("assigned_slot") or "").strip()
                    slot = assigned_slot if (compatible and assigned_slot) else None
                    frames.setdefault(frame_idx, []).append({
                        "box": [x1, y1, x2, y2],
                        "confidence": conf,
                        "slot": slot,
                    })
                    total_detections += 1
        except (OSError, ValueError):
            pass

    return {
        "vision_job_id": job["id"],
        "fps": fps,
        "total_frames": total_frames,
        "coordinate_space": {"width": 1920, "height": 1080},
        "has_compatible_polygons": compatible,
        "total_detections": total_detections,
        "frames": frames,
    }

