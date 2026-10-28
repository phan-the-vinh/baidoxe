"""Read-only Car -> Plate inference bridge for the Flask application."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import cv2
import numpy as np

from ..config import ROOT
from .common import BusinessError


CAR_MODEL = ROOT / "runs/detect/runs/yolo_car_v12/train_27_from_smoke01_best/weights/best.pt"
PLATE_MODEL = ROOT / "runs/detect/runs/license_plate/train_30/weights/best.pt"
WORKER = ROOT / "tools/detect_car_plates.py"
_inference_gate = threading.BoundedSemaphore(1)
MAX_TEMPORAL_FRAMES = 21
MAX_PLATE_FRAME_WINDOW_BYTES = 128 * 1024 * 1024


def _yolo_python() -> Path:
    configured = os.getenv("YOLO_TRAIN_PYTHON", "").strip()
    if configured:
        candidate = Path(configured).expanduser().resolve()
    else:
        executable = "python.exe" if os.name == "nt" else "bin/python"
        candidate = Path(sys.prefix).parent / "yolo_train" / executable
    if not candidate.is_file():
        raise BusinessError(
            "Không tìm thấy Python của môi trường yolo_train. Hãy cấu hình YOLO_TRAIN_PYTHON.", 503
        )
    return candidate


def readiness() -> dict:
    missing = [str(path) for path in (CAR_MODEL, PLATE_MODEL, WORKER) if not path.is_file()]
    try:
        python = _yolo_python()
    except BusinessError as exc:
        return {"ready": False, "message": str(exc), "missing": missing}
    if missing:
        return {"ready": False, "message": "Thiếu model hoặc worker nhận diện biển số.", "missing": missing}
    return {
        "ready": True,
        "message": "Car Detector, Plate Detector và Tesseract OCR đã sẵn sàng.",
        "car_model": str(CAR_MODEL),
        "plate_model": str(PLATE_MODEL),
        "python": str(python),
    }


def _frame_quality(result: dict) -> tuple[int, float]:
    scores = [
        float(item.get("plate_crop_quality_score") or 0.0)
        for item in result.get("vehicles", [])
        if item.get("plate_detected")
    ]
    return len(scores), round(sum(scores), 6)


def _select_temporal_result(results: list[dict], current_index: int) -> dict:
    if not results or not 0 <= current_index < len(results):
        raise BusinessError("Cửa sổ frame video không hợp lệ.")
    selected_index = max(range(len(results)), key=lambda index: _frame_quality(results[index]))
    current_quality, selected_quality = _frame_quality(results[current_index]), _frame_quality(results[selected_index])
    selected = results[selected_index]
    selected["temporal_selection"] = {
        "analyzed_frames": len(results),
        "current_index": current_index,
        "selected_index": selected_index,
        "selected_frame_offset": selected_index - current_index,
        "current_frame_quality": list(current_quality),
        "selected_frame_quality": list(selected_quality),
        "better_frame_selected": selected_index != current_index and selected_quality > current_quality,
    }
    return selected


def detect_vehicles_and_plates(frame: np.ndarray) -> dict:
    return detect_plate_frame_window([frame], 0)


def detect_plate_frame_window(frames: list[np.ndarray], current_index: int) -> dict:
    if not frames or len(frames) > MAX_TEMPORAL_FRAMES:
        raise BusinessError(f"Chỉ được gửi từ 1 đến {MAX_TEMPORAL_FRAMES} frame để phân tích.")
    if any(frame is None or not frame.size for frame in frames):
        raise BusinessError("Ảnh đầu vào không hợp lệ.")
    ready = readiness()
    if not ready["ready"]:
        raise BusinessError(ready["message"], 503)
    if not _inference_gate.acquire(blocking=False):
        raise BusinessError("AI đang xử lý ảnh khác. Hãy thử lại sau.", 429)
    try:
        with tempfile.TemporaryDirectory(prefix="parking_plate_") as temporary:
            image_paths = []
            for index, frame in enumerate(frames):
                image_path = Path(temporary) / f"frame_{index:03d}.png"
                if not cv2.imwrite(str(image_path), frame, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
                    raise BusinessError("Không chuẩn bị được khung hình để nhận diện.", 500)
                image_paths.append(image_path)
            process = subprocess.run(
                [
                    str(_yolo_python()), str(WORKER), *(str(path) for path in image_paths),
                    "--car-model", str(CAR_MODEL),
                    "--plate-model", str(PLATE_MODEL),
                    "--device", "0",
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
                check=False,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
        payload = None
        for line in reversed([line.strip() for line in process.stdout.splitlines() if line.strip()]):
            try:
                payload = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
        if process.returncode != 0 or not isinstance(payload, dict) or not payload.get("ok"):
            raise BusinessError("Không chạy được Car Detector/Plate Detector trên ảnh đã chọn.", 503)
        results = payload.get("results") if len(frames) > 1 else [payload]
        if not isinstance(results, list) or len(results) != len(frames):
            raise BusinessError("Worker trả dữ liệu cửa sổ frame không hợp lệ.", 503)
        selected = _select_temporal_result(results, current_index)
        return _enrich_with_ocr(selected, frames[current_index])
    except subprocess.TimeoutExpired:
        raise BusinessError("Nhận diện quá thời gian chờ. Hãy thử ảnh có độ phân giải nhỏ hơn.", 504) from None
    finally:
        _inference_gate.release()


def _enrich_with_ocr(result: dict, frame: np.ndarray | None = None) -> dict:
    import base64
    from tools.ocr_plate_crop_worker import recognize_exact_crop

    vehicles = result.get("vehicles", [])
    best_candidate = None

    for vehicle in vehicles:
        enhanced_b64 = vehicle.get("plate_crop_enhanced") or vehicle.get("plate_crop_original")
        if vehicle.get("plate_detected") and enhanced_b64:
            try:
                raw_b64 = enhanced_b64.split(",", 1)[-1]
                crop_bytes = base64.b64decode(raw_b64)
                crop_arr = np.frombuffer(crop_bytes, dtype=np.uint8)
                crop_img = cv2.imdecode(crop_arr, cv2.IMREAD_COLOR)
                if crop_img is not None and crop_img.size > 0:
                    ocr_res = recognize_exact_crop(crop_img, None)
                    vehicle["plate"] = ocr_res.get("plate")
                    vehicle["raw_text"] = ocr_res.get("raw_text")
                    vehicle["ocr_confidence"] = ocr_res.get("ocr_confidence")
                    vehicle["ocr_layout"] = ocr_res.get("layout")
                    if ocr_res.get("plate") and (
                        not best_candidate
                        or (ocr_res.get("ocr_confidence") or 0) > (best_candidate.get("ocr_confidence") or 0)
                    ):
                        best_candidate = vehicle
            except Exception as exc:
                vehicle["ocr_error"] = str(exc)

    if not any(v.get("plate_detected") for v in vehicles) and frame is not None and frame.size > 0:
        try:
            fallback_res = recognize_exact_crop(frame, None)
            if fallback_res.get("plate"):
                ok, encoded = cv2.imencode(".png", frame, [cv2.IMWRITE_PNG_COMPRESSION, 3])
                if ok:
                    encoded_crop = "data:image/png;base64," + base64.b64encode(encoded).decode("ascii")
                    fallback_vehicle = {
                        "vehicle_index": 0,
                        "vehicle_bbox": [0, 0, int(frame.shape[1]), int(frame.shape[0])],
                        "vehicle_confidence": 1.0,
                        "plate_detected": True,
                        "plate_bbox": [0, 0, int(frame.shape[1]), int(frame.shape[0])],
                        "plate_confidence": fallback_res.get("ocr_confidence") or 0.8,
                        "plate_detection_source": "direct_crop_fallback",
                        "plate_crop_original": encoded_crop,
                        "plate_crop_enhanced": encoded_crop,
                        "plate_crop_size": [int(frame.shape[1]), int(frame.shape[0])],
                        "plate_crop_quality_score": 0.8,
                        "plate": fallback_res.get("plate"),
                        "raw_text": fallback_res.get("raw_text"),
                        "ocr_confidence": fallback_res.get("ocr_confidence"),
                        "ocr_layout": fallback_res.get("layout"),
                    }
                    vehicles.append(fallback_vehicle)
                    best_candidate = fallback_vehicle
                    result["plate_count"] = result.get("plate_count", 0) + 1
        except Exception:
            pass

    if best_candidate and best_candidate.get("plate"):
        result["plate"] = best_candidate.get("plate")
        result["raw_text"] = best_candidate.get("raw_text")
        result["ocr_confidence"] = best_candidate.get("ocr_confidence")
    else:
        result["plate"] = None
        result["raw_text"] = None
        result["ocr_confidence"] = None

    return result

