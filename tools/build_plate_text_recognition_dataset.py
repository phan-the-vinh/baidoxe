"""Build a review-first license-plate text-recognition dataset.

Stages intentionally run in the environments that already own each dependency:

* ``crop``: yolo_train (Ultralytics + CUDA)
* ``ocr``: parking_ai (pytesseract + configured Tesseract executable)

OCR output is always a pseudo-label with NEED_REVIEW status. This script never
promotes OCR output to VERIFIED and never modifies source images or models.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import string
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SOURCE_DATASET = ROOT / "dataset" / "license_plate_yolo_v1"
SOURCE_MANIFEST = SOURCE_DATASET / "split_manifest.json"
RAW_SOURCE = ROOT / "dataset" / "roboflow_car_plate_raw" / "car-plate.v2i.yolov8"
TARGET = ROOT / "dataset" / "plate_text_recognition_v1"
TARGET_IMAGES = TARGET / "images"
PLATE_MODEL = ROOT / "runs" / "detect" / "runs" / "license_plate" / "train_30" / "weights" / "best.pt"
MANIFEST_CSV = TARGET / "crop_manifest.csv"
LABELS_CSV = TARGET / "labels.csv"
QUEUE_CSV = TARGET / "review_queue.csv"
SUMMARY_JSON = TARGET / "dataset_summary.json"
METADATA_AUDIT_JSON = TARGET / "metadata_audit.json"
OCR_RESULTS_JSONL = TARGET / "ocr_results.jsonl"
README = TARGET / "README.md"
ALLOWED_TEXT = set(string.ascii_uppercase + string.digits)
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
PLATE_TEXT_PATTERN = re.compile(
    r"(?<![A-Z0-9])\d{2}[\s._-]?[A-Z]{1,3}[\s._-]?\d{4,6}(?![A-Z0-9])",
    re.IGNORECASE,
)

MANIFEST_FIELDS = [
    "filename", "source", "source_id", "source_split", "source_sha256",
    "source_width", "source_height", "detection_index", "plate_confidence",
    "bbox_x1", "bbox_y1", "bbox_x2", "bbox_y2", "crop_x1", "crop_y1",
    "crop_x2", "crop_y2", "padding_ratio", "crop_width", "crop_height",
]
LABEL_FIELDS = ["filename", "text", "status", "source", "layout"]
QUEUE_FIELDS = [
    "filename", "pseudo_text", "raw_text", "normalized_text",
    "ocr_confidence", "status", "source", "layout",
    "plate_confidence", "bbox", "ocr_error",
]


README_TEXT = """# Plate Text Recognition v1

Dataset dùng để xây dựng mô hình nhận dạng nội dung biển số từ **crop biển số**.

## Quy tắc nhãn

- OCR/Tesseract chỉ tạo `pseudo_text` với trạng thái `NEED_REVIEW`.
- OCR không phải ground truth và không bao giờ tự chuyển thành `VERIFIED`.
- Chỉ các dòng `labels.csv` có `status=VERIFIED` mới được dùng để train recognition.
- Text canonical chỉ chứa `A-Z` và `0-9`; dấu chấm, gạch nối và khoảng trắng có thể được bỏ khi người kiểm duyệt xác nhận.
- Không thay `0/O`, `1/I`, thêm ký tự hoặc đoán ký tự bằng regex.

## Nguồn và pipeline

Một ảnh đại diện perceptual-medoid được chọn cho mỗi trong 744 nhóm ảnh nguồn của
`dataset/license_plate_yolo_v1`. Việc này tránh đưa ba biến thể augmentation của
cùng ảnh vào hàng đợi review.

```text
source car image
  -> trained Plate YOLO best.pt
  -> padded plate crop
  -> existing OpenCV + Tesseract OCR
  -> NEED_REVIEW
  -> human confirmation
  -> VERIFIED
```

`crop_manifest.csv` lưu đầy đủ ánh xạ ảnh nguồn, bbox YOLO, confidence và crop.

## Chạy lại pipeline

```powershell
conda activate yolo_train
python tools/build_plate_text_recognition_dataset.py --stage crop --device 0

conda activate parking_ai
python tools/build_plate_text_recognition_dataset.py --stage ocr --ocr-workers 2

python tools/review_plate_text_dataset.py
```

