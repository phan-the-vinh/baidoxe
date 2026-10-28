"""Train YOLOv8n for 27 epochs from the team's smoke-test checkpoint."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import torch
from ultralytics import YOLO


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_YAML = PROJECT_ROOT / "dataset" / "parking_yolo_car_v12_grouped" / "data.yaml"
INPUT_CHECKPOINT = (
    PROJECT_ROOT
    / "runs"
    / "detect"
    / "runs"
    / "yolo_car_v12"
    / "smoke_01"
    / "weights"
    / "best.pt"
)
OUTPUT_PROJECT = (
    PROJECT_ROOT / "runs" / "detect" / "runs" / "yolo_car_v12"
)
OUTPUT_NAME = "train_27_from_smoke01_best"
OUTPUT_DIR = OUTPUT_PROJECT / OUTPUT_NAME
EPOCHS = 27


def require_file(path: Path, description: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Không tìm thấy {description}: {path}")


def main() -> None:
    require_file(DATA_YAML, "data.yaml")
    require_file(INPUT_CHECKPOINT, "checkpoint đầu vào")
    if OUTPUT_DIR.exists():
        raise FileExistsError(
            f"Thư mục kết quả đã tồn tại, dừng để tránh ghi đè: {OUTPUT_DIR}"
        )
    if not torch.cuda.is_available():
        raise RuntimeError("PyTorch không nhận diện được CUDA.")

    checkpoint = torch.load(INPUT_CHECKPOINT, map_location="cpu", weights_only=False)
    checkpoint_epoch = checkpoint.get("epoch")
    optimizer_saved = checkpoint.get("optimizer") is not None
    del checkpoint

    print("=== XÁC NHẬN TRƯỚC KHI HUẤN LUYỆN ===", flush=True)
    print(f"Python executable: {sys.executable}", flush=True)
    print(f"CUDA available: {torch.cuda.is_available()}", flush=True)
    print(f"GPU: {torch.cuda.get_device_name(0)}", flush=True)
    print(f"Dataset: {DATA_YAML}", flush=True)
    print(f"Checkpoint đầu vào: {INPUT_CHECKPOINT}", flush=True)
    print(f"Checkpoint epoch metadata: {checkpoint_epoch}", flush=True)
    print(f"Checkpoint còn optimizer state: {optimizer_saved}", flush=True)
    print(f"Thư mục kết quả dự kiến: {OUTPUT_DIR}", flush=True)
    print(f"Số epoch dự kiến của lượt mới: {EPOCHS}", flush=True)
    print("Phương thức: khởi tạo từ trọng số best.pt, không resume optimizer", flush=True)

    model = YOLO(str(INPUT_CHECKPOINT))
    started = time.perf_counter()
    model.train(
        data=str(DATA_YAML),
        epochs=EPOCHS,
        imgsz=512,
        batch=4,
        device=0,
        workers=0,
        cache=False,
        project=str(OUTPUT_PROJECT),
        name=OUTPUT_NAME,
        exist_ok=False,
        resume=False,
        pretrained=False,
        val=True,
        save=True,
        plots=True,
        amp=True,
        seed=0,
        deterministic=True,
        verbose=True,
    )
    elapsed = time.perf_counter() - started
    print(f"TRAINING_ELAPSED_SECONDS={elapsed:.3f}", flush=True)
    print(f"TRAINING_OUTPUT_DIR={OUTPUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
