"""Local Tkinter reviewer for Plate Text Recognition v1.

The tool only updates dataset CSV files. It never writes business SQL and never
uses pseudo-label text as ground truth without an explicit human confirmation.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import string
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "dataset" / "plate_text_recognition_v1"
IMAGES = DATASET / "images"
LABELS = DATASET / "labels.csv"
QUEUE = DATASET / "review_queue.csv"
MANIFEST = DATASET / "crop_manifest.csv"
SUMMARY = DATASET / "dataset_summary.json"
LABEL_FIELDS = ["filename", "text", "status", "source", "layout"]
QUEUE_FIELDS = [
    "filename", "pseudo_text", "raw_text", "normalized_text",
    "ocr_confidence", "status", "source", "layout",
    "plate_confidence", "bbox", "ocr_error",
]
ALLOWED = set(string.ascii_uppercase + string.digits)
REMOVABLE_SEPARATORS = set(" .-_\t\r\n")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def canonicalize_human_text(value: str) -> str:
    upper = value.upper()
    invalid = sorted({character for character in upper if character not in ALLOWED and character not in REMOVABLE_SEPARATORS})
    if invalid:
        raise ValueError(f"Ký tự không hợp lệ: {' '.join(repr(character) for character in invalid)}")
    canonical = "".join(character for character in upper if character in ALLOWED)
    if not canonical:
        raise ValueError("Nhãn xác nhận không được để trống.")
    return canonical


def canonicalize_ocr_text(value: str) -> str:
    """Mirror pipeline normalization: remove non A-Z/0-9, never substitute."""
    return "".join(character for character in str(value or "").upper() if character in ALLOWED)


def update_summary(labels: list[dict[str, str]], queue: list[dict[str, str]]) -> None:
    payload = json.loads(SUMMARY.read_text(encoding="utf-8")) if SUMMARY.is_file() else {}
    payload.update(
        {
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "pseudo_label_non_null": sum(bool(row.get("pseudo_text", "").strip()) for row in queue),
            "pseudo_label_null": sum(not bool(row.get("pseudo_text", "").strip()) for row in queue),
            "need_review": sum(row.get("status") == "NEED_REVIEW" for row in labels),
            "verified": sum(row.get("status") == "VERIFIED" for row in labels),
            "train_eligible_verified_rows": sum(
                row.get("status") == "VERIFIED" and bool(row.get("text", "").strip()) for row in labels
            ),
            "layout_counts": dict(Counter(row.get("layout") or "unknown" for row in queue)),
            "ready_for_recognition_training": False,
        }
    )
    temporary = SUMMARY.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, SUMMARY)


def validate_dataset() -> dict:
    if not LABELS.is_file() or not QUEUE.is_file() or not MANIFEST.is_file():
        raise FileNotFoundError("Thiếu labels.csv, review_queue.csv hoặc crop_manifest.csv; hãy chạy pipeline trước.")
    labels = read_csv(LABELS)
    queue = read_csv(QUEUE)
    manifest = read_csv(MANIFEST)
    label_names = [row["filename"] for row in labels]
    queue_names = [row["filename"] for row in queue]
    manifest_names = [row["filename"] for row in manifest]
    issues = []
    if len(label_names) != len(set(label_names)):
        issues.append("labels.csv có filename trùng")
    if len(queue_names) != len(set(queue_names)):
        issues.append("review_queue.csv có filename trùng")
    if len(manifest_names) != len(set(manifest_names)):
        issues.append("crop_manifest.csv có filename trùng")
    if set(label_names) != set(queue_names) or set(label_names) != set(manifest_names):
        issues.append("labels.csv, review_queue.csv và crop_manifest.csv không cùng tập filename")

    import cv2

    queue_by_name = {row["filename"]: row for row in queue}
    for row in manifest:
        filename = row["filename"]
        crop_path = IMAGES / filename
        image = cv2.imread(str(crop_path), cv2.IMREAD_COLOR)
        if image is None:
            issues.append(f"crop không đọc được: {filename}")
            continue
        if image.shape[1] != int(row["crop_width"]) or image.shape[0] != int(row["crop_height"]):
            issues.append(f"kích thước crop sai manifest: {filename}")
        source_path = ROOT / row["source"]
        if not source_path.is_file():
            issues.append(f"thiếu ảnh nguồn: {row['source']}")
        source_width, source_height = int(row["source_width"]), int(row["source_height"])
        bbox = [int(row[key]) for key in ("bbox_x1", "bbox_y1", "bbox_x2", "bbox_y2")]
        crop_box = [int(row[key]) for key in ("crop_x1", "crop_y1", "crop_x2", "crop_y2")]
        if not (0 <= bbox[0] < bbox[2] <= source_width and 0 <= bbox[1] < bbox[3] <= source_height):
            issues.append(f"bbox ngoài ảnh nguồn: {filename}")
        if not (0 <= crop_box[0] < crop_box[2] <= source_width and 0 <= crop_box[1] < crop_box[3] <= source_height):
            issues.append(f"crop bbox ngoài ảnh nguồn: {filename}")
        confidence = float(row["plate_confidence"])
        if not 0 <= confidence <= 1:
            issues.append(f"plate confidence ngoài [0,1]: {filename}")

        queue_row = queue_by_name.get(filename)
        if queue_row is not None:
            expected = canonicalize_ocr_text(queue_row.get("raw_text", ""))
            if queue_row.get("normalized_text", "") != expected or queue_row.get("pseudo_text", "") != expected:
                issues.append(f"pseudo text không khớp raw canonical: {filename}")
            ocr_confidence = queue_row.get("ocr_confidence", "").strip()
            if ocr_confidence and not 0 <= float(ocr_confidence) <= 1:
                issues.append(f"OCR confidence ngoài [0,1]: {filename}")
    for row in labels:
        path = IMAGES / row["filename"]
        if not path.is_file():
            issues.append(f"thiếu crop {row['filename']}")
        if row.get("status") not in {"NEED_REVIEW", "VERIFIED"}:
            issues.append(f"status không hợp lệ {row['filename']}: {row.get('status')}")
        text = row.get("text", "")
        if row.get("status") == "VERIFIED" and (not text or any(character not in ALLOWED for character in text)):
            issues.append(f"nhãn VERIFIED không canonical: {row['filename']}")
        if row.get("status") == "NEED_REVIEW" and text:
            issues.append(f"NEED_REVIEW không được có ground-truth text: {row['filename']}")
        queue_row = queue_by_name.get(row["filename"])
        if queue_row is not None and queue_row.get("status") != row.get("status"):
            issues.append(f"status lệch giữa labels/queue: {row['filename']}")
    return {
        "labels": labels,
        "queue": queue,
        "summary": {
            "rows": len(labels),
            "need_review": sum(row.get("status") == "NEED_REVIEW" for row in labels),
            "verified": sum(row.get("status") == "VERIFIED" for row in labels),
            "issues": issues,
        },
    }


class ReviewApp:
    def __init__(self, root, labels: list[dict[str, str]], queue: list[dict[str, str]]):
        import tkinter as tk
        from tkinter import messagebox
        from PIL import Image, ImageTk

        self.tk = tk
        self.messagebox = messagebox
        self.Image = Image
        self.ImageTk = ImageTk
        self.root = root
        self.labels = labels
        self.queue = queue
        self.labels_by_name = {row["filename"]: row for row in labels}
        self.pending = [index for index, row in enumerate(queue) if row.get("status") == "NEED_REVIEW"]
        self.position = 0
        self.photo = None

        root.title("Review biển số - Plate Text Recognition v1")
        root.geometry("1000x720")
        root.minsize(780, 560)
        self.title_label = tk.Label(root, font=("Segoe UI", 13, "bold"))
        self.title_label.pack(pady=(12, 5))
        self.image_label = tk.Label(root, bg="#202020")
        self.image_label.pack(fill="both", expand=True, padx=16, pady=8)
        self.info_label = tk.Label(root, justify="left", anchor="w", font=("Consolas", 10))
        self.info_label.pack(fill="x", padx=18)

        entry_frame = tk.Frame(root)
        entry_frame.pack(fill="x", padx=18, pady=10)
        tk.Label(entry_frame, text="Nhãn đúng:", font=("Segoe UI", 11)).pack(side="left")
        self.entry = tk.Entry(entry_frame, font=("Consolas", 16))
        self.entry.pack(side="left", fill="x", expand=True, padx=10)
        self.entry.bind("<Return>", lambda _event: self.confirm())

        buttons = tk.Frame(root)
        buttons.pack(pady=(0, 14))
        tk.Button(buttons, text="Xác nhận VERIFIED", command=self.confirm, width=22).pack(side="left", padx=6)
        tk.Button(buttons, text="Bỏ qua", command=self.skip, width=12).pack(side="left", padx=6)
        tk.Button(buttons, text="Thoát", command=root.destroy, width=12).pack(side="left", padx=6)
        self.show_current()

    def show_current(self):
        if not self.pending or self.position >= len(self.pending):
            self.title_label.config(text="Đã duyệt hết hàng đợi hiện tại.")
            self.image_label.config(image="")
            self.info_label.config(text="")
            self.entry.delete(0, self.tk.END)
            return
        row = self.queue[self.pending[self.position]]
        image_path = IMAGES / row["filename"]
        image = self.Image.open(image_path).convert("RGB")
        image.thumbnail((920, 430), self.Image.Resampling.LANCZOS)
        self.photo = self.ImageTk.PhotoImage(image)
        self.image_label.config(image=self.photo)
        self.title_label.config(text=f"{self.position + 1}/{len(self.pending)} — {row['filename']}")
        self.info_label.config(
            text=(
                f"OCR đề xuất: {row.get('pseudo_text') or '<trống>'}\n"
                f"OCR raw: {row.get('raw_text') or '<trống>'}\n"
                f"Confidence OCR: {row.get('ocr_confidence') or '<không có>'}    "
                f"Layout: {row.get('layout') or '<không rõ>'}\n"
                f"Nguồn: {row.get('source')}"
            )
        )
        self.entry.delete(0, self.tk.END)
        self.entry.insert(0, row.get("pseudo_text", ""))
        self.entry.focus_set()
        self.entry.selection_range(0, self.tk.END)

    def persist(self):
        write_csv(LABELS, LABEL_FIELDS, self.labels)
        write_csv(QUEUE, QUEUE_FIELDS, self.queue)
        update_summary(self.labels, self.queue)

    def confirm(self):
        if not self.pending or self.position >= len(self.pending):
            return
        try:
            canonical = canonicalize_human_text(self.entry.get())
        except ValueError as exc:
            self.messagebox.showerror("Nhãn không hợp lệ", str(exc))
            return
        row = self.queue[self.pending[self.position]]
        label = self.labels_by_name[row["filename"]]
        label["text"] = canonical
        label["status"] = "VERIFIED"
        row["status"] = "VERIFIED"
        self.persist()
        self.position += 1
        self.show_current()

    def skip(self):
        if self.pending and self.position < len(self.pending):
            self.position += 1
            self.show_current()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    try:
        audit = validate_dataset()
        print(json.dumps(audit["summary"], ensure_ascii=False, indent=2))
        if audit["summary"]["issues"]:
            return 1
        if args.validate_only:
            return 0
        import tkinter as tk

        root = tk.Tk()
        ReviewApp(root, audit["labels"], audit["queue"])
        root.mainloop()
        return 0
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