Không train recognition model cho đến khi có đủ nhãn `VERIFIED` và chia tập theo
nhóm ảnh nguồn để tránh leakage.
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fields: list[str], rows: Iterable[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def difference_hash(image: np.ndarray) -> int:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    comparisons = small[:, :-1] > small[:, 1:]
    value = 0
    for bit in comparisons.flat:
        value = (value << 1) | int(bit)
    return value


def sharpness(image: np.ndarray) -> float:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def select_canonical(paths: list[Path]) -> Path:
    if len(paths) == 1:
        return paths[0]
    images: dict[Path, np.ndarray] = {}
    hashes: dict[Path, int] = {}
    sharpness_scores: dict[Path, float] = {}
    for path in paths:
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"Không đọc được ảnh nguồn: {path}")
        images[path] = image
        hashes[path] = difference_hash(image)
        sharpness_scores[path] = sharpness(image)
    return min(
        paths,
        key=lambda path: (
            sum((hashes[path] ^ hashes[other]).bit_count() for other in paths if other != path),
            -sharpness_scores[path],
            path.name.casefold(),
        ),
    )


def source_records() -> list[dict[str, Any]]:
    if not SOURCE_MANIFEST.is_file():
        raise FileNotFoundError(f"Thiếu source manifest: {SOURCE_MANIFEST}")
    payload = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))
    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in payload.get("records", []):
        grouped[str(record["source_id"])].append(record)
    if len(grouped) != 744:
        raise ValueError(f"Cần 744 nhóm nguồn, nhận {len(grouped)}")

    selected: list[dict[str, Any]] = []
    for source_id, records in sorted(grouped.items(), key=lambda item: item[0].casefold()):
        splits = {str(record["split"]) for record in records}
        if len(splits) != 1:
            raise ValueError(f"Source group bị chia split: {source_id} -> {sorted(splits)}")
        candidates = [ROOT / str(record["target_image"]) for record in records]
        missing = [path for path in candidates if not path.is_file()]
        if missing:
            raise FileNotFoundError(f"Thiếu ảnh nguồn: {missing[0]}")
        chosen = select_canonical(candidates)
        selected.append(
            {
                "source_id": source_id,
                "source_split": next(iter(splits)),
                "path": chosen,
                "group_variant_count": len(candidates),
            }
        )
    return selected


def canonical_ocr_text(raw_text: str | None) -> str:
    """Remove separators only; never substitute, infer, or append characters."""
    return "".join(character for character in str(raw_text or "").upper() if character in ALLOWED_TEXT)


def metadata_audit() -> dict[str, Any]:
    inspected_roots = [SOURCE_DATASET, RAW_SOURCE]
    metadata_extensions = {".csv", ".json", ".xml", ".txt", ".md", ".yaml", ".yml"}
    files: list[str] = []
    filename_matches: list[dict[str, str]] = []
    content_matches: list[dict[str, str]] = []
    for root in inspected_roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if PLATE_TEXT_PATTERN.search(path.stem):
                filename_matches.append({"file": relative(path), "match": PLATE_TEXT_PATTERN.search(path.stem).group(0)})
            if path.suffix.lower() not in metadata_extensions:
                continue
            if "labels" in {part.casefold() for part in path.parts} or path.suffix.lower() == ".cache":
                continue
            files.append(relative(path))
            if path.stat().st_size > 20 * 1024 * 1024:
                continue
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            for match in PLATE_TEXT_PATTERN.finditer(text):
                content_matches.append({"file": relative(path), "match": match.group(0)})

    # A filename/text occurrence alone is not accepted as trustworthy transcription.
    # This source contains only detection bbox labels and source manifests.
    return {
        "checked_at_utc": utc_now(),
        "inspected_roots": [relative(path) for path in inspected_roots if path.exists()],
        "metadata_files": sorted(files),
        "plate_like_filename_matches": filename_matches,
        "plate_like_content_matches": content_matches,
        "trustworthy_full_plate_transcription_found": False,
        "ground_truth_source": None,
        "reason": "Source annotations contain detection bounding boxes only; no reviewed full-plate transcription mapping was found.",
    }


def ensure_scaffold() -> None:
    TARGET_IMAGES.mkdir(parents=True, exist_ok=True)
    if not README.exists():
        README.write_text(README_TEXT, encoding="utf-8")
    audit = metadata_audit()
    METADATA_AUDIT_JSON.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not LABELS_CSV.exists():
        write_csv(LABELS_CSV, LABEL_FIELDS, [])
    if not QUEUE_CSV.exists():
        write_csv(QUEUE_CSV, QUEUE_FIELDS, [])
    update_summary()


