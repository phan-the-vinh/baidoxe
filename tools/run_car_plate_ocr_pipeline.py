"""Independent Car YOLO -> Plate YOLO -> Tesseract pipeline for still images.

Car detections are preferred but are not a hard gate. If they are implausible
or yield no plates, the trained Plate model runs once on the full source image.
The script only reads images/models and writes isolated debug artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import torch
from ultralytics import YOLO


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CAR_MODEL = ROOT / "runs/detect/runs/yolo_car_v12/train_27_from_smoke01_best/weights/best.pt"
DEFAULT_PLATE_MODEL = ROOT / "runs/detect/runs/license_plate/train_30/weights/best.pt"
DEFAULT_OUTPUT_ROOT = ROOT / "instance/plate_pipeline_debug"
OCR_WORKER = ROOT / "tools/ocr_plate_crop_worker.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run filtered Car YOLO -> Plate YOLO/fallback -> Tesseract OCR."
    )
    parser.add_argument("images", nargs="+", type=Path, help="Input still images.")
    parser.add_argument("--car-model", type=Path, default=DEFAULT_CAR_MODEL)
    parser.add_argument("--plate-model", type=Path, default=DEFAULT_PLATE_MODEL)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--ocr-python", type=Path, default=None)
    parser.add_argument("--device", default="0")
    parser.add_argument("--car-conf", type=float, default=0.25)
    parser.add_argument("--car-valid-conf", type=float, default=0.35)
    parser.add_argument("--plate-conf", type=float, default=0.25)
    parser.add_argument("--iou", type=float, default=0.50)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--min-car-area-ratio", type=float, default=0.006)
    parser.add_argument("--max-car-area-ratio", type=float, default=0.85)
    parser.add_argument("--min-car-side-ratio", type=float, default=0.025)
    parser.add_argument("--min-car-aspect", type=float, default=0.18)
    parser.add_argument("--max-car-aspect", type=float, default=3.20)
    parser.add_argument("--max-car-detections", type=int, default=50)
    parser.add_argument("--duplicate-iou", type=float, default=0.60)
    parser.add_argument("--duplicate-containment", type=float, default=0.82)
    parser.add_argument(
        "--plate-padding",
        type=float,
        default=0.04,
        help="Padding around detected plate for OCR; reported bbox is unchanged.",
    )
    return parser.parse_args()


def resolve_ocr_python(explicit: Path | None) -> Path:
    if explicit is not None:
        candidate = explicit.expanduser().resolve()
    elif os.getenv("PARKING_AI_PYTHON"):
        candidate = Path(os.environ["PARKING_AI_PYTHON"]).expanduser().resolve()
    else:
        executable = "python.exe" if os.name == "nt" else "bin/python"
        candidate = Path(sys.prefix).parent / "parking_ai" / executable
    if not candidate.is_file():
        raise FileNotFoundError(
            "Cannot find the parking_ai Python interpreter for Tesseract. "
            "Pass --ocr-python with its absolute path."
        )
    return candidate


def validate_probability(name: str, value: float) -> None:
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1, got {value}")


def class_ids(model: YOLO, expected_name: str) -> set[int]:
    names = model.names
    items = names.items() if isinstance(names, dict) else enumerate(names)
    matches = {int(index) for index, name in items if str(name).strip().lower() == expected_name}
    if not matches:
        raise ValueError(f"Model does not contain expected class {expected_name!r}: {names}")
    return matches


def integer_box(values: Any, width: int, height: int) -> list[int] | None:
    x1, y1, x2, y2 = (float(value) for value in values)
    left = max(0, min(width - 1, int(np.floor(x1))))
    top = max(0, min(height - 1, int(np.floor(y1))))
    right = max(0, min(width, int(np.ceil(x2))))
    bottom = max(0, min(height, int(np.ceil(y2))))
    if right <= left or bottom <= top:
        return None
    return [left, top, right, bottom]


def box_intersection(first: list[int], second: list[int]) -> int:
    left, top = max(first[0], second[0]), max(first[1], second[1])
    right, bottom = min(first[2], second[2]), min(first[3], second[3])
    return max(0, right - left) * max(0, bottom - top)


def box_area(box: list[int]) -> int:
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def box_iou(first: list[int], second: list[int]) -> float:
    intersection = box_intersection(first, second)
    union = box_area(first) + box_area(second) - intersection
    return intersection / union if union else 0.0


def containment(first: list[int], second: list[int]) -> float:
    intersection = box_intersection(first, second)
    smaller = min(box_area(first), box_area(second))
    return intersection / smaller if smaller else 0.0


def predict_boxes(
    model: YOLO,
    image: np.ndarray,
    allowed_classes: set[int],
    confidence: float,
    args: argparse.Namespace,
) -> list[dict]:
    height, width = image.shape[:2]
    result = model.predict(
        source=image,
        conf=confidence,
        iou=args.iou,
        imgsz=args.imgsz,
        device=args.device,
        verbose=False,
    )[0]
    detections: list[dict] = []
    if result.boxes is not None:
        for box in result.boxes:
            class_id = int(box.cls.item())
            if class_id not in allowed_classes:
                continue
            bbox = integer_box(box.xyxy[0].detach().cpu().tolist(), width, height)
            if bbox is None:
                continue
            detections.append(
                {
                    "bbox": bbox,
                    "confidence": round(float(box.conf.item()), 6),
                    "class_id": class_id,
                }
            )
    detections.sort(key=lambda item: item["confidence"], reverse=True)
    return detections


def filter_car_detections(
    detections: list[dict], width: int, height: int, args: argparse.Namespace
) -> tuple[list[dict], list[dict]]:
    image_area = width * height
    accepted: list[dict] = []
    rejected: list[dict] = []
    for detection in detections:
        bbox = detection["bbox"]
        box_width, box_height = bbox[2] - bbox[0], bbox[3] - bbox[1]
        area_ratio = box_area(bbox) / max(1, image_area)
        aspect_ratio = box_width / max(1, box_height)
        width_ratio, height_ratio = box_width / width, box_height / height
        reasons: list[str] = []
        if detection["confidence"] < args.car_valid_conf:
            reasons.append("low_confidence")
        if not args.min_car_area_ratio <= area_ratio <= args.max_car_area_ratio:
            reasons.append("area_ratio_out_of_range")
        if width_ratio < args.min_car_side_ratio or height_ratio < args.min_car_side_ratio:
            reasons.append("side_too_small")
        if not args.min_car_aspect <= aspect_ratio <= args.max_car_aspect:
            reasons.append("aspect_ratio_out_of_range")
        if not reasons:
            for existing in accepted:
                if box_iou(bbox, existing["bbox"]) >= args.duplicate_iou:
                    reasons.append("duplicate_iou")
                    break
                if containment(bbox, existing["bbox"]) >= args.duplicate_containment:
                    reasons.append("duplicate_containment")
                    break
        enriched = {
            **detection,
            "area_ratio": round(area_ratio, 6),
            "aspect_ratio": round(aspect_ratio, 4),
            "width_ratio": round(width_ratio, 6),
            "height_ratio": round(height_ratio, 6),
        }
        if reasons:
            rejected.append({**enriched, "rejection_reasons": reasons})
        else:
            accepted.append(enriched)
    accepted.sort(key=lambda item: ((item["bbox"][0] + item["bbox"][2]) / 2,
                                    (item["bbox"][1] + item["bbox"][3]) / 2))
    return accepted, rejected


def padded_crop(image: np.ndarray, bbox: list[int], ratio: float) -> np.ndarray:
    left, top, right, bottom = bbox
    width, height = right - left, bottom - top
    pad_x, pad_y = round(width * ratio), round(height * ratio)
    left, top = max(0, left - pad_x), max(0, top - pad_y)
    right, bottom = min(image.shape[1], right + pad_x), min(image.shape[0], bottom + pad_y)
    return image[top:bottom, left:right].copy()


def run_ocr(ocr_python: Path, crop_path: Path, debug_dir: Path) -> dict:
    process = subprocess.run(
        [
            str(ocr_python),
            str(OCR_WORKER),
            "--image",
            str(crop_path),
            "--output-dir",
            str(debug_dir),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    lines = [line.strip() for line in process.stdout.splitlines() if line.strip()]
    if not lines:
        return {
            "ok": False,
            "raw_text": None,
            "plate": None,
            "ocr_confidence": None,
            "needs_confirmation": True,
            "error": process.stderr.strip() or f"OCR worker exited {process.returncode}",
        }
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        return {
            "ok": False,
            "raw_text": None,
            "plate": None,
            "ocr_confidence": None,
            "needs_confirmation": True,
            "error": f"OCR worker returned invalid JSON (exit {process.returncode})",
        }


def annotate_box(
    image: np.ndarray,
    bbox: list[int],
    label: str,
    color: tuple[int, int, int],
    thickness: int,
) -> None:
    left, top, right, bottom = bbox
    cv2.rectangle(image, (left, top), (right, bottom), color, thickness)
    cv2.putText(
        image,
        label,
        (left, max(16, top - 5)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        color,
        1,
        cv2.LINE_AA,
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def blank_object(
    object_index: int,
    vehicle_index: int | None,
    vehicle_bbox: list[int] | None,
    vehicle_confidence: float | None,
) -> dict:
    return {
        "object_index": object_index,
        "vehicle_index": vehicle_index,
        "vehicle_bbox": vehicle_bbox,
        "vehicle_confidence": vehicle_confidence,
        "plate_detected": False,
        "plate_bbox": None,
        "plate_bbox_in_vehicle": None,
        "plate_confidence": None,
        "plate_crop_size": None,
        "plate_detection_source": None,
        "raw_text": None,
        "plate": None,
        "ocr_confidence": None,
        "ocr_layout": None,
        "needs_confirmation": True,
        "plate_candidate_count": 0,
        "failure_stage": "plate_detection",
    }


def apply_plate_ocr(
    record: dict,
    image: np.ndarray,
    plate_bbox: list[int],
    plate_confidence: float,
    detection_source: str,
    crop_stem: str,
    output_dir: Path,
    ocr_python: Path,
    args: argparse.Namespace,
    plate_bbox_in_vehicle: list[int] | None = None,
) -> dict:
    plate_crop = padded_crop(image, plate_bbox, args.plate_padding)
    crop_path = output_dir / f"{crop_stem}.jpg"
    if not cv2.imwrite(str(crop_path), plate_crop):
        raise OSError(f"Cannot write plate crop: {crop_path}")
    ocr = run_ocr(ocr_python, crop_path, output_dir / f"{crop_stem}_ocr")
    record.update(
        {
            "plate_detected": True,
            "plate_bbox": plate_bbox,
            "plate_bbox_in_vehicle": plate_bbox_in_vehicle,
            "plate_confidence": plate_confidence,
            "plate_crop_size": [int(plate_crop.shape[1]), int(plate_crop.shape[0])],
            "plate_detection_source": detection_source,
            "raw_text": ocr.get("raw_text"),
            "plate": ocr.get("plate"),
            "ocr_confidence": ocr.get("ocr_confidence"),
            "ocr_layout": ocr.get("layout"),
            "ocr_layout_evidence": ocr.get("layout_evidence"),
            "rectification": ocr.get("rectification"),
            "selected_ocr_candidate": ocr.get("selected_candidate"),
            "ocr_candidates": ocr.get("ocr_candidates", []),
            "ocr_candidate_count": ocr.get("ocr_candidate_count", 0),
            "failure_stage": None if ocr.get("plate") else "ocr_normalization",
        }
    )
    if not ocr.get("ok", False):
        record["ocr_error"] = ocr.get("error", "OCR worker failed")
    return record


def process_image(
    image_path: Path,
    output_dir: Path,
    car_model: YOLO,
    plate_model: YOLO,
    car_classes: set[int],
    plate_classes: set[int],
    args: argparse.Namespace,
    ocr_python: Path,
) -> dict:
    source = image_path.expanduser().resolve()
    image = cv2.imread(str(source), cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise ValueError(f"Cannot read image: {source}")

    output_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(source, output_dir / f"original{source.suffix.lower()}")
    annotated = image.copy()
    height, width = image.shape[:2]

    initial_cars = predict_boxes(car_model, image, car_classes, args.car_conf, args)
    valid_cars, rejected_cars = filter_car_detections(initial_cars, width, height, args)
    for rejected in rejected_cars:
        annotate_box(
            annotated,
            rejected["bbox"],
            f"Car rejected {rejected['confidence']:.2f}",
            (128, 128, 128),
            1,
        )

    objects: list[dict] = []
    roi_plate_boxes: list[list[int]] = []
    object_index = 0
    for vehicle_index, detection in enumerate(valid_cars, start=1):
        object_index += 1
        vx1, vy1, vx2, vy2 = detection["bbox"]
        vehicle_crop = image[vy1:vy2, vx1:vx2].copy()
        vehicle_path = output_dir / f"vehicle_{vehicle_index:02d}.jpg"
        if not cv2.imwrite(str(vehicle_path), vehicle_crop):
            raise OSError(f"Cannot write vehicle crop: {vehicle_path}")
        annotate_box(
            annotated,
            detection["bbox"],
            f"Car accepted {detection['confidence']:.2f}",
            (255, 255, 0),
            2,
        )

        record = blank_object(
            object_index,
            vehicle_index,
            detection["bbox"],
            detection["confidence"],
        )
        local_plates = predict_boxes(
            plate_model, vehicle_crop, plate_classes, args.plate_conf, args
        )
        record["plate_candidate_count"] = len(local_plates)
        if local_plates:
            selected = local_plates[0]
            px1, py1, px2, py2 = selected["bbox"]
            global_bbox = [vx1 + px1, vy1 + py1, vx1 + px2, vy1 + py2]
            record = apply_plate_ocr(
                record,
                image,
                global_bbox,
                selected["confidence"],
                "vehicle_roi",
                f"vehicle_{vehicle_index:02d}_plate",
                output_dir,
                ocr_python,
                args,
                selected["bbox"],
            )
            roi_plate_boxes.append(global_bbox)
            annotate_box(
                annotated,
                global_bbox,
                f"Plate ROI {selected['confidence']:.2f}",
                (0, 255, 0),
                2,
            )
        objects.append(record)

    fallback_reasons: list[str] = []
    if not valid_cars:
        fallback_reasons.append("no_valid_car_detection")
    if len(initial_cars) > args.max_car_detections:
        fallback_reasons.append("too_many_initial_car_detections")
    if len(initial_cars) >= 4 and len(rejected_cars) / len(initial_cars) >= 0.75:
        fallback_reasons.append("mostly_invalid_car_detections")
    if not roi_plate_boxes:
        fallback_reasons.append("no_plate_in_vehicle_rois")

    fallback_plate_count = 0
    if fallback_reasons:
        full_image_plates = predict_boxes(
            plate_model, image, plate_classes, args.plate_conf, args
        )
        for candidate in full_image_plates:
            if any(box_iou(candidate["bbox"], existing) >= 0.60 for existing in roi_plate_boxes):
                continue
            object_index += 1
            fallback_plate_count += 1
            record = blank_object(object_index, None, None, None)
            record["plate_candidate_count"] = len(full_image_plates)
            record = apply_plate_ocr(
                record,
                image,
                candidate["bbox"],
                candidate["confidence"],
                "full_image_fallback",
                f"fallback_plate_{fallback_plate_count:02d}",
                output_dir,
                ocr_python,
                args,
            )
            annotate_box(
                annotated,
                candidate["bbox"],
                f"Plate fallback {candidate['confidence']:.2f}",
                (0, 255, 0),
                2,
            )
            objects.append(record)

    annotated_path = output_dir / "vehicles.jpg"
    if not cv2.imwrite(str(annotated_path), annotated):
        raise OSError(f"Cannot write annotated image: {annotated_path}")

    result = {
        "input_image": str(source),
        "input_sha256": sha256(source),
        "image_size": [width, height],
        "car_detections_initial_count": len(initial_cars),
        "car_detections_filtered_count": len(valid_cars),
        "car_detections_rejected_count": len(rejected_cars),
        "car_detections_initial": initial_cars,
        "car_detections_filtered": valid_cars,
        "car_detections_rejected": rejected_cars,
        "fallback_used": bool(fallback_reasons),
        "fallback_reasons": fallback_reasons,
        "fallback_plate_count": fallback_plate_count,
        "plate_detection_count": sum(item["plate_detected"] for item in objects),
        "normalized_plate_count": sum(item["plate"] is not None for item in objects),
        "objects": objects,
        "vehicles": objects,
    }
    (output_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def unique_image_dir(run_dir: Path, image: Path, index: int) -> Path:
    safe_stem = "".join(char if char.isalnum() or char in "-_" else "_" for char in image.stem)
    return run_dir / f"{index:02d}_{safe_stem[:80]}"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    for name in (
        "car_conf",
        "car_valid_conf",
        "plate_conf",
        "iou",
        "min_car_area_ratio",
        "max_car_area_ratio",
        "min_car_side_ratio",
        "duplicate_iou",
        "duplicate_containment",
        "plate_padding",
    ):
        validate_probability(f"--{name.replace('_', '-')}", getattr(args, name))
    if args.min_car_aspect <= 0 or args.max_car_aspect <= args.min_car_aspect:
        raise ValueError("Invalid Car aspect-ratio range")
    if args.max_car_detections < 1:
        raise ValueError("--max-car-detections must be positive")

    car_model_path = args.car_model.expanduser().resolve()
    plate_model_path = args.plate_model.expanduser().resolve()
    for model_path in (car_model_path, plate_model_path):
        if not model_path.is_file():
            raise FileNotFoundError(model_path)
    if str(args.device).startswith("0") and not torch.cuda.is_available():
        raise RuntimeError("device=0 requested but torch.cuda.is_available() is False")
    ocr_python = resolve_ocr_python(args.ocr_python)

    car_model = YOLO(str(car_model_path))
    plate_model = YOLO(str(plate_model_path))
    car_classes = class_ids(car_model, "car")
    plate_classes = class_ids(plate_model, "license_plate")

    stamp = datetime.now().strftime("run_%Y%m%d_%H%M%S_%f")
    run_dir = args.output_root.expanduser().resolve() / stamp
    run_dir.mkdir(parents=True, exist_ok=False)
    results: list[dict] = []
    for index, image_path in enumerate(args.images, start=1):
        results.append(
            process_image(
                image_path=image_path,
                output_dir=unique_image_dir(run_dir, image_path, index),
                car_model=car_model,
                plate_model=plate_model,
                car_classes=car_classes,
                plate_classes=plate_classes,
                args=args,
                ocr_python=ocr_python,
            )
        )

    summary = {
        "run_directory": str(run_dir),
        "car_model": str(car_model_path),
        "plate_model": str(plate_model_path),
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "device": str(args.device),
        "thresholds": {
            "car_conf": args.car_conf,
            "car_valid_conf": args.car_valid_conf,
            "plate_conf": args.plate_conf,
            "iou": args.iou,
            "imgsz": args.imgsz,
            "min_car_area_ratio": args.min_car_area_ratio,
            "max_car_area_ratio": args.max_car_area_ratio,
            "min_car_side_ratio": args.min_car_side_ratio,
            "car_aspect_range": [args.min_car_aspect, args.max_car_aspect],
            "max_car_detections": args.max_car_detections,
            "duplicate_iou": args.duplicate_iou,
            "duplicate_containment": args.duplicate_containment,
            "plate_padding": args.plate_padding,
        },
        "ocr_python": str(ocr_python),
        "results": results,
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    console_summary = {
        "run_directory": str(run_dir),
        "gpu": summary["gpu"],
        "images": [
            {
                "input_image": item["input_image"],
                "car_initial": item["car_detections_initial_count"],
                "car_filtered": item["car_detections_filtered_count"],
                "fallback_used": item["fallback_used"],
                "fallback_reasons": item["fallback_reasons"],
                "plate_detection_count": item["plate_detection_count"],
                "normalized_plate_count": item["normalized_plate_count"],
            }
            for item in results
        ],
    }
    print(json.dumps(console_summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
