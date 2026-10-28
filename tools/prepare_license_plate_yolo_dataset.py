"""Chuẩn hóa Roboflow car-plate v2 vào License Plate YOLO v1.

Mặc định chỉ kiểm tra. Chỉ sao chép khi truyền --execute. Script không xóa
hoặc thay đổi dữ liệu nguồn và sẽ dừng nếu dataset đích đã chứa ảnh/nhãn.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter, ImageStat, UnidentifiedImageError


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "dataset" / "roboflow_car_plate_raw" / "car-plate.v2i.yolov8"
TARGET = ROOT / "dataset" / "license_plate_yolo_v1"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
ROBOFLOW_NAME = re.compile(r"^(?P<source>.+)\.rf\.(?P<hash>[0-9a-f]+)$", re.IGNORECASE)

# Hai ranh giới này đã được đối chiếu trực quan trên contact sheet toàn bộ
# 744 source image. Chúng nằm giữa hai chuỗi xe khác nhau:
# 594 -> 595 và 669 -> 670.
TRAIN_SOURCE_COUNT = 595
VAL_SOURCE_COUNT = 75


def image_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        (path for path in directory.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS),
        key=lambda path: path.name.casefold(),
    )


def source_id(path: Path) -> str:
    match = ROBOFLOW_NAME.fullmatch(path.stem)
    if not match:
        raise ValueError(f"Tên file không theo định dạng Roboflow: {path.name}")
    return match.group("source")


def matching_label(image_path: Path) -> Path:
    return image_path.parent.parent / "labels" / f"{image_path.stem}.txt"


def validate_image(path: Path) -> str | None:
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            if image.width <= 0 or image.height <= 0:
                return "kích thước không hợp lệ"
            image.load()
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        return str(exc)
    return None


def validate_label(path: Path) -> tuple[int, list[str]]:
    issues: list[str] = []
    if not path.is_file():
        return 0, ["thiếu file label"]
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as exc:
        return 0, [f"không đọc được: {exc}"]
    populated = [line.strip() for line in lines if line.strip()]
    if not populated:
        return 0, ["label rỗng"]
    for line_number, line in enumerate(populated, start=1):
        fields = line.split()
        if len(fields) != 5:
            issues.append(f"dòng {line_number}: cần 5 trường")
            continue
        if fields[0] != "0":
            issues.append(f"dòng {line_number}: class phải là 0, nhận {fields[0]!r}")
        try:
            x_center, y_center, width, height = map(float, fields[1:])
        except ValueError:
            issues.append(f"dòng {line_number}: bbox không phải số")
            continue
        values = (x_center, y_center, width, height)
        if not all(math.isfinite(value) for value in values):
            issues.append(f"dòng {line_number}: bbox chứa NaN/Infinity")
            continue
        if any(value < 0 or value > 1 for value in values):
            issues.append(f"dòng {line_number}: giá trị bbox ngoài 0..1")
        if width <= 0 or height <= 0:
            issues.append(f"dòng {line_number}: width/height phải > 0")
        if (
            x_center - width / 2 < -1e-6
            or y_center - height / 2 < -1e-6
            or x_center + width / 2 > 1 + 1e-6
            or y_center + height / 2 > 1 + 1e-6
        ):
            issues.append(f"dòng {line_number}: biên bbox vượt ảnh")
    return len(populated), issues


def difference_hash(path: Path) -> int:
    with Image.open(path) as image:
        gray = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
        pixels = gray.tobytes()
    value = 0
    for row in range(8):
        for column in range(8):
            value = (value << 1) | int(pixels[row * 9 + column] > pixels[row * 9 + column + 1])
    return value


def sharpness(path: Path) -> float:
    with Image.open(path) as image:
        gray = image.convert("L").resize((256, 256), Image.Resampling.BILINEAR)
        edges = gray.filter(ImageFilter.FIND_EDGES)
        return float(ImageStat.Stat(edges).var[0])


def canonical_variant(paths: list[Path]) -> Path:
    """Chọn medoid cảm nhận; ưu tiên ảnh sắc nét hơn nếu bằng điểm."""
    hashes = {path: difference_hash(path) for path in paths}
    scores = {}
    for path in paths:
        distance_sum = sum((hashes[path] ^ hashes[other]).bit_count() for other in paths if other != path)
        scores[path] = (distance_sum, -sharpness(path), path.name.casefold())
    return min(paths, key=scores.__getitem__)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_source() -> dict[str, Any]:
    train_images = image_files(SOURCE / "train" / "images")
    test_images = image_files(SOURCE / "test" / "images")
    groups: defaultdict[str, list[Path]] = defaultdict(list)
    issues: list[str] = []
    dimensions: Counter[str] = Counter()
    total_boxes = 0
    content_hashes: defaultdict[str, list[str]] = defaultdict(list)

    for path in train_images + test_images:
        try:
            group_name = source_id(path)
        except ValueError as exc:
            issues.append(str(exc))
            continue
        if path in train_images:
            groups[group_name].append(path)
        error = validate_image(path)
        if error:
            issues.append(f"Ảnh lỗi {path}: {error}")
        else:
            with Image.open(path) as image:
                dimensions[f"{image.width}x{image.height}"] += 1
        box_count, label_issues = validate_label(matching_label(path))
        total_boxes += box_count
        issues.extend(f"{matching_label(path)}: {issue}" for issue in label_issues)
        content_hashes[sha256(path)].append(str(path))

    group_sizes = Counter(len(paths) for paths in groups.values())
    if len(train_images) != 2217:
        issues.append(f"Train phải có 2217 ảnh, nhận {len(train_images)}")
    if len(test_images) != 5:
        issues.append(f"Test phải có 5 ảnh, nhận {len(test_images)}")
    if len(groups) != 739 or group_sizes != Counter({3: 739}):
        issues.append(f"Grouping không đúng 739 nhóm x 3: {dict(group_sizes)}")

    exact_duplicates = [paths for paths in content_hashes.values() if len(paths) > 1]
    sorted_groups = sorted(groups.items(), key=lambda item: item[0].casefold())
    return {
        "train_images": train_images,
        "test_images": test_images,
        "groups": sorted_groups,
        "group_sizes": dict(group_sizes),
        "dimensions": dict(dimensions),
        "source_boxes": total_boxes,
        "exact_duplicate_sets": exact_duplicates,
        "issues": issues,
    }


def build_plan(audit: dict[str, Any]) -> dict[str, Any]:
    groups = audit["groups"]
    train_groups = groups[:TRAIN_SOURCE_COUNT]
    val_groups = groups[TRAIN_SOURCE_COUNT:TRAIN_SOURCE_COUNT + VAL_SOURCE_COUNT]
    test_groups = groups[TRAIN_SOURCE_COUNT + VAL_SOURCE_COUNT:]
    if [len(train_groups), len(val_groups), len(test_groups)] != [595, 75, 69]:
        raise RuntimeError("Số nhóm nguồn không khớp kế hoạch 595/75/69")

    records: list[dict[str, Any]] = []
    for group_name, paths in train_groups:
        for path in sorted(paths, key=lambda item: item.name.casefold()):
            records.append({"source_id": group_name, "split": "train", "image": path, "variant_policy": "all_train_variants"})
    for split, selected_groups in (("val", val_groups), ("test", test_groups)):
        for group_name, paths in selected_groups:
            selected = canonical_variant(paths)
            records.append({"source_id": group_name, "split": split, "image": selected, "variant_policy": "perceptual_medoid"})
    for path in audit["test_images"]:
        records.append({"source_id": source_id(path), "split": "test", "image": path, "variant_policy": "roboflow_test_original"})

    source_split_map: defaultdict[str, set[str]] = defaultdict(set)
    for record in records:
        source_split_map[record["source_id"]].add(record["split"])
    leakage = {key: sorted(value) for key, value in source_split_map.items() if len(value) > 1}
    return {
        "records": records,
        "source_counts": {"train": 595, "val": 75, "test": 74},
        "file_counts": dict(Counter(record["split"] for record in records)),
        "group_leakage": leakage,
        "boundaries": {
            "train_last": train_groups[-1][0],
            "val_first": val_groups[0][0],
            "val_last": val_groups[-1][0],
            "test_first": test_groups[0][0],
        },
    }


def ensure_target_empty() -> None:
    existing = []
    for kind in ("images", "labels"):
        for split in ("train", "val", "test"):
            directory = TARGET / kind / split
            directory.mkdir(parents=True, exist_ok=True)
            existing.extend(path for path in directory.iterdir() if path.is_file())
    if existing:
        preview = "\n".join(str(path) for path in existing[:10])
        raise RuntimeError(f"Dataset đích đã chứa dữ liệu; không tự ghi đè:\n{preview}")


def execute_plan(plan: dict[str, Any], audit: dict[str, Any]) -> Path:
    ensure_target_empty()
    manifest_records = []
    for record in plan["records"]:
        image_path: Path = record["image"]
        label_path = matching_label(image_path)
        split = record["split"]
        image_target = TARGET / "images" / split / image_path.name
        label_target = TARGET / "labels" / split / label_path.name
        shutil.copy2(image_path, image_target)
        shutil.copy2(label_path, label_target)
        manifest_records.append({
            "source_id": record["source_id"],
            "split": split,
            "variant_policy": record["variant_policy"],
            "source_image": str(image_path.relative_to(ROOT)).replace("\\", "/"),
            "target_image": str(image_target.relative_to(ROOT)).replace("\\", "/"),
            "target_label": str(label_target.relative_to(ROOT)).replace("\\", "/"),
        })

    manifest = {
        "source": str(SOURCE.relative_to(ROOT)).replace("\\", "/"),
        "target": str(TARGET.relative_to(ROOT)).replace("\\", "/"),
        "license": "CC BY 4.0",
        "roboflow_project": "car-plate-55lcp",
        "roboflow_version": 2,
        "source_train_files": len(audit["train_images"]),
        "source_test_files": len(audit["test_images"]),
        "independent_source_images": 744,
        "source_group_counts": plan["source_counts"],
        "output_file_counts": plan["file_counts"],
        "group_leakage": plan["group_leakage"],
        "boundaries": plan["boundaries"],
        "selection": {
            "train": "copy all 3 Roboflow variants for each train source group",
            "val": "one perceptual-medoid representative per source group",
            "test": "one perceptual-medoid representative plus 5 original Roboflow test images",
        },
        "records": manifest_records,
    }
    manifest_path = TARGET / "split_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def print_report(audit: dict[str, Any], plan: dict[str, Any]) -> None:
    print("ROBOfLOW LICENSE PLATE SOURCE AUDIT")
    print(f"Source train files: {len(audit['train_images'])}")
    print(f"Source test files: {len(audit['test_images'])}")
    print(f"Train source groups: {len(audit['groups'])}")
    print(f"Group sizes: {audit['group_sizes']}")
    print(f"Dimensions: {audit['dimensions']}")
    print(f"Source boxes: {audit['source_boxes']}")
    print(f"Exact duplicate image sets: {len(audit['exact_duplicate_sets'])}")
    print(f"Source validation issues: {len(audit['issues'])}")
    for issue in audit["issues"][:20]:
        print(f"  - {issue}")
    print(f"Source split counts: {plan['source_counts']}")
    print(f"Output file counts: {plan['file_counts']}")
    print(f"Boundaries: {plan['boundaries']}")
    print(f"Group leakage: {plan['group_leakage'] or 'none'}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Sao chép dữ liệu theo kế hoạch đã kiểm tra")
    args = parser.parse_args()

    audit = inspect_source()
    plan = build_plan(audit)
    print_report(audit, plan)
    if audit["issues"] or plan["group_leakage"]:
        print("DỪNG: source audit chưa đạt.")
        return 1
    if not args.execute:
        print("DRY RUN: chưa sao chép dữ liệu. Dùng --execute sau khi audit đạt.")
        return 0
    manifest_path = execute_plan(plan, audit)
    print(f"Đã sao chép dataset và tạo manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
