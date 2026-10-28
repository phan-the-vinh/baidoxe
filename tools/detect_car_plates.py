"""Detect vehicles and license plates without running OCR.

The trained Car model is used first.  The Plate model runs in each accepted
vehicle ROI and, when necessary, once on the full image as a fallback.  The
last stdout line is JSON so the Flask environment can call this script through
the separate ``yolo_train`` interpreter.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import torch
from ultralytics import YOLO


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CAR_MODEL = ROOT / "runs/detect/runs/yolo_car_v12/train_27_from_smoke01_best/weights/best.pt"
DEFAULT_PLATE_MODEL = ROOT / "runs/detect/runs/license_plate/train_30/weights/best.pt"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Car -> license plate crop detector (no OCR).")
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--car-model", type=Path, default=DEFAULT_CAR_MODEL)
    parser.add_argument("--plate-model", type=Path, default=DEFAULT_PLATE_MODEL)
    parser.add_argument("--device", default="0")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--car-conf", type=float, default=0.25)
    parser.add_argument("--car-valid-conf", type=float, default=0.35)
    parser.add_argument("--plate-conf", type=float, default=0.25)
    parser.add_argument("--iou", type=float, default=0.50)
    parser.add_argument("--plate-padding", type=float, default=0.04)
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args()


def class_ids(model: YOLO, expected: str) -> set[int]:
    names = model.names
    items = names.items() if isinstance(names, dict) else enumerate(names)
    matches = {int(index) for index, name in items if str(name).strip().lower() == expected}
    if not matches:
        raise ValueError(f"Model does not contain class {expected!r}: {names}")
    return matches


def integer_box(values: Any, width: int, height: int) -> list[int] | None:
    x1, y1, x2, y2 = (float(value) for value in values)
    result = [
        max(0, min(width - 1, int(np.floor(x1)))),
        max(0, min(height - 1, int(np.floor(y1)))),
        max(0, min(width, int(np.ceil(x2)))),
        max(0, min(height, int(np.ceil(y2)))),
    ]
    return result if result[2] > result[0] and result[3] > result[1] else None


def box_area(box: list[int]) -> int:
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def intersection(first: list[int], second: list[int]) -> int:
    return max(0, min(first[2], second[2]) - max(first[0], second[0])) * max(
        0, min(first[3], second[3]) - max(first[1], second[1])
    )


def iou(first: list[int], second: list[int]) -> float:
    overlap = intersection(first, second)
    union = box_area(first) + box_area(second) - overlap
    return overlap / union if union else 0.0


def containment(first: list[int], second: list[int]) -> float:
    smaller = min(box_area(first), box_area(second))
    return intersection(first, second) / smaller if smaller else 0.0


def predict(model: YOLO, image: np.ndarray, classes: set[int], confidence: float, args: argparse.Namespace) -> list[dict]:
    height, width = image.shape[:2]
    result = model.predict(
        source=image, conf=confidence, iou=args.iou, imgsz=args.imgsz,
        device=args.device, verbose=False,
    )[0]
    detections: list[dict] = []
    if result.boxes is not None:
        for item in result.boxes:
            class_id = int(item.cls.item())
            if class_id not in classes:
                continue
            bbox = integer_box(item.xyxy[0].detach().cpu().tolist(), width, height)
            if bbox:
                detections.append({
                    "bbox": bbox,
                    "confidence": round(float(item.conf.item()), 6),
                })
    return sorted(detections, key=lambda item: item["confidence"], reverse=True)


def valid_cars(detections: list[dict], width: int, height: int, args: argparse.Namespace) -> tuple[list[dict], list[dict]]:
    accepted: list[dict] = []
    rejected: list[dict] = []
    image_area = width * height
    for item in detections:
        bbox = item["bbox"]
        box_width, box_height = bbox[2] - bbox[0], bbox[3] - bbox[1]
        area_ratio = box_area(bbox) / max(1, image_area)
        aspect = box_width / max(1, box_height)
        reasons = []
        if item["confidence"] < args.car_valid_conf:
            reasons.append("low_confidence")
        if not 0.006 <= area_ratio <= 0.85:
            reasons.append("area_ratio_out_of_range")
        if box_width / width < 0.025 or box_height / height < 0.025:
            reasons.append("side_too_small")
        if not 0.18 <= aspect <= 3.20:
            reasons.append("aspect_ratio_out_of_range")
        if not reasons and any(iou(bbox, old["bbox"]) >= 0.60 or containment(bbox, old["bbox"]) >= 0.82 for old in accepted):
            reasons.append("duplicate")
        if reasons:
            rejected.append({**item, "reasons": reasons})
        else:
            accepted.append(item)
    accepted.sort(key=lambda item: ((item["bbox"][0] + item["bbox"][2]) / 2, (item["bbox"][1] + item["bbox"][3]) / 2))
    return accepted, rejected


def crop_with_padding(image: np.ndarray, bbox: list[int], ratio: float) -> np.ndarray:
    x1, y1, x2, y2 = bbox
    pad_x, pad_y = round((x2 - x1) * ratio), round((y2 - y1) * ratio)
    return image[max(0, y1 - pad_y):min(image.shape[0], y2 + pad_y),
                 max(0, x1 - pad_x):min(image.shape[1], x2 + pad_x)].copy()


def sharpness(crop: np.ndarray) -> float:
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    return round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 4)


def _ordered_corners(points: np.ndarray) -> np.ndarray:
    points = points.reshape(4, 2).astype(np.float32)
    sums, differences = points.sum(axis=1), np.diff(points, axis=1).reshape(-1)
    return np.array([
        points[np.argmin(sums)], points[np.argmin(differences)],
        points[np.argmax(sums)], points[np.argmax(differences)],
    ], dtype=np.float32)


def rectify_if_confident(crop: np.ndarray) -> tuple[np.ndarray, bool]:
    """Rectify only a large, centred, convex four-corner plate contour."""
    height, width = crop.shape[:2]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 55, 150)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:12]:
        perimeter = cv2.arcLength(contour, True)
        polygon = cv2.approxPolyDP(contour, 0.025 * perimeter, True)
        if len(polygon) != 4 or not cv2.isContourConvex(polygon):
            continue
        area_ratio = cv2.contourArea(polygon) / max(1, width * height)
        x, y, box_width, box_height = cv2.boundingRect(polygon)
        center_x, center_y = x + box_width / 2, y + box_height / 2
        if not (0.35 <= area_ratio <= 0.94 and box_width >= width * 0.60 and box_height >= height * 0.35):
            continue
        if abs(center_x - width / 2) > width * 0.18 or abs(center_y - height / 2) > height * 0.18:
            continue
        corners = _ordered_corners(polygon)
        top_left, top_right, bottom_right, bottom_left = corners
        target_width = round(max(np.linalg.norm(top_right - top_left), np.linalg.norm(bottom_right - bottom_left)))
        target_height = round(max(np.linalg.norm(bottom_left - top_left), np.linalg.norm(bottom_right - top_right)))
        aspect = target_width / max(1, target_height)
        if target_width < 40 or target_height < 15 or not 1.0 <= aspect <= 6.5:
            continue
        destination = np.array(
            [[0, 0], [target_width - 1, 0], [target_width - 1, target_height - 1], [0, target_height - 1]],
            dtype=np.float32,
        )
        transform = cv2.getPerspectiveTransform(corners, destination)
        return cv2.warpPerspective(crop, transform, (target_width, target_height), flags=cv2.INTER_CUBIC), True
    return crop, False


def enhance_crop_for_display(crop: np.ndarray) -> tuple[np.ndarray, bool]:
    """Create a conservative 2x display image without changing the source crop."""
    rectified, was_rectified = rectify_if_confident(crop)
    enlarged = cv2.resize(rectified, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
    denoised = cv2.fastNlMeansDenoisingColored(enlarged, None, 3, 3, 7, 21)
    lab = cv2.cvtColor(denoised, cv2.COLOR_BGR2LAB)
    luminance, channel_a, channel_b = cv2.split(lab)
    luminance = cv2.createCLAHE(clipLimit=1.6, tileGridSize=(8, 8)).apply(luminance)
    contrasted = cv2.cvtColor(cv2.merge((luminance, channel_a, channel_b)), cv2.COLOR_LAB2BGR)
    blurred = cv2.GaussianBlur(contrasted, (0, 0), 1.0)
    return cv2.addWeighted(contrasted, 1.22, blurred, -0.22, 0), was_rectified


def encoded_crop(image: np.ndarray) -> str:
    ok, encoded = cv2.imencode(".png", image, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    if not ok:
        raise ValueError("Cannot encode plate crop")
    return "data:image/png;base64," + base64.b64encode(encoded).decode("ascii")


def empty_vehicle(index: int, detection: dict) -> dict:
    return {
        "vehicle_index": index,
        "vehicle_bbox": detection["bbox"],
        "vehicle_confidence": detection["confidence"],
        "plate_detected": False,
        "plate_bbox": None,
        "plate_confidence": None,
        "plate_detection_source": None,
        "plate_crop_original": None,
        "plate_crop_enhanced": None,
    }


def attach_plate(record: dict, image: np.ndarray, candidate: dict, source: str, args: argparse.Namespace) -> None:
    crop = crop_with_padding(image, candidate["bbox"], args.plate_padding)
    display_crop, rectified = enhance_crop_for_display(crop)
    original_sharpness, enhanced_sharpness = sharpness(crop), sharpness(display_crop)
    crop_area_score = min(1.0, np.sqrt(crop.shape[0] * crop.shape[1]) / 320.0)
    sharpness_score = min(1.0, np.log1p(original_sharpness) / np.log1p(1200.0))
    quality_score = 0.55 * candidate["confidence"] + 0.20 * crop_area_score + 0.25 * sharpness_score
    original_encoded, enhanced_encoded = encoded_crop(crop), encoded_crop(display_crop)
    record.update({
        "plate_detected": True,
        "plate_bbox": candidate["bbox"],
        "plate_confidence": candidate["confidence"],
        "plate_detection_source": source,
        "plate_crop_original": original_encoded,
        "plate_crop_enhanced": enhanced_encoded,
        "plate_crop_original_size": [int(crop.shape[1]), int(crop.shape[0])],
        "plate_crop_enhanced_size": [int(display_crop.shape[1]), int(display_crop.shape[0])],
        "plate_crop_size": [int(display_crop.shape[1]), int(display_crop.shape[0])],
        "plate_crop_original_sharpness": original_sharpness,
        "plate_crop_enhanced_sharpness": enhanced_sharpness,
        "plate_crop_rectified": rectified,
        "plate_crop_quality_score": round(float(quality_score), 6),
    })


def best_vehicle_for_plate(plate: list[int], records: list[dict]) -> dict | None:
    center_x, center_y = (plate[0] + plate[2]) / 2, (plate[1] + plate[3]) / 2
    matches = []
    for record in records:
        vehicle = record["vehicle_bbox"]
        if record["plate_detected"] or vehicle is None:
            continue
        inside = vehicle[0] <= center_x <= vehicle[2] and vehicle[1] <= center_y <= vehicle[3]
        plate_inside = intersection(plate, vehicle) / max(1, box_area(plate))
        if inside and plate_inside >= 0.75:
            matches.append((plate_inside, -box_area(vehicle), record))
    return max(matches, key=lambda match: (match[0], match[1]))[2] if matches else None


def falls_inside_assigned_vehicle(plate: list[int], records: list[dict]) -> bool:
    center_x, center_y = (plate[0] + plate[2]) / 2, (plate[1] + plate[3]) / 2
    return any(
        record["plate_detected"]
        and record["vehicle_bbox"] is not None
        and record["vehicle_bbox"][0] <= center_x <= record["vehicle_bbox"][2]
        and record["vehicle_bbox"][1] <= center_y <= record["vehicle_bbox"][3]
        and intersection(plate, record["vehicle_bbox"]) / max(1, box_area(plate)) >= 0.75
        for record in records
    )


def process(image: np.ndarray, car_model: YOLO, plate_model: YOLO, car_classes: set[int], plate_classes: set[int], args: argparse.Namespace) -> dict:
    height, width = image.shape[:2]
    initial = predict(car_model, image, car_classes, args.car_conf, args)
    cars, rejected = valid_cars(initial, width, height, args)
    records: list[dict] = []
    selected_plate_boxes: list[list[int]] = []

    for index, car in enumerate(cars, start=1):
        record = empty_vehicle(index, car)
        x1, y1, x2, y2 = car["bbox"]
        vehicle_roi = image[y1:y2, x1:x2]
        local = predict(plate_model, vehicle_roi, plate_classes, args.plate_conf, args)
        if local:
            candidate = local[0]
            px1, py1, px2, py2 = candidate["bbox"]
            candidate = {**candidate, "bbox": [x1 + px1, y1 + py1, x1 + px2, y1 + py2]}
            attach_plate(record, image, candidate, "vehicle_roi", args)
            selected_plate_boxes.append(candidate["bbox"])
        records.append(record)

    abnormal = len(initial) > 50 or (len(initial) >= 4 and len(rejected) / len(initial) >= 0.75)
    fallback_reasons = []
    if not cars:
        fallback_reasons.append("no_valid_car_detection")
    if abnormal:
        fallback_reasons.append("abnormal_car_detection")
    if any(not record["plate_detected"] for record in records):
        fallback_reasons.append("plate_missing_in_vehicle_roi")

    fallback_plate_count = 0
    if fallback_reasons:
        for candidate in predict(plate_model, image, plate_classes, args.plate_conf, args):
            if any(iou(candidate["bbox"], old) >= 0.60 or containment(candidate["bbox"], old) >= 0.82 for old in selected_plate_boxes):
                continue
            if falls_inside_assigned_vehicle(candidate["bbox"], records):
                continue
            target = best_vehicle_for_plate(candidate["bbox"], records)
            if target is None:
                target = {
                    "vehicle_index": None,
                    "vehicle_bbox": None,
                    "vehicle_confidence": None,
                    "plate_detected": False,
                    "plate_bbox": None,
                    "plate_confidence": None,
                    "plate_detection_source": None,
                    "plate_crop_original": None,
                    "plate_crop_enhanced": None,
                }
                records.append(target)
            attach_plate(target, image, candidate, "full_image_fallback", args)
            selected_plate_boxes.append(candidate["bbox"])
            fallback_plate_count += 1

    return {
        "ok": True,
        "message": f"Phát hiện {len(cars)} xe và {sum(bool(item['plate_detected']) for item in records)} biển số.",
        "image_width": width,
        "image_height": height,
        "car_count": len(cars),
        "plate_count": sum(bool(item["plate_detected"]) for item in records),
        "fallback_used": bool(fallback_reasons),
        "fallback_reasons": fallback_reasons,
        "fallback_plate_count": fallback_plate_count,
        "vehicles": records,
    }


def save_artifacts(result: dict, image: np.ndarray, directory: Path, stem: str) -> None:
    image_dir = directory / stem
    image_dir.mkdir(parents=True, exist_ok=True)
    serializable = json.loads(json.dumps(result))
    for index, record in enumerate(serializable["vehicles"], start=1):
        original_url = record.get("plate_crop_original")
        enhanced_url = record.get("plate_crop_enhanced")
        if original_url and enhanced_url:
            original_name = f"plate_{index:02d}_original.png"
            enhanced_name = f"plate_{index:02d}_enhanced.png"
            (image_dir / original_name).write_bytes(base64.b64decode(original_url.split(",", 1)[1]))
            (image_dir / enhanced_name).write_bytes(base64.b64decode(enhanced_url.split(",", 1)[1]))
            record["plate_crop_original"] = original_name
            record["plate_crop_enhanced"] = enhanced_name
    (image_dir / "result.json").write_text(json.dumps(serializable, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    for model_path in (args.car_model, args.plate_model):
        if not model_path.resolve().is_file():
            raise FileNotFoundError(model_path)
    if str(args.device).startswith("0") and not torch.cuda.is_available():
        raise RuntimeError("CUDA device=0 requested but torch.cuda.is_available() is False")
    car_model, plate_model = YOLO(str(args.car_model.resolve())), YOLO(str(args.plate_model.resolve()))
    car_classes, plate_classes = class_ids(car_model, "car"), class_ids(plate_model, "license_plate")
    outputs = []
    for image_path in args.images:
        image = cv2.imread(str(image_path.resolve()), cv2.IMREAD_COLOR)
        if image is None or not image.size:
            raise ValueError(f"Cannot read image: {image_path}")
        result = process(image, car_model, plate_model, car_classes, plate_classes, args)
        result["input_image"] = str(image_path.resolve())
        outputs.append(result)
        if args.output_dir:
            save_artifacts(result, image, args.output_dir.resolve(), image_path.stem)
    payload = outputs[0] if len(outputs) == 1 else {"ok": True, "results": outputs}
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
