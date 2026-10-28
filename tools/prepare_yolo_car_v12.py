"""Tao ban Car.v12 tach theo nhom nguon, khong thay doi dataset Roboflow goc."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import re
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
CLASS_NAMES = ["Car"]
SEED = 42


@dataclass(frozen=True)
class Record:
    image: Path
    label: Path
    original_split: str
    source_key: str
    group_id: str
    boxes: int


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_source_key(filename: str) -> str:
    """Bo hash Roboflow, duoi anh va cac bien the augmentation o cuoi ten."""
    value = Path(filename).stem.split(".rf.", 1)[0]
    previous = None
    while value != previous:
        previous = value
        value = re.sub(r"_(?:jpg|jpeg|png|bmp|webp)$", "", value, flags=re.I)
        value = re.sub(r"(?:[_-](?:aug|augment)(?:[_-]?\d+)?)$", "", value, flags=re.I)
    return value.lower()


def screenshot_session_map(keys: set[str], gap_seconds: int = 300) -> dict[str, str]:
    parsed: list[tuple[datetime, str]] = []
    pattern = re.compile(r"^screenshot-(\d{4}-\d{2}-\d{2})-(\d{6})$", re.I)
    for key in keys:
        match = pattern.fullmatch(key)
        if match:
            parsed.append((datetime.strptime("".join(match.groups()), "%Y-%m-%d%H%M%S"), key))

    mapping: dict[str, str] = {}
    session = 0
    previous: datetime | None = None
    previous_day = None
    for captured_at, key in sorted(parsed):
        if (
            previous is None
            or captured_at.date() != previous_day
            or (captured_at - previous).total_seconds() > gap_seconds
        ):
            session += 1
        mapping[key] = f"screenshot-session:{captured_at.date()}:{session:03d}"
        previous, previous_day = captured_at, captured_at.date()
    return mapping


def build_group_map(keys: set[str]) -> dict[str, str]:
    screenshot_sessions = screenshot_session_map(keys)
    groups: dict[str, str] = {}
    for key in keys:
        if key in screenshot_sessions:
            groups[key] = screenshot_sessions[key]
        elif match := re.match(r"^(\d{4}-\d{2}-\d{2})_\d{2}_\d{2}_\d{2}$", key):
            groups[key] = f"timestamp-day:{match.group(1)}"
        elif key.startswith("harlem-"):
            groups[key] = "video:harlem"
        elif match := re.match(r"^(dji_\d+)-\d+$", key):
            groups[key] = f"video:{match.group(1)}"
        else:
            groups[key] = f"source:{key}"
    return groups


def parse_label(path: Path) -> int:
    count = 0
    for line_number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 5:
            raise ValueError(f"{path}:{line_number}: nhan YOLO can dung 5 truong")
        try:
            class_value = float(parts[0])
            values = [float(value) for value in parts[1:]]
        except ValueError as exc:
            raise ValueError(f"{path}:{line_number}: nhan khong phai so") from exc
        if not class_value.is_integer() or int(class_value) != 0:
            raise ValueError(f"{path}:{line_number}: class id phai la 0")
        if not all(math.isfinite(value) for value in values):
            raise ValueError(f"{path}:{line_number}: toa do khong huu han")
        x, y, width, height = values
        if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < width <= 1 and 0 < height <= 1):
            raise ValueError(f"{path}:{line_number}: toa do/size ngoai khoang YOLO")
        if x - width / 2 < -1e-9 or x + width / 2 > 1 + 1e-9:
            raise ValueError(f"{path}:{line_number}: bounding box vuot bien ngang")
        if y - height / 2 < -1e-9 or y + height / 2 > 1 + 1e-9:
            raise ValueError(f"{path}:{line_number}: bounding box vuot bien doc")
        count += 1
    return count


def discover_source(project_root: Path) -> Path:
    candidates = sorted(
        path
        for path in (project_root / "dataset").glob("*yolo*/Car.v12-ver3.yolov8")
        if (path / "data.yaml").is_file()
    )
    if len(candidates) != 1:
        raise ValueError(f"Can dung mot dataset Car.v12 goc; tim thay {len(candidates)}: {candidates}")
    return candidates[0]


def scan_source(source_root: Path) -> list[Record]:
    provisional: list[tuple[Path, Path, str, str, int]] = []
    all_keys: set[str] = set()
    seen_names: set[str] = set()
    for split in ("train", "valid"):
        image_dir = source_root / split / "images"
        label_dir = source_root / split / "labels"
        images = sorted(
            path for path in image_dir.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        )
        labels = {path.stem: path for path in label_dir.glob("*.txt")}
        image_stems = {path.stem for path in images}
        missing = sorted(path.name for path in images if path.stem not in labels)
        orphan = sorted(path.name for stem, path in labels.items() if stem not in image_stems)
        if missing or orphan:
            raise ValueError(f"{split}: thieu nhan={missing[:5]}, nhan mo coi={orphan[:5]}")
        for image in images:
            if image.name in seen_names:
                raise ValueError(f"Trung ten file giua cac tap: {image.name}")
            seen_names.add(image.name)
            with Image.open(image) as opened:
                opened.verify()
            key = normalize_source_key(image.name)
            all_keys.add(key)
            provisional.append((image, labels[image.stem], split, key, parse_label(labels[image.stem])))

    group_map = build_group_map(all_keys)
    return [
        Record(image, label, split, key, group_map[key], boxes)
        for image, label, split, key, boxes in provisional
    ]


def select_validation_groups(
    groups: dict[str, list[Record]], target_images: int, target_boxes: int
) -> set[str]:
    """Tim split nhom can bang ca so anh va annotation, co seed de tai lap."""
    ordered = sorted(groups)
    metrics = {
        key: (len(groups[key]), sum(record.boxes for record in groups[key]))
        for key in ordered
    }
    generator = random.Random(SEED)
    best_rank = None
    best_selection: set[str] | None = None
    # Dataset chi co 80 nhom; tim ngau nhien co seed cho ket qua on dinh va du nhanh.
    for _ in range(200_000):
        selected = {key for key in ordered if generator.random() < 0.20}
        if not selected:
            continue
        images = sum(metrics[key][0] for key in selected)
        boxes = sum(metrics[key][1] for key in selected)
        score = ((images - target_images) / target_images) ** 2
        score += ((boxes - target_boxes) / target_boxes) ** 2
        rank = (score, abs(images - target_images), abs(boxes - target_boxes))
        if best_rank is None or rank < best_rank:
            best_rank, best_selection = rank, selected
    if not best_selection:
        raise ValueError("Khong tim duoc tap validation theo nhom.")
    return best_selection


def dhash(path: Path) -> int:
    with Image.open(path) as image:
        pixels = list(image.convert("L").resize((9, 8)).getdata())
    value = 0
    for row in range(8):
        for column in range(8):
            value = (value << 1) | (pixels[row * 9 + column] > pixels[row * 9 + column + 1])
    return value


def prepare(source_root: Path, output_root: Path, validation_fraction: float) -> dict:
    if output_root.exists() and any(output_root.iterdir()):
        raise ValueError(f"Thu muc dich da co du lieu; khong ghi de: {output_root}")
    records = scan_source(source_root)
    groups: dict[str, list[Record]] = defaultdict(list)
    for record in records:
        groups[record.group_id].append(record)

    target = round(len(records) * validation_fraction)
    target_boxes = round(sum(record.boxes for record in records) * validation_fraction)
    validation_groups = select_validation_groups(groups, target, target_boxes)
    assignments = {
        record.image.name: "valid" if record.group_id in validation_groups else "train"
        for record in records
    }

    for split in ("train", "valid"):
        (output_root / split / "images").mkdir(parents=True, exist_ok=True)
        (output_root / split / "labels").mkdir(parents=True, exist_ok=True)

    manifest_rows = []
    split_counts = Counter()
    split_boxes = Counter()
    for record in sorted(records, key=lambda item: item.image.name):
        split = assignments[record.image.name]
        image_target = output_root / split / "images" / record.image.name
        label_target = output_root / split / "labels" / record.label.name
        shutil.copy2(record.image, image_target)
        shutil.copy2(record.label, label_target)
        split_counts[split] += 1
        split_boxes[split] += record.boxes
        manifest_rows.append({
            "split": split,
            "group_id": record.group_id,
            "source_key": record.source_key,
            "original_split": record.original_split,
            "image": record.image.name,
            "label": record.label.name,
            "boxes": record.boxes,
            "image_sha256": sha256_file(record.image),
            "label_sha256": sha256_file(record.label),
        })

    data_yaml = "\n".join([
        "train: train/images",
        "val: valid/images",
        "",
        "nc: 1",
        "names:",
        "  0: Car",
        "",
    ])
    (output_root / "data.yaml").write_text(data_yaml, encoding="utf-8")
    with (output_root / "split_manifest.csv").open("w", encoding="utf-8", newline="") as target_file:
        writer = csv.DictWriter(target_file, fieldnames=list(manifest_rows[0]))
        writer.writeheader()
        writer.writerows(manifest_rows)

    group_splits: dict[str, set[str]] = defaultdict(set)
    for row in manifest_rows:
        group_splits[row["group_id"]].add(row["split"])
    leaked_groups = sorted(key for key, splits in group_splits.items() if len(splits) > 1)

    train_records = [record for record in records if assignments[record.image.name] == "train"]
    valid_records = [record for record in records if assignments[record.image.name] == "valid"]
    train_hashes = [(record, dhash(record.image)) for record in train_records]
    valid_hashes = [(record, dhash(record.image)) for record in valid_records]
    near_candidates = []
    for train_record, train_hash in train_hashes:
        for valid_record, valid_hash in valid_hashes:
            distance = (train_hash ^ valid_hash).bit_count()
            if distance <= 2:
                near_candidates.append({
                    "hamming_distance": distance,
                    "train_image": train_record.image.name,
                    "train_group": train_record.group_id,
                    "valid_image": valid_record.image.name,
                    "valid_group": valid_record.group_id,
                })
    near_candidates.sort(key=lambda row: (row["hamming_distance"], row["train_image"], row["valid_image"]))
    with (output_root / "near_duplicate_candidates.csv").open("w", encoding="utf-8", newline="") as target_file:
        fields = ["hamming_distance", "train_image", "train_group", "valid_image", "valid_group"]
        writer = csv.DictWriter(target_file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(near_candidates)

    summary = {
        "source": str(source_root.resolve()),
        "output": str(output_root.resolve()),
        "seed": SEED,
        "validation_fraction_requested": validation_fraction,
        "classes": CLASS_NAMES,
        "groups": len(groups),
        "train_images": split_counts["train"],
        "train_labels": split_counts["train"],
        "train_boxes": split_boxes["train"],
        "valid_images": split_counts["valid"],
        "valid_labels": split_counts["valid"],
        "valid_boxes": split_boxes["valid"],
        "group_leakage": leaked_groups,
        "near_duplicate_candidates_cross_split": len(near_candidates),
        "test_set_created": False,
    }
    (output_root / "split_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Chia Car.v12 theo nhom nguon cho YOLOv8")
    parser.add_argument("--source", type=Path, help="Thu muc Car.v12 goc; bo trong de tu tim")
    parser.add_argument("--output", type=Path, default=Path("dataset/parking_yolo_car_v12_grouped"))
    parser.add_argument("--validation-fraction", type=float, default=0.20)
    args = parser.parse_args()
    if not 0.05 <= args.validation_fraction <= 0.40:
        raise SystemExit("validation-fraction phai trong khoang 0.05-0.40")
    project_root = Path(__file__).resolve().parents[1]
    source = args.source.resolve() if args.source else discover_source(project_root)
    output = args.output if args.output.is_absolute() else project_root / args.output
    try:
        summary = prepare(source, output.resolve(), args.validation_fraction)
    except (ValueError, OSError) as exc:
        raise SystemExit(str(exc)) from None
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