def integer_box(values: Iterable[float], width: int, height: int) -> list[int] | None:
    x1, y1, x2, y2 = (float(value) for value in values)
    left = max(0, min(width - 1, int(math.floor(x1))))
    top = max(0, min(height - 1, int(math.floor(y1))))
    right = max(0, min(width, int(math.ceil(x2))))
    bottom = max(0, min(height, int(math.ceil(y2))))
    return [left, top, right, bottom] if right > left and bottom > top else None


def padded_box(box: list[int], width: int, height: int, ratio: float) -> list[int]:
    left, top, right, bottom = box
    pad_x = round((right - left) * ratio)
    pad_y = round((bottom - top) * ratio)
    return [max(0, left - pad_x), max(0, top - pad_y), min(width, right + pad_x), min(height, bottom + pad_y)]


def validate_fresh_crop_target() -> None:
    existing_images = [path for path in TARGET_IMAGES.iterdir() if path.is_file()]
    existing_manifest = read_csv(MANIFEST_CSV)
    if existing_images or existing_manifest:
        raise RuntimeError(
            "Dataset đích đã có crop/manifest; không tự ghi đè. "
            "Hãy giữ nguyên để resume OCR hoặc chọn target khác sau khi kiểm tra."
        )


def crop_stage(args: argparse.Namespace) -> None:
    from ultralytics import YOLO
    import torch

    ensure_scaffold()
    validate_fresh_crop_target()
    if not PLATE_MODEL.is_file():
        raise FileNotFoundError(f"Thiếu Plate model: {PLATE_MODEL}")
    if str(args.device) != "cpu" and not torch.cuda.is_available():
        raise RuntimeError("CUDA không hoạt động trong môi trường crop.")

    records = source_records()
    model = YOLO(str(PLATE_MODEL))
    names = model.names if isinstance(model.names, dict) else dict(enumerate(model.names))
    plate_classes = {int(index) for index, name in names.items() if str(name).strip().lower() == "license_plate"}
    if not plate_classes:
        raise ValueError(f"Model không có class license_plate: {names}")

    manifest_rows: list[dict[str, Any]] = []
    source_results: list[dict[str, Any]] = []
    crop_counter = 0
    # Run one image at a time. Passing hundreds of mixed-size images as one
    # source list made Ultralytics retain an oversized CUDA batch on a 6 GB GPU.
    for source_index, record in enumerate(records, start=1):
        source_path: Path = record["path"]
        image = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"Không đọc được ảnh nguồn: {source_path}")
        height, width = image.shape[:2]
        result = model.predict(
            source=image,
            conf=args.conf,
            iou=args.iou,
            imgsz=args.imgsz,
            device=args.device,
            max_det=args.max_det,
            verbose=False,
        )[0]
        detections = []
        if result.boxes is not None:
            for box in result.boxes:
                class_id = int(box.cls.item())
                if class_id not in plate_classes:
                    continue
                integer = integer_box(box.xyxy[0].detach().cpu().tolist(), width, height)
                if integer is None:
                    continue
                detections.append(
                    {"bbox": integer, "confidence": float(box.conf.item())}
                )
        detections.sort(key=lambda item: (-item["confidence"], item["bbox"][0], item["bbox"][1]))
        saved = 0
        source_digest = sha256(source_path)
        for detection_index, detection in enumerate(detections, start=1):
            box = detection["bbox"]
            crop_bounds = padded_box(box, width, height, args.padding)
            left, top, right, bottom = crop_bounds
            crop = image[top:bottom, left:right]
            if crop.size == 0 or crop.shape[0] < 4 or crop.shape[1] < 4:
                continue
            crop_counter += 1
            filename = f"plate_{crop_counter:06d}.jpg"
            destination = TARGET_IMAGES / filename
            if not cv2.imwrite(str(destination), crop, [cv2.IMWRITE_JPEG_QUALITY, args.jpeg_quality]):
                raise OSError(f"Không ghi được crop: {destination}")
            manifest_rows.append(
                {
                    "filename": filename,
                    "source": relative(source_path),
                    "source_id": record["source_id"],
                    "source_split": record["source_split"],
                    "source_sha256": source_digest,
                    "source_width": width,
                    "source_height": height,
                    "detection_index": detection_index,
                    "plate_confidence": f"{detection['confidence']:.6f}",
                    "bbox_x1": box[0], "bbox_y1": box[1], "bbox_x2": box[2], "bbox_y2": box[3],
                    "crop_x1": left, "crop_y1": top, "crop_x2": right, "crop_y2": bottom,
                    "padding_ratio": f"{args.padding:.4f}",
                    "crop_width": crop.shape[1], "crop_height": crop.shape[0],
                }
            )
            saved += 1
        source_results.append(
            {
                "source_id": record["source_id"],
                "source": relative(source_path),
                "source_split": record["source_split"],
                "detections": saved,
            }
        )
        if source_index % 50 == 0 or source_index == len(records):
            print(f"crop {source_index}/{len(records)} sources, {crop_counter} plates", flush=True)

    write_csv(MANIFEST_CSV, MANIFEST_FIELDS, manifest_rows)
    (TARGET / "source_detection_results.json").write_text(
        json.dumps(source_results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_csv(
        LABELS_CSV,
        LABEL_FIELDS,
        [
            {
                "filename": row["filename"], "text": "", "status": "NEED_REVIEW",
                "source": row["source"], "layout": "",
            }
            for row in manifest_rows
        ],
    )
    update_summary()
    print(f"CROP_COMPLETE sources={len(records)} crops={len(manifest_rows)} target={TARGET}")


def ocr_task(task: tuple[str, str]) -> dict[str, Any]:
    filename, absolute_path = task
    try:
        from tools.ocr_plate_crop_worker import recognize_exact_crop

        result = recognize_exact_crop(Path(absolute_path), None)
        raw_text = result.get("raw_text")
        normalized = canonical_ocr_text(raw_text)
        return {
            "filename": filename,
            "raw_text": raw_text or "",
            "normalized_text": normalized,
            "ocr_confidence": result.get("ocr_confidence"),
            "layout": result.get("layout") or "",
            "ocr_error": "",
        }
    except Exception as exc:
        return {
            "filename": filename,
            "raw_text": "",
            "normalized_text": "",
            "ocr_confidence": None,
            "layout": "",
            "ocr_error": f"{type(exc).__name__}: {exc}",
        }


def load_ocr_results() -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    if not OCR_RESULTS_JSONL.is_file():
        return results
    for line_number, line in enumerate(OCR_RESULTS_JSONL.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSONL lỗi dòng {line_number}: {exc}") from exc
        results[str(payload["filename"])] = payload
    return results


def append_ocr_result(result: dict[str, Any]) -> None:
    with OCR_RESULTS_JSONL.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(result, ensure_ascii=False) + "\n")
        handle.flush()


def ocr_stage(args: argparse.Namespace) -> None:
    ensure_scaffold()
    manifest = read_csv(MANIFEST_CSV)
    if not manifest:
        raise RuntimeError("Chưa có crop_manifest.csv; hãy chạy stage crop trước.")
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    from app.services.plate_ocr import _tesseract_command

    command = _tesseract_command()
    print(f"Tesseract: {command}")
    existing = load_ocr_results()
    pending = [
        (row["filename"], str((TARGET_IMAGES / row["filename"]).resolve()))
        for row in manifest
        if row["filename"] not in existing
    ]
    for _, path in pending:
        if not Path(path).is_file():
            raise FileNotFoundError(f"Thiếu crop: {path}")

    processed = len(existing)
    if pending:
        with ProcessPoolExecutor(max_workers=args.ocr_workers) as executor:
            futures = {executor.submit(ocr_task, task): task[0] for task in pending}
            for future in as_completed(futures):
                result = future.result()
                existing[result["filename"]] = result
                append_ocr_result(result)
                processed += 1
                if processed % 25 == 0 or processed == len(manifest):
                    print(f"ocr {processed}/{len(manifest)}", flush=True)

    manifest_by_name = {row["filename"]: row for row in manifest}
    ordered_results = [existing[row["filename"]] for row in manifest]
    queue_rows = []
    label_rows = []
    for result in ordered_results:
        mapping = manifest_by_name[result["filename"]]
        bbox = ",".join(mapping[key] for key in ("bbox_x1", "bbox_y1", "bbox_x2", "bbox_y2"))
        queue_rows.append(
            {
                "filename": result["filename"],
                "pseudo_text": result.get("normalized_text") or "",
                "raw_text": result.get("raw_text") or "",
                "normalized_text": result.get("normalized_text") or "",
                "ocr_confidence": "" if result.get("ocr_confidence") is None else f"{float(result['ocr_confidence']):.4f}",
                "status": "NEED_REVIEW",
                "source": mapping["source"],
                "layout": result.get("layout") or "",
                "plate_confidence": mapping["plate_confidence"],
                "bbox": bbox,
                "ocr_error": result.get("ocr_error") or "",
            }
        )
        label_rows.append(
            {
                "filename": result["filename"], "text": "", "status": "NEED_REVIEW",
                "source": mapping["source"], "layout": result.get("layout") or "",
            }
        )
    write_csv(QUEUE_CSV, QUEUE_FIELDS, queue_rows)
    write_csv(LABELS_CSV, LABEL_FIELDS, label_rows)
    update_summary()
    print(f"OCR_COMPLETE crops={len(manifest)} non_null={sum(bool(row['pseudo_text']) for row in queue_rows)}")


def update_summary() -> dict[str, Any]:
    TARGET.mkdir(parents=True, exist_ok=True)
    manifest = read_csv(MANIFEST_CSV)
    labels = read_csv(LABELS_CSV)
    queue = read_csv(QUEUE_CSV)
    source_results_path = TARGET / "source_detection_results.json"
    source_results = json.loads(source_results_path.read_text(encoding="utf-8")) if source_results_path.is_file() else []
    metadata = json.loads(METADATA_AUDIT_JSON.read_text(encoding="utf-8")) if METADATA_AUDIT_JSON.is_file() else {}
    summary = {
        "dataset": "Plate Text Recognition v1",
        "path": str(TARGET.resolve()),
        "updated_at_utc": utc_now(),
        "source_dataset": relative(SOURCE_DATASET),
        "plate_model": relative(PLATE_MODEL),
        "source_policy": "one perceptual-medoid image per independent source_id",
        "source_images_considered": len(source_results) if source_results else 0,
        "source_images_with_detection": sum(int(row.get("detections", 0)) > 0 for row in source_results),
        "source_images_without_detection": sum(int(row.get("detections", 0)) == 0 for row in source_results),
        "plate_crops": len(manifest),
        "pseudo_label_non_null": sum(bool(row.get("pseudo_text", "").strip()) for row in queue),
        "pseudo_label_null": sum(not bool(row.get("pseudo_text", "").strip()) for row in queue),
        "need_review": sum(row.get("status") == "NEED_REVIEW" for row in labels),
        "verified": sum(row.get("status") == "VERIFIED" for row in labels),
        "train_eligible_verified_rows": sum(
            row.get("status") == "VERIFIED" and bool(row.get("text", "").strip()) for row in labels
        ),
        "layout_counts": dict(Counter(row.get("layout") or "unknown" for row in queue)),
        "ocr_error_count": sum(bool(row.get("ocr_error", "").strip()) for row in queue),
        "trustworthy_source_transcription_found": bool(metadata.get("trustworthy_full_plate_transcription_found", False)),
        "ground_truth_policy": "Only human-confirmed VERIFIED rows are training labels.",
        "ready_for_recognition_training": False,
    }
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def inspect_stage() -> None:
    ensure_scaffold()
    records = source_records()
    metadata = json.loads(METADATA_AUDIT_JSON.read_text(encoding="utf-8"))
    print(json.dumps({
        "source_groups": len(records),
        "splits": dict(Counter(record["source_split"] for record in records)),
        "plate_model_exists": PLATE_MODEL.is_file(),
        "target": str(TARGET),
        "trustworthy_transcription": metadata["trustworthy_full_plate_transcription_found"],
    }, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("inspect", "crop", "ocr", "summary"), default="inspect")
    parser.add_argument("--device", default="0")
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--iou", type=float, default=0.50)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--max-det", type=int, default=5)
    parser.add_argument("--padding", type=float, default=0.04)
    parser.add_argument("--jpeg-quality", type=int, default=95)
    parser.add_argument("--ocr-workers", type=int, default=2)
    args = parser.parse_args()
    if not 0 <= args.conf <= 1 or not 0 <= args.iou <= 1:
        parser.error("--conf và --iou phải nằm trong [0,1]")
    if not 0 <= args.padding <= 0.5:
        parser.error("--padding phải nằm trong [0,0.5]")
    if not 1 <= args.ocr_workers <= 4:
        parser.error("--ocr-workers phải từ 1 đến 4")
    return args


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    try:
        if args.stage == "inspect":
            inspect_stage()
        elif args.stage == "crop":
            crop_stage(args)
        elif args.stage == "ocr":
            ocr_stage(args)
        else:
            ensure_scaffold()
            print(json.dumps(update_summary(), ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
