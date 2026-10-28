"""Deterministic, no-database smoke checks for the OpenCV + Tesseract pipeline."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.plate_ocr import recognize_plate_from_image


def plate_scene(scale: float = 1.0, brightness: float = 1.0, blur: int = 0,
                label: str = "29A67890") -> np.ndarray:
    image = np.full((720, 1280, 3), 105, dtype=np.uint8)
    width, height = round(470 * scale), round(125 * scale)
    left, top = (1280 - width) // 2, round(470 - height / 2)
    cv2.rectangle(image, (left, top), (left + width, top + height), (242, 242, 242), -1)
    cv2.rectangle(image, (left, top), (left + width, top + height), (10, 10, 10), max(2, round(5 * scale)))
    font_scale = max(0.55, 2.2 * scale)
    thickness = max(1, round(5 * scale))
    size = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness)[0]
    origin = (left + max(4, (width - size[0]) // 2), top + (height + size[1]) // 2)
    cv2.putText(image, label, origin, cv2.FONT_HERSHEY_SIMPLEX, font_scale,
                (15, 15, 15), thickness, cv2.LINE_AA)
    if brightness != 1.0:
        image = np.clip(image.astype(np.float32) * brightness, 0, 255).astype(np.uint8)
    if blur:
        image = cv2.GaussianBlur(image, (blur, blur), 0)
    return image


def no_plate_scene() -> np.ndarray:
    image = np.full((720, 1280, 3), 155, dtype=np.uint8)
    cv2.circle(image, (300, 330), 130, (80, 110, 140), -1)
    cv2.line(image, (0, 600), (1279, 500), (230, 230, 230), 14)
    return image


def text_scene() -> np.ndarray:
    image = np.full((720, 1280, 3), 235, dtype=np.uint8)
    cv2.putText(image, "PARKING ENTRANCE", (100, 230), cv2.FONT_HERSHEY_SIMPLEX,
                2.1, (30, 30, 30), 5, cv2.LINE_AA)
    cv2.putText(image, "LEVEL TWO", (300, 440), cv2.FONT_HERSHEY_SIMPLEX,
                1.8, (30, 30, 30), 4, cv2.LINE_AA)
    return image


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    fixtures = {
        "clear_frontal_controlled": plate_scene(),
        "far_controlled": plate_scene(scale=0.48),
        "low_light_controlled": plate_scene(brightness=0.24),
        "no_plate_controlled": no_plate_scene(),
        "other_text_controlled": text_scene(),
        "blurred_controlled": plate_scene(blur=19),
    }
    results = []
    for name, image in fixtures.items():
        original = image.copy()
        result = recognize_plate_from_image(image)
        results.append({
            "fixture": name,
            "ok": result["ok"],
            "plate": result["plate"],
            "raw_text": result["raw_text"],
            "confidence": result["confidence"],
            "candidate_count": result["candidate_count"],
            "has_crop": bool(result["crop_image"]),
            "source_unchanged": bool(np.array_equal(image, original)),
        })
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
