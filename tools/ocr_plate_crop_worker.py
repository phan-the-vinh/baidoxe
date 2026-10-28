"""OCR exactly one YOLO-provided plate crop with OpenCV + Tesseract.

The worker runs in the ``parking_ai`` environment, which owns pytesseract and
the project's TESSERACT_CMD configuration. It never detects a plate and never
invents or repairs OCR characters.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import cv2
import numpy as np
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from app.services.plate_ocr import (  # noqa: E402
    _read_variant,
    _tesseract_command,
    normalize_ocr_plate,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the existing Tesseract engine on one exact YOLO plate crop."
    )
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, default=None)
    return parser.parse_args()


def _write_debug(path: Path | None, name: str, image: np.ndarray) -> None:
    if path is None:
        return
    path.mkdir(parents=True, exist_ok=True)
    target = path / name
    if not cv2.imwrite(str(target), image):
        raise OSError(f"Cannot write OCR debug image: {target}")


def _order_points(points: np.ndarray) -> np.ndarray:
    points = points.astype(np.float32)
    ordered = np.zeros((4, 2), dtype=np.float32)
    sums = points.sum(axis=1)
    differences = np.diff(points, axis=1).reshape(-1)
    ordered[0] = points[np.argmin(sums)]
    ordered[2] = points[np.argmax(sums)]
    ordered[1] = points[np.argmin(differences)]
    ordered[3] = points[np.argmax(differences)]
    return ordered


def _rectify_plate(image: np.ndarray) -> tuple[np.ndarray, dict]:
    """Rectify only when a strong, convex four-corner contour is present."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    enhanced = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    blurred = cv2.bilateralFilter(enhanced, 7, 55, 55)
    edges = cv2.Canny(blurred, 45, 160)
    edges = cv2.morphologyEx(
        edges,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)),
        iterations=2,
    )
    otsu_mask = cv2.threshold(
        blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )[1]
    otsu_mask = cv2.morphologyEx(
        otsu_mask,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)),
        iterations=1,
    )
    image_area = float(image.shape[0] * image.shape[1])
    candidates: list[tuple[float, np.ndarray, float]] = []
    contours: list[np.ndarray] = []
    for mask in (edges, otsu_mask):
        found, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        contours.extend(found)
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:60]:
        for outline in (contour, cv2.convexHull(contour)):
            perimeter = cv2.arcLength(outline, True)
            if perimeter <= 0:
                continue
            for epsilon in (0.02, 0.03, 0.04, 0.05):
                polygon = cv2.approxPolyDP(outline, epsilon * perimeter, True)
                if len(polygon) != 4 or not cv2.isContourConvex(polygon):
                    continue
                area_ratio = cv2.contourArea(polygon) / max(1.0, image_area)
                if not 0.30 <= area_ratio <= 0.98:
                    continue
                ordered = _order_points(polygon.reshape(4, 2))
                top_width = np.linalg.norm(ordered[1] - ordered[0])
                bottom_width = np.linalg.norm(ordered[2] - ordered[3])
                left_height = np.linalg.norm(ordered[3] - ordered[0])
                right_height = np.linalg.norm(ordered[2] - ordered[1])
                width = max(top_width, bottom_width)
                height = max(left_height, right_height)
                if width < 45 or height < 25:
                    continue
                aspect = width / max(1.0, height)
                if not 0.65 <= aspect <= 6.5:
                    continue
                opposite_consistency = min(top_width, bottom_width) / max(top_width, bottom_width)
                opposite_consistency *= min(left_height, right_height) / max(left_height, right_height)
                if opposite_consistency < 0.42:
                    continue
                score = 0.75 * area_ratio + 0.25 * opposite_consistency
                candidates.append((score, ordered, area_ratio))

    if not candidates:
        return image.copy(), {
            "applied": False,
            "reason": "no_confident_four_corner_contour",
            "area_ratio": None,
        }

    _, points, area_ratio = max(candidates, key=lambda item: item[0])
    target_width = max(
        1,
        round(max(np.linalg.norm(points[1] - points[0]), np.linalg.norm(points[2] - points[3]))),
    )
    target_height = max(
        1,
        round(max(np.linalg.norm(points[3] - points[0]), np.linalg.norm(points[2] - points[1]))),
    )
    destination = np.array(
        [[0, 0], [target_width - 1, 0], [target_width - 1, target_height - 1], [0, target_height - 1]],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(points, destination)
    rectified = cv2.warpPerspective(
        image,
        matrix,
        (target_width, target_height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
    return rectified, {
        "applied": True,
        "reason": "confident_four_corner_contour",
        "area_ratio": round(float(area_ratio), 4),
        "corners": [[round(float(x), 2), round(float(y), 2)] for x, y in points],
    }


def _preprocess_variants(image: np.ndarray) -> tuple[list[dict], dict[str, np.ndarray]]:
    variants: list[dict] = []
    representative: dict[str, np.ndarray] = {}
    gray_source = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    for factor in (2, 3, 4):
        gray = cv2.resize(
            gray_source,
            None,
            fx=factor,
            fy=factor,
            interpolation=cv2.INTER_CUBIC,
        )
        clahe = cv2.createCLAHE(clipLimit=2.4, tileGridSize=(8, 8)).apply(gray)
        denoised = cv2.bilateralFilter(clahe, 7, 45, 45)
        otsu = cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
        adaptive = cv2.adaptiveThreshold(
            denoised,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            31,
            9,
        )
        values = {
            f"upscale_{factor}x_gray": gray,
            f"upscale_{factor}x_clahe_denoise": denoised,
            f"upscale_{factor}x_otsu": otsu,
            f"upscale_{factor}x_adaptive": adaptive,
            f"upscale_{factor}x_otsu_inverted": cv2.bitwise_not(otsu),
        }
        variants.extend({"name": name, "image": value} for name, value in values.items())
        if factor == 3:
            representative = {
                "upscale": denoised,
                "otsu": otsu,
                "adaptive": adaptive,
            }
    return variants, representative


def _line_layout(image: np.ndarray) -> tuple[str, float | None, dict]:
    """Classify layout and return a split ratio using observed components only."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    enlarged = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    enhanced = cv2.createCLAHE(clipLimit=2.4, tileGridSize=(8, 8)).apply(enlarged)
    binary = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    height, width = binary.shape
    margin_x, margin_y = round(width * 0.04), round(height * 0.04)
    binary[:margin_y, :] = 0
    binary[max(0, height - margin_y):, :] = 0
    binary[:, :margin_x] = 0
    binary[:, max(0, width - margin_x):] = 0

    count, _, stats, centroids = cv2.connectedComponentsWithStats(binary, 8)
    components: list[dict] = []
    for index in range(1, count):
        x, y, component_width, component_height, area = stats[index]
        if not (0.06 * height <= component_height <= 0.72 * height):
            continue
        if not (0.008 * width <= component_width <= 0.48 * width):
            continue
        if area < 0.0008 * width * height:
            continue
        components.append(
            {
                "center_y": float(centroids[index][1]),
                "top": int(y),
                "bottom": int(y + component_height),
            }
        )

    split_ratio: float | None = None
    evidence = "aspect_ratio"
    if len(components) >= 4:
        centers = sorted(item["center_y"] for item in components)
        gaps = [(centers[index + 1] - centers[index], index) for index in range(len(centers) - 1)]
        largest_gap, split_index = max(gaps, default=(0.0, 0))
        upper_count, lower_count = split_index + 1, len(centers) - split_index - 1
        if largest_gap >= 0.11 * height and upper_count >= 2 and lower_count >= 2:
            split_ratio = ((centers[split_index] + centers[split_index + 1]) / 2) / height
            evidence = "connected_component_rows"

    aspect = image.shape[1] / max(1, image.shape[0])
    if split_ratio is None and aspect <= 1.55:
        projection = binary.sum(axis=1).astype(np.float32)
        kernel_size = max(3, round(height * 0.025))
        projection = np.convolve(projection, np.ones(kernel_size) / kernel_size, mode="same")
        start, end = round(height * 0.35), round(height * 0.65)
        if end > start:
            valley = int(start + np.argmin(projection[start:end]))
            split_ratio = valley / height
            evidence = "compact_aspect_projection_valley"

    layout = "two_line" if split_ratio is not None else "one_line"
    return layout, split_ratio, {
        "layout": layout,
        "aspect_ratio": round(float(aspect), 4),
        "split_ratio": round(float(split_ratio), 4) if split_ratio is not None else None,
        "evidence": evidence,
        "component_count": len(components),
    }


def _alphanumeric(raw_text: str | None) -> str:
    return "".join(re.findall(r"[A-Z0-9]", str(raw_text or "").upper()))


def _candidate_rank(raw_text: str | None, confidence: float | None, normalized: str | None) -> float:
    clean = _alphanumeric(raw_text)
    length_score = max(0.0, 1.0 - abs(len(clean) - 8.0) / 8.0) if clean else 0.0
    observed_structure = 0.0
    if re.match(r"^\d{2}", clean):
        observed_structure += 0.35
    if re.search(r"[A-Z]", clean):
        observed_structure += 0.25
    if re.search(r"\d{4,6}$", clean):
        observed_structure += 0.40
    return round(
        0.55 * float(confidence or 0.0)
        + 0.20 * length_score
        + 0.15 * observed_structure
        + (0.45 if normalized else 0.0),
        6,
    )


def _read_candidate(image: np.ndarray, psm: int, preprocessing: str, mode: str) -> dict:
    raw_text, confidence = _read_variant(image, psm)
    normalized = normalize_ocr_plate(raw_text)
    return {
        "preprocessing": preprocessing,
        "mode": mode,
        "psm": str(psm),
        "raw_text": raw_text or None,
        "plate": normalized,
        "ocr_confidence": round(confidence, 4) if raw_text else None,
        "rank_score": _candidate_rank(raw_text, confidence if raw_text else None, normalized),
    }


def _best_line_candidate(candidates: list[dict], expected_length: tuple[int, int]) -> dict:
    minimum, maximum = expected_length

    def score(item: dict) -> tuple[float, float]:
        length = len(_alphanumeric(item["raw_text"]))
        length_fit = 1.0 if minimum <= length <= maximum else max(0.0, 1.0 - abs(length - minimum) / 6)
        return (0.75 * float(item["ocr_confidence"] or 0.0) + 0.25 * length_fit, length_fit)

    return max(candidates, key=score)


def recognize_exact_crop(image_path: Path | str | np.ndarray, output_dir: Path | None = None) -> dict:
    if isinstance(image_path, np.ndarray):
        image = image_path
    else:
        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise ValueError(f"Cannot read plate crop: {image_path}")

    import pytesseract

    command = _tesseract_command()
    pytesseract.pytesseract.tesseract_cmd = command
    _write_debug(output_dir, "plate_original.jpg", image)

    rectified, rectification = _rectify_plate(image)
    _write_debug(output_dir, "plate_rectified.jpg", rectified)
    variants, representative = _preprocess_variants(rectified)
    _write_debug(output_dir, "plate_upscale.jpg", representative["upscale"])
    _write_debug(output_dir, "plate_otsu.jpg", representative["otsu"])
    _write_debug(output_dir, "plate_adaptive.jpg", representative["adaptive"])

    layout, split_ratio, layout_evidence = _line_layout(rectified)
    candidates: list[dict] = []
    if layout == "one_line":
        for variant in variants:
            for psm in (7, 8):
                candidates.append(
                    _read_candidate(variant["image"], psm, variant["name"], "whole_one_line")
                )
    else:
        representative_height = representative["upscale"].shape[0]
        representative_split = max(1, min(representative_height - 1, round(representative_height * split_ratio)))
        _write_debug(output_dir, "plate_top.jpg", representative["upscale"][:representative_split])
        _write_debug(output_dir, "plate_bottom.jpg", representative["upscale"][representative_split:])
        for variant in variants:
            variant_image = variant["image"]
            split = max(1, min(variant_image.shape[0] - 1, round(variant_image.shape[0] * split_ratio)))
            top, bottom = variant_image[:split], variant_image[split:]
            candidates.append(
                _read_candidate(variant_image, 6, variant["name"], "whole_two_line")
            )
            top_candidates = [
                _read_candidate(top, psm, variant["name"], "top_line") for psm in (7, 8)
            ]
            bottom_candidates = [
                _read_candidate(bottom, psm, variant["name"], "bottom_line") for psm in (7, 8)
            ]
            best_top = _best_line_candidate(top_candidates, (2, 5))
            best_bottom = _best_line_candidate(bottom_candidates, (4, 6))
            raw_parts = [part for part in (best_top["raw_text"], best_bottom["raw_text"]) if part]
            raw_text = " ".join(raw_parts)
            character_counts = [len(_alphanumeric(best_top["raw_text"])), len(_alphanumeric(best_bottom["raw_text"]))]
            total_characters = sum(character_counts)
            if total_characters:
                confidence = sum(
                    count * float(candidate["ocr_confidence"] or 0.0)
                    for count, candidate in zip(character_counts, (best_top, best_bottom))
                ) / total_characters
            else:
                confidence = None
            normalized = normalize_ocr_plate(raw_text)
            candidates.append(
                {
                    "preprocessing": variant["name"],
                    "mode": "split_two_line",
                    "psm": f"{best_top['psm']}+{best_bottom['psm']}",
                    "raw_text": raw_text or None,
                    "plate": normalized,
                    "ocr_confidence": round(confidence, 4) if confidence is not None else None,
                    "rank_score": _candidate_rank(raw_text, confidence, normalized),
                    "line_parts": {"top": best_top, "bottom": best_bottom},
                }
            )

    candidates.sort(key=lambda item: item["rank_score"], reverse=True)
    best = candidates[0] if candidates else {
        "raw_text": None,
        "plate": None,
        "ocr_confidence": None,
        "rank_score": 0.0,
    }
    return {
        "raw_text": best.get("raw_text"),
        "plate": best.get("plate"),
        "ocr_confidence": best.get("ocr_confidence"),
        "needs_confirmation": True,
        "layout": layout,
        "layout_evidence": layout_evidence,
        "rectification": rectification,
        "selected_candidate": best,
        "ocr_candidates": candidates,
        "ocr_candidate_count": len(candidates),
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    try:
        output_dir = args.output_dir.expanduser().resolve() if args.output_dir else None
        payload = {"ok": True, **recognize_exact_crop(args.image.resolve(), output_dir)}
        exit_code = 0
    except Exception as exc:  # Parent receives a structured failure, never a fake plate.
        payload = {
            "ok": False,
            "raw_text": None,
            "plate": None,
            "ocr_confidence": None,
            "needs_confirmation": True,
            "error": f"{type(exc).__name__}: {exc}",
        }
        exit_code = 1
    print(json.dumps(payload, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
