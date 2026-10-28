"""Tạo dataset_summary.json cho License Plate YOLO v1."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from check_license_plate_dataset import analyze_dataset, default_dataset_root


def build_summary(dataset_root: Path) -> dict[str, object]:
    result = analyze_dataset(dataset_root)
    splits = result["splits"]
    return {
        "classes": {"0": "license_plate"},
        "train_images": splits["train"]["images"],
        "val_images": splits["val"]["images"],
        "test_images": splits["test"]["images"],
        "train_labels": splits["train"]["labels"],
        "val_labels": splits["val"]["labels"],
        "test_labels": splits["test"]["labels"],
        "total_boxes": result["total_boxes"],
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=default_dataset_root(),
        help="Đường dẫn dataset cần tạo thống kê",
    )
    args = parser.parse_args()
    dataset_root = args.dataset.resolve()
    output_path = dataset_root / "dataset_summary.json"
    summary = build_summary(dataset_root)
    output_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Đã tạo: {output_path}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
