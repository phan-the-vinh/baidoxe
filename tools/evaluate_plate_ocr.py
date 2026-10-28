"""Evaluate Plate YOLO + the current Tesseract pipeline against real labels.

Ground truth is loaded only after inference for metric calculation. The script
does not modify Flask, SQL, models, datasets, or source benchmark images.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import torch
from ultralytics import YOLO


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "dataset/plate_ocr_benchmark"
DEFAULT_OUTPUT = ROOT / "instance/plate_ocr_benchmark"
DEFAULT_PLATE_MODEL = ROOT / "runs/detect/runs/license_plate/train_30/weights/best.pt"
OCR_WORKER = ROOT / "tools/ocr_plate_crop_worker.py"
RESULT_FIELDS = [
    "mode",
    "filename",
    "ground_truth",
    "ground_truth_layout",
    "plate_detected",
    "plate_confidence",
    "plate_bbox",
    "plate_candidate_count",
    "ocr_raw",
    "ocr_normalized",
    "ocr_confidence",
    "ocr_layout",
    "exact_match",
    "character_accuracy",
    "levenshtein_distance",
    "error",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark the trained Plate YOLO and current Tesseract OCR."
    )
    parser.add_argument("--mode", choices=("full", "ocr-only"), default="full")
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Exact output directory; defaults to instance/plate_ocr_benchmark/<mode>.",
    )
    parser.add_argument("--plate-model", type=Path, default=DEFAULT_PLATE_MODEL)
    parser.add_argument("--ocr-python", type=Path, default=None)
    parser.add_argument("--device", default="0")
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--iou", type=float, default=0.50)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--plate-padding", type=float, default=0.04)
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
            "Cannot find parking_ai Python for Tesseract; pass --ocr-python explicitly."
        )
    return candidate


def validate_probability(name: str, value: float) -> None:
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1, got {value}")


def canonical_plate(value: str | None) -> str:
    """Remove separators only; never replace, infer, or repair characters."""
    return "".join(re.findall(r"[A-Z0-9]", str(value or "").upper()))


def load_ground_truth(csv_path: Path) -> list[dict]:
    if not csv_path.is_file():
        raise FileNotFoundError(csv_path)
    with csv_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not {"filename", "plate"}.issubset(reader.fieldnames):
            raise ValueError("ground_truth.csv must contain filename and plate columns")
        rows: list[dict] = []
        seen: set[str] = set()
        for line_number, raw in enumerate(reader, start=2):
            filename = str(raw.get("filename") or "").strip()
            plate = canonical_plate(raw.get("plate"))
            layout = str(raw.get("layout") or "").strip().lower() or None
            if not filename and not plate and layout is None:
                continue
            if not filename or not plate:
                raise ValueError(f"Invalid ground truth at line {line_number}")
            key = filename.casefold()
            if key in seen:
                raise ValueError(f"Duplicate filename in ground truth: {filename}")
            seen.add(key)
            if layout not in (None, "one_line", "two_line"):
                raise ValueError(
                    f"Invalid layout at line {line_number}: use one_line or two_line"
                )
            rows.append({"filename": filename, "plate": plate, "layout": layout})
    return rows


def class_ids(model: YOLO, expected_name: str) -> set[int]:
    names = model.names
    items = names.items() if isinstance(names, dict) else enumerate(names)
    matches = {int(index) for index, name in items if str(name).strip().lower() == expected_name}
    if not matches:
        raise ValueError(f"Model does not contain class {expected_name!r}: {names}")
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


def detect_plates(
    model: YOLO,
    image: np.ndarray,
    allowed_classes: set[int],
    args: argparse.Namespace,
) -> list[dict]:
    height, width = image.shape[:2]
    result = model.predict(
        source=image,
        conf=args.conf,
        iou=args.iou,
        imgsz=args.imgsz,
        device=args.device,
        verbose=False,
    )[0]
    detections: list[dict] = []
    if result.boxes is not None:
        for box in result.boxes:
            if int(box.cls.item()) not in allowed_classes:
                continue
            bbox = integer_box(box.xyxy[0].detach().cpu().tolist(), width, height)
            if bbox is not None:
                detections.append(
                    {"bbox": bbox, "confidence": round(float(box.conf.item()), 6)}
                )
    detections.sort(key=lambda item: item["confidence"], reverse=True)
    return detections


def padded_crop(image: np.ndarray, bbox: list[int], ratio: float) -> np.ndarray:
    left, top, right, bottom = bbox
    width, height = right - left, bottom - top
    pad_x, pad_y = round(width * ratio), round(height * ratio)
    left, top = max(0, left - pad_x), max(0, top - pad_y)
    right, bottom = min(image.shape[1], right + pad_x), min(image.shape[0], bottom + pad_y)
    return image[top:bottom, left:right].copy()


def run_ocr(ocr_python: Path, crop_path: Path) -> dict:
    process = subprocess.run(
        [str(ocr_python), str(OCR_WORKER), "--image", str(crop_path)],
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
            "error": f"OCR worker returned invalid JSON (exit {process.returncode})",
        }


def levenshtein_alignment(expected: str, predicted: str) -> tuple[int, list[tuple[str, str]]]:
    rows, columns = len(expected) + 1, len(predicted) + 1
    costs = [[0] * columns for _ in range(rows)]
    operations: list[list[str | None]] = [[None] * columns for _ in range(rows)]
    for row in range(1, rows):
        costs[row][0], operations[row][0] = row, "delete"
    for column in range(1, columns):
        costs[0][column], operations[0][column] = column, "insert"
    for row in range(1, rows):
        for column in range(1, columns):
            substitution_cost = 0 if expected[row - 1] == predicted[column - 1] else 1
            options = [
                (costs[row - 1][column - 1] + substitution_cost,
                 "match" if substitution_cost == 0 else "substitute"),
                (costs[row - 1][column] + 1, "delete"),
                (costs[row][column - 1] + 1, "insert"),
            ]
            costs[row][column], operations[row][column] = min(options, key=lambda item: item[0])

    errors: list[tuple[str, str]] = []
    row, column = len(expected), len(predicted)
    while row or column:
        operation = operations[row][column]
        if operation == "match":
            row, column = row - 1, column - 1
        elif operation == "substitute":
            errors.append((expected[row - 1], predicted[column - 1]))
            row, column = row - 1, column - 1
        elif operation == "delete":
            errors.append((expected[row - 1], "∅"))
            row -= 1
        elif operation == "insert":
            errors.append(("∅", predicted[column - 1]))
            column -= 1
        else:
            break
    errors.reverse()
    return costs[-1][-1], errors


def rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def layout_metrics(rows: list[dict], layout: str) -> dict | None:
    selected = [row for row in rows if row["ground_truth_layout"] == layout]
    if not selected:
        return None
    confidences = [
        float(row["ocr_confidence"])
        for row in selected
        if row["ocr_confidence"] is not None
    ]
    non_null = sum(bool(canonical_plate(row["ocr_normalized"])) for row in selected)
    return {
        "images": len(selected),
        "ocr_non_null_count": non_null,
        "ocr_non_null_rate": rate(non_null, len(selected)),
        "exact_matches": sum(bool(row["exact_match"]) for row in selected),
        "exact_plate_accuracy": rate(
            sum(bool(row["exact_match"]) for row in selected), len(selected)
        ),
        "character_accuracy": round(
            sum(float(row["character_accuracy"]) for row in selected) / len(selected), 6
        ),
        "average_levenshtein_distance": round(
            sum(int(row["levenshtein_distance"]) for row in selected) / len(selected), 6
        ),
        "average_ocr_confidence": (
            round(sum(confidences) / len(confidences), 6) if confidences else None
        ),
    }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_results(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=RESULT_FIELDS)
        writer.writeheader()
        for row in rows:
            output = {key: row.get(key) for key in RESULT_FIELDS}
            output["plate_bbox"] = json.dumps(output["plate_bbox"]) if output["plate_bbox"] else ""
            writer.writerow(output)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    for name in ("conf", "iou", "plate_padding"):
        validate_probability(f"--{name.replace('_', '-')}", getattr(args, name))

    dataset_dir = args.dataset_dir.expanduser().resolve()
    if args.mode == "full":
        images_dir = dataset_dir / "full_images"
        ground_truth_path = dataset_dir / "ground_truth_full.csv"
        default_mode_output = DEFAULT_OUTPUT / "full"
    else:
        images_dir = dataset_dir / "ocr_crops"
        ground_truth_path = dataset_dir / "ground_truth_ocr.csv"
        default_mode_output = DEFAULT_OUTPUT / "ocr_only"
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir is not None
        else default_mode_output.resolve()
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    if not images_dir.is_dir():
        raise FileNotFoundError(images_dir)
    labels = load_ground_truth(ground_truth_path)

    model_path = args.plate_model.expanduser().resolve()
    if args.mode == "full" and not model_path.is_file():
        raise FileNotFoundError(model_path)
    ocr_python = resolve_ocr_python(args.ocr_python)
    if (
        args.mode == "full"
        and labels
        and str(args.device).startswith("0")
        and not torch.cuda.is_available()
    ):
        raise RuntimeError("device=0 requested but torch.cuda.is_available() is False")

    model: YOLO | None = None
    plate_classes: set[int] = set()
    if args.mode == "full" and labels:
        model = YOLO(str(model_path))
        plate_classes = class_ids(model, "license_plate")

    results: list[dict] = []
    character_errors: Counter[tuple[str, str]] = Counter()
    with tempfile.TemporaryDirectory(prefix="plate_ocr_", dir=output_dir) as temporary:
        temporary_dir = Path(temporary)
        for index, label in enumerate(labels, start=1):
            image_path = (images_dir / label["filename"]).resolve()
            if images_dir not in image_path.parents:
                raise ValueError(f"Image path escapes benchmark directory: {label['filename']}")
            row = {
                "mode": args.mode,
                "filename": label["filename"],
                "ground_truth": label["plate"],
                "ground_truth_layout": label["layout"],
                "plate_detected": False if args.mode == "full" else None,
                "plate_confidence": None,
                "plate_bbox": None,
                "plate_candidate_count": 0,
                "ocr_raw": None,
                "ocr_normalized": None,
                "ocr_confidence": None,
                "ocr_layout": None,
                "exact_match": False,
                "character_accuracy": 0.0,
                "levenshtein_distance": None,
                "error": None,
            }
            try:
                image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
                if image is None or image.size == 0:
                    raise ValueError("Image missing or unreadable")
                ocr: dict | None = None
                if args.mode == "full":
                    detections = detect_plates(model, image, plate_classes, args)
                    row["plate_candidate_count"] = len(detections)
                    if detections:
                        selected = detections[0]
                        row["plate_detected"] = True
                        row["plate_confidence"] = selected["confidence"]
                        row["plate_bbox"] = selected["bbox"]
                        crop = padded_crop(image, selected["bbox"], args.plate_padding)
                        crop_path = temporary_dir / f"{index:06d}.jpg"
                        if not cv2.imwrite(str(crop_path), crop):
                            raise OSError("Cannot create temporary plate crop")
                        ocr = run_ocr(ocr_python, crop_path)
                else:
                    # Input is already an actual plate crop; Plate YOLO is never loaded or run.
                    ocr = run_ocr(ocr_python, image_path)

                if ocr is not None:
                    row["ocr_raw"] = ocr.get("raw_text")
                    row["ocr_normalized"] = ocr.get("plate")
                    row["ocr_confidence"] = ocr.get("ocr_confidence")
                    row["ocr_layout"] = ocr.get("layout")
                    if not ocr.get("ok", False):
                        row["error"] = ocr.get("error", "OCR worker failed")
            except Exception as exc:
                row["error"] = f"{type(exc).__name__}: {exc}"

            exact_prediction = canonical_plate(row["ocr_normalized"])
            character_prediction = canonical_plate(row["ocr_normalized"] or row["ocr_raw"])
            row["exact_match"] = bool(exact_prediction) and exact_prediction == label["plate"]
            distance, errors = levenshtein_alignment(label["plate"], character_prediction)
            row["levenshtein_distance"] = distance
            row["character_accuracy"] = round(
                max(0.0, 1.0 - distance / max(1, len(label["plate"]))), 6
            )
            character_errors.update(errors)
            results.append(row)

    total = len(results)
    plate_detected = sum(bool(row["plate_detected"]) for row in results)
    ocr_text_success = sum(bool(canonical_plate(row["ocr_raw"])) for row in results)
    normalized_success = sum(bool(canonical_plate(row["ocr_normalized"])) for row in results)
    exact_matches = sum(bool(row["exact_match"]) for row in results)
    ocr_confidences = [
        float(row["ocr_confidence"])
        for row in results
        if row["ocr_confidence"] is not None
    ]
    summary = {
        "status": "complete" if total else "no_data",
        "mode": args.mode,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_directory": str(dataset_dir),
        "images_directory": str(images_dir),
        "ground_truth_file": str(ground_truth_path),
        "plate_model": str(model_path) if args.mode == "full" else None,
        "plate_model_sha256": file_sha256(model_path) if args.mode == "full" else None,
        "plate_detector_executed": args.mode == "full" and bool(labels),
        "ocr_worker": str(OCR_WORKER),
        "ground_truth_used_for_inference": False,
        "plate_detection_settings": (
            {
                "confidence": args.conf,
                "iou": args.iou,
                "imgsz": args.imgsz,
                "plate_padding": args.plate_padding,
                "device": str(args.device),
            }
            if args.mode == "full"
            else None
        ),
        "definitions": {
            "ocr_success_rate": "Tesseract returned at least one alphanumeric character",
            "ocr_non_null_rate": "normalized OCR result is not null",
            "normalized_success_rate": "OCR output passed the existing plate normalizer",
            "character_accuracy": "max(0, 1 - Levenshtein distance / ground-truth length); raw OCR is used when normalization is null",
            "null_failure_rate": "normalized OCR result is null",
        },
        "images": total,
        "plate_detector_success_count": plate_detected if args.mode == "full" else None,
        "plate_detector_success_rate": (
            rate(plate_detected, total) if args.mode == "full" else None
        ),
        "ocr_success_count": ocr_text_success,
        "ocr_success_rate": rate(ocr_text_success, total),
        "ocr_non_null_count": normalized_success,
        "ocr_non_null_rate": rate(normalized_success, total),
        "normalized_success_count": normalized_success,
        "normalized_success_rate": rate(normalized_success, total),
        "exact_match_count": exact_matches,
        "exact_plate_accuracy": rate(exact_matches, total),
        "character_accuracy": (
            round(sum(float(row["character_accuracy"]) for row in results) / total, 6)
            if total
            else None
        ),
        "average_ocr_confidence": (
            round(sum(ocr_confidences) / len(ocr_confidences), 6)
            if ocr_confidences
            else None
        ),
        "average_levenshtein_distance": (
            round(
                sum(int(row["levenshtein_distance"]) for row in results) / total,
                6,
            )
            if total
            else None
        ),
        "null_failure_count": total - normalized_success,
        "null_failure_rate": rate(total - normalized_success, total),
        "one_line": layout_metrics(results, "one_line"),
        "two_line": layout_metrics(results, "two_line"),
        "top_character_errors": [
            {"expected": expected, "predicted": predicted, "count": count}
            for (expected, predicted), count in character_errors.most_common(20)
        ],
        "errors": sum(bool(row["error"]) for row in results),
        "results_csv": str(output_dir / "results.csv"),
    }
    write_results(output_dir / "results.csv", results)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
