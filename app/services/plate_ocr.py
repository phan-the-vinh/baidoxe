"""Conservative OpenCV + Tesseract plate suggestion pipeline.

This module never writes business data. Its result is only a suggestion that a
staff member must review and explicitly copy into the business form.
"""

from __future__ import annotations

import base64
import os
import re
import threading
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from .common import BusinessError


PLATE_PATTERN = re.compile(r"^(\d{2})([A-Z]{1,3})(\d{4,6})$")
OCR_WHITELIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
MAX_WORKING_WIDTH = 1600
MAX_CANDIDATES = 6
_ocr_gate = threading.BoundedSemaphore(1)


@lru_cache(maxsize=4)
def _verify_tesseract(command: str) -> str:
    try:
        import pytesseract

        pytesseract.pytesseract.tesseract_cmd = command
        return str(pytesseract.get_tesseract_version())
    except ImportError as exc:
        raise BusinessError(
            "Thiếu pytesseract trong môi trường ứng dụng. Vui lòng nhập biển số thủ công.",
            503,
        ) from exc
    except Exception as exc:
        raise BusinessError(
            "Tesseract OCR chưa được cấu hình hoặc không thể khởi chạy. "
            "Hãy cấu hình TESSERACT_CMD trong .env; vẫn có thể nhập biển số thủ công.",
            503,
        ) from exc


def _tesseract_command() -> str:
    configured = (os.getenv("TESSERACT_CMD") or "").strip()
    if configured:
        path = Path(configured)
        if not path.is_file():
            raise BusinessError(
                "TESSERACT_CMD không trỏ tới tesseract.exe hợp lệ. "
                "Vui lòng kiểm tra cấu hình hoặc nhập biển số thủ công.",
                503,
            )
        command = str(path)
    else:
        # Use PATH only; never embed a workstation-specific installation path.
        command = "tesseract"
    _verify_tesseract(command)
    return command


def normalize_ocr_plate(raw_text: str) -> str | None:
    """Format only a complete OCR sequence; never infer or replace characters."""
    tokens = re.findall(r"[A-Z0-9]+", str(raw_text or "").upper())
    if not tokens:
        return None
    sequences: list[str] = []
    for size in (1, 2, 3):
        sequences.extend("".join(tokens[index:index + size]) for index in range(len(tokens) - size + 1))
    sequences.append("".join(tokens))
    for value in dict.fromkeys(sequences):
        match = PLATE_PATTERN.fullmatch(value)
        if match:
            return f"{match.group(1)}{match.group(2)}-{match.group(3)}"
    return None


def _resize_for_detection(frame: np.ndarray) -> tuple[np.ndarray, float]:
    height, width = frame.shape[:2]
    if width <= MAX_WORKING_WIDTH:
        return frame.copy(), 1.0
    scale = MAX_WORKING_WIDTH / width
    return cv2.resize(frame, (MAX_WORKING_WIDTH, max(1, round(height * scale))),
                      interpolation=cv2.INTER_AREA), scale


def _candidate_score(contour: np.ndarray, bbox: tuple[int, int, int, int],
                     edge_map: np.ndarray) -> float:
    x, y, width, height = bbox
    rectangle_area = width * height
    rectangularity = min(1.0, cv2.contourArea(contour) / max(1, rectangle_area))
    aspect = width / max(1, height)
    aspect_score = max(0.0, 1.0 - abs(aspect - 3.4) / 3.4)
    edge_density = min(1.0, cv2.countNonZero(edge_map[y:y + height, x:x + width]) /
                       max(1, rectangle_area) * 5.0)
    vertical_position = min(1.0, (y + height / 2) / max(1, edge_map.shape[0]))
    return 0.38 * rectangularity + 0.30 * aspect_score + 0.22 * edge_density + 0.10 * vertical_position


def _iou(first: tuple[int, int, int, int], second: tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = first
    bx, by, bw, bh = second
    left, top = max(ax, bx), max(ay, by)
    right, bottom = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    intersection = max(0, right - left) * max(0, bottom - top)
    union = aw * ah + bw * bh - intersection
    return intersection / union if union else 0.0


def detect_plate_candidates(frame: np.ndarray) -> tuple[np.ndarray, float, list[dict]]:
    """Return resized source, its scale, and non-overlapping geometric candidates."""
    working, scale = _resize_for_detection(frame)
    gray = cv2.cvtColor(working, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8)).apply(gray)
    filtered = cv2.bilateralFilter(clahe, 9, 60, 60)
    edges = cv2.Canny(filtered, 45, 170)
    edges = cv2.morphologyEx(
        edges, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (7, 3)), iterations=1,
    )
    blackhat = cv2.morphologyEx(
        filtered, cv2.MORPH_BLACKHAT,
        cv2.getStructuringElement(cv2.MORPH_RECT, (17, 5)),
    )
    gradient = cv2.Sobel(blackhat, cv2.CV_32F, 1, 0, ksize=3)
    gradient = cv2.convertScaleAbs(gradient)
    gradient = cv2.morphologyEx(
        gradient, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (13, 3)), iterations=2,
    )
    gradient = cv2.threshold(gradient, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    masks = (edges, gradient)
    frame_area = gray.size
    minimum_area = max(500, frame_area * 0.00035)
    maximum_area = frame_area * 0.32
    ranked: list[dict] = []
    for mask in masks:
        contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            x, y, width, height = cv2.boundingRect(contour)
            area = width * height
            aspect = width / max(1, height)
            if not (minimum_area <= area <= maximum_area):
                continue
            if width < 55 or height < 16 or not 1.15 <= aspect <= 6.8:
                continue
            rectangularity = cv2.contourArea(contour) / max(1, area)
            if rectangularity < 0.16:
                continue
            ranked.append({
                "bbox": (x, y, width, height),
                "geometry_score": _candidate_score(contour, (x, y, width, height), edges),
            })
    ranked.sort(key=lambda item: item["geometry_score"], reverse=True)
    selected: list[dict] = []
    for item in ranked:
        if any(_iou(item["bbox"], existing["bbox"]) > 0.55 for existing in selected):
            continue
        selected.append(item)
        if len(selected) >= MAX_CANDIDATES:
            break
    return working, scale, selected


def _crop_with_padding(frame: np.ndarray, bbox: tuple[int, int, int, int]) -> np.ndarray:
    x, y, width, height = bbox
    pad_x, pad_y = max(3, round(width * 0.05)), max(3, round(height * 0.10))
    left, top = max(0, x - pad_x), max(0, y - pad_y)
    right, bottom = min(frame.shape[1], x + width + pad_x), min(frame.shape[0], y + height + pad_y)
    return frame[top:bottom, left:right].copy()


def _ocr_variants(crop: np.ndarray) -> list[np.ndarray]:
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    factor = min(4.0, max(1.0, 110 / max(1, gray.shape[0])))
    gray = cv2.resize(gray, None, fx=factor, fy=factor, interpolation=cv2.INTER_CUBIC)
    enhanced = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(6, 6)).apply(gray)
    enhanced = cv2.bilateralFilter(enhanced, 7, 45, 45)
    otsu = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    adaptive = cv2.adaptiveThreshold(
        enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY, 31, 9,
    )
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    return [
        cv2.morphologyEx(otsu, cv2.MORPH_OPEN, kernel),
        cv2.morphologyEx(adaptive, cv2.MORPH_CLOSE, kernel),
    ]


