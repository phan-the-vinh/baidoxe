"""Run the trained YOLOv8n car detector on a video without modifying inputs."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import torch
from ultralytics import YOLO


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VIDEO = PROJECT_ROOT / "demo_videos" / "0922(1).mp4"
DEFAULT_WEIGHTS = (
    PROJECT_ROOT
    / "runs"
    / "detect"
    / "runs"
    / "yolo_car_v12"
    / "train_27_from_smoke01_best"
    / "weights"
    / "best.pt"
)
DEFAULT_OUTPUT_ROOT = (
    PROJECT_ROOT / "runs" / "detect" / "video_tests" / "yolo_car_v12_best"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_VIDEO)
    parser.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--seconds", type=float, default=None)
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--iou", type=float, default=0.70)
    parser.add_argument("--imgsz", type=int, default=512)
    parser.add_argument("--device", default="0")
    parser.add_argument("--output-width", type=int, default=1920)
    parser.add_argument("--sample-count", type=int, default=6)
    parser.add_argument(
        "--compare-conf",
        type=float,
        nargs="*",
        default=(0.10, 0.25, 0.40),
    )
    return parser.parse_args()


def require_file(path: Path, description: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Không tìm thấy {description}: {resolved}")
    return resolved


def extract_detections(result) -> list[dict[str, float | int]]:
    detections: list[dict[str, float | int]] = []
    if result.boxes is None:
        return detections
    for xyxy, confidence, class_id in zip(
        result.boxes.xyxy.cpu().numpy(),
        result.boxes.conf.cpu().numpy(),
        result.boxes.cls.cpu().numpy(),
    ):
        x1, y1, x2, y2 = xyxy.tolist()
        detections.append(
            {
                "class_id": int(class_id),
                "confidence": float(confidence),
                "x1": float(x1),
                "y1": float(y1),
                "x2": float(x2),
                "y2": float(y2),
            }
        )
    return detections


def draw_detections(
    frame: np.ndarray,
    detections: list[dict[str, float | int]],
    names: dict[int, str],
) -> np.ndarray:
    annotated = frame.copy()
    thickness = max(1, round(min(frame.shape[:2]) / 1080))
    font_scale = max(0.38, min(frame.shape[:2]) / 2450)
    text_thickness = 1
    for detection in detections:
        class_id = int(detection["class_id"])
        confidence = float(detection["confidence"])
        x1 = int(round(float(detection["x1"])))
        y1 = int(round(float(detection["y1"])))
        x2 = int(round(float(detection["x2"])))
        y2 = int(round(float(detection["y2"])))
        label = f"{names.get(class_id, str(class_id))} {confidence:.2f}"
        # Cyan-blue stays distinct from the red/green/white slot-state palette.
        color = (255, 190, 30)
        cv2.rectangle(
            annotated, (x1, y1), (x2, y2), color, thickness, cv2.LINE_AA
        )
        (text_width, text_height), baseline = cv2.getTextSize(
            label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, text_thickness
        )
        text_origin = (x1 + 2, max(text_height + 2, y1 - baseline - 3))
        # A narrow dark outline keeps text legible without a solid label block.
        cv2.putText(
            annotated,
            label,
            text_origin,
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            (15, 20, 20),
            text_thickness + 2,
            cv2.LINE_AA,
        )
        cv2.putText(
            annotated,
            label,
            text_origin,
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            color,
            text_thickness,
            cv2.LINE_AA,
        )
    return annotated


def main() -> None:
    args = parse_args()
    input_video = require_file(args.input, "video đầu vào")
    weights = require_file(args.weights, "checkpoint best.pt")
    run_dir = (args.output_root / args.run_name).resolve()
    if run_dir.exists():
        raise FileExistsError(f"Dừng để tránh ghi đè thư mục đã có: {run_dir}")
    if args.device != "cpu" and not torch.cuda.is_available():
        raise RuntimeError("Đã yêu cầu GPU nhưng torch.cuda.is_available() là False")

    capture = cv2.VideoCapture(str(input_video))
    if not capture.isOpened():
        raise RuntimeError(f"OpenCV không mở được video: {input_video}")

    source_fps = float(capture.get(cv2.CAP_PROP_FPS))
    source_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    source_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    source_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if source_fps <= 0 or source_frames <= 0 or source_width <= 0 or source_height <= 0:
        capture.release()
        raise RuntimeError("Metadata video không hợp lệ")

    max_frames = source_frames
    if args.seconds is not None:
        max_frames = min(source_frames, max(1, round(args.seconds * source_fps)))
    output_width = args.output_width if args.output_width > 0 else source_width
    output_height = round(source_height * output_width / source_width)
    if output_height % 2:
        output_height += 1

    run_dir.mkdir(parents=True)
    sample_dir = run_dir / "samples"
    comparison_dir = run_dir / "confidence_comparison"
    sample_dir.mkdir()
    comparison_dir.mkdir()
    output_video = run_dir / "annotated.mp4"
    detections_csv = run_dir / "detections.csv"
    summary_json = run_dir / "summary.json"

    writer = cv2.VideoWriter(
        str(output_video),
        cv2.VideoWriter_fourcc(*"mp4v"),
        source_fps,
        (output_width, output_height),
    )
    if not writer.isOpened():
        capture.release()
        raise RuntimeError(f"Không tạo được video đầu ra: {output_video}")

    print("=== VIDEO TEST PREFLIGHT ===", flush=True)
    print(f"Python executable: {sys.executable}", flush=True)
    print(f"CUDA available: {torch.cuda.is_available()}", flush=True)
    print(
        f"GPU: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU'}",
        flush=True,
    )
    print(f"Input video: {input_video}", flush=True)
    print(f"Weights: {weights}", flush=True)
    print(
        f"Source: {source_width}x{source_height}, {source_fps:.3f} FPS, "
        f"{source_frames} frames, {source_frames / source_fps:.3f} seconds",
        flush=True,
    )
    print(
        f"Processing: {max_frames} frames -> {output_width}x{output_height}, "
        f"conf={args.conf}, imgsz={args.imgsz}, device={args.device}",
        flush=True,
    )
    print(f"Output directory: {run_dir}", flush=True)

    model = YOLO(str(weights))
    names = {int(key): str(value) for key, value in model.names.items()}
    if names.get(0) != "Car":
        raise RuntimeError(f"Checkpoint không có lớp 0=Car: {names}")

    sample_indices = set(
        int(index)
        for index in np.linspace(0, max_frames - 1, min(args.sample_count, max_frames))
    )
    comparison_thresholds = sorted(set(float(value) for value in args.compare_conf))
    comparison_counts: dict[str, dict[str, int]] = {}
    per_frame_counts: list[int] = []
    inference_seconds = 0.0
    processed_frames = 0
    started = time.perf_counter()

    with detections_csv.open("w", newline="", encoding="utf-8") as csv_file:
        fieldnames = [
            "frame_index",
            "timestamp_seconds",
            "class_id",
            "class_name",
            "confidence",
            "x1",
            "y1",
            "x2",
            "y2",
        ]
        csv_writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        csv_writer.writeheader()

        try:
            for frame_index in range(max_frames):
                readable, frame = capture.read()
                if not readable or frame is None:
                    print(f"Cảnh báo: dừng sớm tại frame {frame_index}", flush=True)
                    break
                if (source_width, source_height) != (output_width, output_height):
                    frame = cv2.resize(
                        frame,
                        (output_width, output_height),
                        interpolation=cv2.INTER_AREA,
                    )

                inference_started = time.perf_counter()
                result = model.predict(
                    source=frame,
                    conf=args.conf,
                    iou=args.iou,
                    imgsz=args.imgsz,
                    device=args.device,
                    classes=[0],
                    verbose=False,
                )[0]
                inference_seconds += time.perf_counter() - inference_started
                detections = extract_detections(result)
                annotated = draw_detections(frame, detections, names)
                writer.write(annotated)

                timestamp = frame_index / source_fps
                per_frame_counts.append(len(detections))
                for detection in detections:
                    csv_writer.writerow(
                        {
                            "frame_index": frame_index,
                            "timestamp_seconds": f"{timestamp:.6f}",
                            "class_id": detection["class_id"],
                            "class_name": names[int(detection["class_id"])],
                            "confidence": f"{float(detection['confidence']):.6f}",
                            "x1": f"{float(detection['x1']):.3f}",
                            "y1": f"{float(detection['y1']):.3f}",
                            "x2": f"{float(detection['x2']):.3f}",
                            "y2": f"{float(detection['y2']):.3f}",
                        }
                    )

                if frame_index in sample_indices:
                    stem = f"frame_{frame_index:06d}_t{timestamp:06.2f}s"
                    cv2.imwrite(str(sample_dir / f"{stem}_original.jpg"), frame)
                    cv2.imwrite(str(sample_dir / f"{stem}_annotated.jpg"), annotated)
                    low_confidence = min(comparison_thresholds, default=args.conf)
                    comparison_result = model.predict(
                        source=frame,
                        conf=low_confidence,
                        iou=args.iou,
                        imgsz=args.imgsz,
                        device=args.device,
                        classes=[0],
                        verbose=False,
                    )[0]
                    comparison_detections = extract_detections(comparison_result)
                    sample_comparison: dict[str, int] = {}
                    for threshold in comparison_thresholds:
                        selected = [
                            item
                            for item in comparison_detections
                            if float(item["confidence"]) >= threshold
                        ]
                        threshold_key = f"{threshold:.2f}"
                        sample_comparison[threshold_key] = len(selected)
                        compared = draw_detections(frame, selected, names)
                        cv2.imwrite(
                            str(comparison_dir / f"{stem}_conf{threshold:.2f}.jpg"),
                            compared,
                        )
                    comparison_counts[f"{timestamp:.2f}"] = sample_comparison

                processed_frames += 1
                if processed_frames == 1 or processed_frames % 100 == 0:
                    elapsed = time.perf_counter() - started
                    print(
                        f"Processed {processed_frames}/{max_frames} frames "
                        f"({processed_frames / elapsed:.2f} FPS)",
                        flush=True,
                    )
        finally:
            capture.release()
            writer.release()

    elapsed = time.perf_counter() - started
    summary = {
        "input_video": str(input_video),
        "weights": str(weights),
        "output_video": str(output_video),
        "source": {
            "width": source_width,
            "height": source_height,
            "fps": source_fps,
            "frames": source_frames,
            "duration_seconds": source_frames / source_fps,
        },
        "processing": {
            "width": output_width,
            "height": output_height,
            "frames": processed_frames,
            "duration_seconds": processed_frames / source_fps,
            "elapsed_seconds": elapsed,
            "overall_fps": processed_frames / elapsed if elapsed else 0.0,
            "inference_fps": processed_frames / inference_seconds if inference_seconds else 0.0,
            "device": args.device,
            "imgsz": args.imgsz,
            "confidence": args.conf,
            "iou": args.iou,
        },
        "detections": {
            "total": int(sum(per_frame_counts)),
            "frames_with_detections": int(sum(count > 0 for count in per_frame_counts)),
            "mean_per_frame": float(np.mean(per_frame_counts)) if per_frame_counts else 0.0,
            "min_per_frame": int(min(per_frame_counts)) if per_frame_counts else 0,
            "max_per_frame": int(max(per_frame_counts)) if per_frame_counts else 0,
        },
        "confidence_comparison_counts": comparison_counts,
        "sample_frames": sorted(str(path) for path in sample_dir.glob("*_annotated.jpg")),
    }
    summary_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print("=== VIDEO TEST COMPLETE ===", flush=True)
    print(json.dumps(summary, indent=2, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
