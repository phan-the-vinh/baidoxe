"""Kiểm tra tính hợp lệ của dataset License Plate YOLO v1."""

from __future__ import annotations

import argparse
import hashlib
import math
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError


SPLITS = ("train", "val", "test")
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
EXPECTED_CLASS_ID = 0
ROBOFLOW_NAME = re.compile(r"^(?P<source>.+)\.rf\.[0-9a-f]+$", re.IGNORECASE)


def default_dataset_root() -> Path:
    return Path(__file__).resolve().parents[1] / "dataset" / "license_plate_yolo_v1"


def _image_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        (path for path in directory.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS),
        key=lambda path: path.name.lower(),
    )


def _label_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        (path for path in directory.iterdir() if path.is_file() and path.suffix.lower() == ".txt"),
        key=lambda path: path.name.lower(),
    )


def _validate_image(path: Path) -> str | None:
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            if image.width <= 0 or image.height <= 0:
                return "kích thước ảnh không hợp lệ"
            image.load()
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        return str(exc)
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_group(path: Path) -> str:
    match = ROBOFLOW_NAME.fullmatch(path.stem)
    return (match.group("source") if match else path.stem).casefold()


def _validate_label(path: Path) -> tuple[int, list[str], bool]:
    issues: list[str] = []
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        return 0, [f"không đọc được label: {exc}"], False

    if not text.strip():
        return 0, [], True

    box_count = 0
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        box_count += 1
        fields = line.split()
        if len(fields) != 5:
            issues.append(f"dòng {line_number}: phải có đúng 5 trường, hiện có {len(fields)}")
            continue

        try:
            class_id = int(fields[0])
        except ValueError:
            issues.append(f"dòng {line_number}: class không phải số nguyên ({fields[0]!r})")
            continue

        if class_id != EXPECTED_CLASS_ID:
            issues.append(f"dòng {line_number}: class {class_id}, chỉ chấp nhận class 0")

        try:
            x_center, y_center, width, height = (float(value) for value in fields[1:])
        except ValueError:
            issues.append(f"dòng {line_number}: tọa độ bbox không phải số")
            continue

        values = (x_center, y_center, width, height)
        if not all(math.isfinite(value) for value in values):
            issues.append(f"dòng {line_number}: bbox chứa NaN hoặc Infinity")
            continue
        if any(value < 0.0 or value > 1.0 for value in values):
            issues.append(f"dòng {line_number}: giá trị bbox nằm ngoài khoảng 0..1")
        if width <= 0.0 or height <= 0.0:
            issues.append(f"dòng {line_number}: width/height phải lớn hơn 0")

        left = x_center - width / 2.0
        right = x_center + width / 2.0
        top = y_center - height / 2.0
        bottom = y_center + height / 2.0
        if left < 0.0 or top < 0.0 or right > 1.0 or bottom > 1.0:
            issues.append(f"dòng {line_number}: biên bbox vượt ra ngoài ảnh chuẩn hóa")

    return box_count, issues, False


def analyze_dataset(dataset_root: Path) -> dict[str, Any]:
    dataset_root = dataset_root.resolve()
    result: dict[str, Any] = {
        "dataset_root": str(dataset_root),
        "splits": {},
        "duplicate_image_filenames": {},
        "duplicate_label_filenames": {},
        "duplicate_image_contents": {},
        "source_group_leakage": {},
        "missing_directories": [],
        "total_boxes": 0,
        "issue_count": 0,
    }
    image_names: defaultdict[str, list[str]] = defaultdict(list)
    label_names: defaultdict[str, list[str]] = defaultdict(list)
    image_contents: defaultdict[str, list[str]] = defaultdict(list)
    source_groups: defaultdict[str, list[tuple[str, str]]] = defaultdict(list)

    for split in SPLITS:
        image_dir = dataset_root / "images" / split
        label_dir = dataset_root / "labels" / split
        for directory in (image_dir, label_dir):
            if not directory.is_dir():
                result["missing_directories"].append(str(directory))
        images = _image_files(image_dir)
        labels = _label_files(label_dir)
        image_stems = {path.stem.casefold(): path for path in images}
        label_stems = {path.stem.casefold(): path for path in labels}

        for path in images:
            image_names[path.name.casefold()].append(str(path))
            image_contents[_sha256(path)].append(str(path))
            source_groups[_source_group(path)].append((split, str(path)))
        for path in labels:
            label_names[path.name.casefold()].append(str(path))

        missing_labels = [str(path) for key, path in image_stems.items() if key not in label_stems]
        labels_without_images = [str(path) for key, path in label_stems.items() if key not in image_stems]
        corrupt_images: list[dict[str, str]] = []
        empty_labels: list[str] = []
        invalid_labels: list[dict[str, Any]] = []
        split_boxes = 0

        for image_path in images:
            error = _validate_image(image_path)
            if error:
                corrupt_images.append({"file": str(image_path), "error": error})

        for label_path in labels:
            box_count, issues, is_empty = _validate_label(label_path)
            split_boxes += box_count
            if is_empty:
                empty_labels.append(str(label_path))
            if issues:
                invalid_labels.append({"file": str(label_path), "issues": issues})

        split_issue_count = (
            len(missing_labels)
            + len(labels_without_images)
            + len(corrupt_images)
            + len(empty_labels)
            + sum(len(item["issues"]) for item in invalid_labels)
        )
        result["splits"][split] = {
            "images": len(images),
            "labels": len(labels),
            "boxes": split_boxes,
            "missing_labels": missing_labels,
            "labels_without_images": labels_without_images,
            "corrupt_images": corrupt_images,
            "empty_labels": empty_labels,
            "invalid_labels": invalid_labels,
            "issue_count": split_issue_count,
        }
        result["total_boxes"] += split_boxes
        result["issue_count"] += split_issue_count

    result["duplicate_image_filenames"] = {
        name: paths for name, paths in image_names.items() if len(paths) > 1
    }
    result["duplicate_label_filenames"] = {
        name: paths for name, paths in label_names.items() if len(paths) > 1
    }
    result["duplicate_image_contents"] = {
        digest: paths for digest, paths in image_contents.items() if len(paths) > 1
    }
    result["source_group_leakage"] = {
        group: entries
        for group, entries in source_groups.items()
        if len({split for split, _ in entries}) > 1
    }
    duplicate_count = sum(len(paths) - 1 for paths in result["duplicate_image_filenames"].values())
    duplicate_count += sum(len(paths) - 1 for paths in result["duplicate_label_filenames"].values())
    duplicate_count += sum(len(paths) - 1 for paths in result["duplicate_image_contents"].values())
    result["issue_count"] += duplicate_count + len(result["source_group_leakage"]) + len(result["missing_directories"])
    return result