def _read_variant(image: np.ndarray, page_segmentation_mode: int) -> tuple[str, float]:
    import pytesseract

    config = (
        f"--oem 3 --psm {page_segmentation_mode} "
        f"-c tessedit_char_whitelist={OCR_WHITELIST}"
    )
    result = pytesseract.image_to_data(
        image, config=config, output_type=pytesseract.Output.DICT, timeout=5,
    )
    words: list[str] = []
    confidences: list[float] = []
    for text, confidence in zip(result.get("text", []), result.get("conf", [])):
        clean = str(text or "").strip()
        try:
            score = float(confidence)
        except (TypeError, ValueError):
            continue
        if clean and score >= 0:
            words.append(clean)
            confidences.append(score)
    raw_text = " ".join(words)
    confidence = float(np.mean(confidences)) / 100 if confidences else 0.0
    return raw_text, max(0.0, min(1.0, confidence))


def _encoded_crop(crop: np.ndarray) -> str | None:
    ok, encoded = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 86])
    if not ok:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(encoded).decode("ascii")


def recognize_plate_from_image(image: np.ndarray) -> dict:
    """Detect and OCR a plate from a BGR frame without mutating the input image."""
    if not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] != 3 or image.size == 0:
        raise BusinessError("Ảnh đầu vào không hợp lệ.")
    if not _ocr_gate.acquire(blocking=False):
        raise BusinessError("OCR đang xử lý ảnh khác. Hãy thử lại sau.", 429)
    try:
        command = _tesseract_command()
        import pytesseract
        pytesseract.pytesseract.tesseract_cmd = command

        working, scale, candidates = detect_plate_candidates(image)
        recognized: list[dict] = []
        for candidate in candidates:
            crop = _crop_with_padding(working, candidate["bbox"])
            aspect = crop.shape[1] / max(1, crop.shape[0])
            psm = 7 if aspect >= 2.2 else 6
            for variant in _ocr_variants(crop):
                raw_text, confidence = _read_variant(variant, psm)
                normalized = normalize_ocr_plate(raw_text)
                if normalized is None:
                    continue
                recognized.append({
                    "plate": normalized,
                    "raw_text": raw_text,
                    "confidence": confidence,
                    "rank": 0.78 * confidence + 0.22 * candidate["geometry_score"],
                    "crop": crop,
                    "bbox": candidate["bbox"],
                })
        if not recognized:
            return {
                "ok": False,
                "plate": None,
                "normalized_plate": None,
                "raw_text": None,
                "confidence": None,
                "needs_confirmation": True,
                "crop_image": None,
                "bbox": None,
                "candidate_count": len(candidates),
                "message": "Không nhận diện rõ biển số. Vui lòng nhập thủ công.",
            }
        best = max(recognized, key=lambda item: item["rank"])
        x, y, width, height = best["bbox"]
        original_bbox = [
            round(x / scale), round(y / scale),
            round(width / scale), round(height / scale),
        ]
        return {
            "ok": True,
            "plate": best["plate"],
            "normalized_plate": best["plate"],
            "raw_text": best["raw_text"],
            "confidence": round(best["confidence"], 4),
            "needs_confirmation": True,
            "crop_image": _encoded_crop(best["crop"]),
            "bbox": original_bbox,
            "candidate_count": len(candidates),
            "message": (
                f"AI đề xuất {best['plate']}. Hãy đối chiếu ảnh, sửa nếu cần "
                "và xác nhận biển số. Điểm OCR không phải độ chính xác đã hiệu chuẩn."
            ),
        }
    except BusinessError:
        raise
    except Exception as exc:
        raise BusinessError(
            "OCR không xử lý được ảnh này. Vui lòng thử ảnh rõ hơn hoặc nhập biển số thủ công.",
            503,
        ) from exc
    finally:
        _ocr_gate.release()