def _print_paths(title: str, paths: list[str]) -> None:
    if not paths:
        return
    print(f"  {title} ({len(paths)}):")
    for path in paths:
        print(f"    - {path}")


def print_summary(result: dict[str, Any]) -> None:
    print("LICENSE PLATE YOLO DATASET CHECK")
    print(f"Dataset: {result['dataset_root']}")
    print("Class hợp lệ duy nhất: 0 = license_plate")
    print()

    total_images = 0
    total_labels = 0
    for split in SPLITS:
        details = result["splits"][split]
        total_images += details["images"]
        total_labels += details["labels"]
        print(
            f"[{split}] images={details['images']} labels={details['labels']} "
            f"boxes={details['boxes']} issues={details['issue_count']}"
        )
        _print_paths("Ảnh thiếu label", details["missing_labels"])
        _print_paths("Label thiếu ảnh", details["labels_without_images"])
        _print_paths("Label rỗng", details["empty_labels"])
        if details["corrupt_images"]:
            print(f"  Ảnh lỗi/không đọc được ({len(details['corrupt_images'])}):")
            for item in details["corrupt_images"]:
                print(f"    - {item['file']}: {item['error']}")
        if details["invalid_labels"]:
            print(f"  Label không hợp lệ ({len(details['invalid_labels'])} file):")
            for item in details["invalid_labels"]:
                print(f"    - {item['file']}")
                for issue in item["issues"]:
                    print(f"      * {issue}")

    if result["duplicate_image_filenames"]:
        print("\nTên ảnh trùng giữa các tập:")
        for name, paths in result["duplicate_image_filenames"].items():
            print(f"  - {name}: {', '.join(paths)}")
    if result["duplicate_label_filenames"]:
        print("\nTên label trùng giữa các tập:")
        for name, paths in result["duplicate_label_filenames"].items():
            print(f"  - {name}: {', '.join(paths)}")
    if result["duplicate_image_contents"]:
        print("\nẢnh trùng nội dung SHA-256:")
        for digest, paths in result["duplicate_image_contents"].items():
            print(f"  - {digest}: {', '.join(paths)}")
    if result["source_group_leakage"]:
        print("\nNhóm ảnh nguồn xuất hiện ở nhiều split:")
        for group, entries in result["source_group_leakage"].items():
            formatted = ", ".join(f"{split}:{path}" for split, path in entries)
            print(f"  - {group}: {formatted}")
    _print_paths("Thiếu thư mục", result["missing_directories"])

    print()
    print(f"Tổng ảnh: {total_images}")
    print(f"Tổng label: {total_labels}")
    print(f"Tổng bbox license_plate: {result['total_boxes']}")
    print(f"Leakage nhóm nguồn: {len(result['source_group_leakage'])}")
    print(f"Nhóm ảnh trùng nội dung: {len(result['duplicate_image_contents'])}")
    print(f"Tổng vấn đề: {result['issue_count']}")
    print("Kết quả: HỢP LỆ" if result["issue_count"] == 0 else "Kết quả: CÓ VẤN ĐỀ")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=default_dataset_root(),
        help="Đường dẫn dataset cần kiểm tra",
    )
    args = parser.parse_args()
    result = analyze_dataset(args.dataset)
    print_summary(result)
    return 0 if result["issue_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
