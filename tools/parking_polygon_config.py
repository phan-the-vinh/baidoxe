"""Validation and JSON persistence for the standalone parking polygon demo."""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path
from typing import Any, Iterable, Sequence


SCHEMA_VERSION = 1
POLYGON_TYPES = {"parking_space", "exclusion_zone"}


def polygon_area(points: Sequence[Sequence[float]]) -> float:
    """Return the absolute polygon area using the shoelace formula."""
    if len(points) < 3:
        return 0.0
    signed_twice_area = 0.0
    for index, point in enumerate(points):
        next_point = points[(index + 1) % len(points)]
        signed_twice_area += point[0] * next_point[1]
        signed_twice_area -= next_point[0] * point[1]
    return abs(signed_twice_area) / 2.0


def _orientation(a: Sequence[float], b: Sequence[float], c: Sequence[float]) -> int:
    value = (b[1] - a[1]) * (c[0] - b[0]) - (b[0] - a[0]) * (
        c[1] - b[1]
    )
    if math.isclose(value, 0.0, abs_tol=1e-9):
        return 0
    return 1 if value > 0 else 2


def _on_segment(a: Sequence[float], b: Sequence[float], c: Sequence[float]) -> bool:
    return (
        min(a[0], c[0]) <= b[0] <= max(a[0], c[0])
        and min(a[1], c[1]) <= b[1] <= max(a[1], c[1])
    )


def _segments_intersect(
    a: Sequence[float],
    b: Sequence[float],
    c: Sequence[float],
    d: Sequence[float],
) -> bool:
    o1 = _orientation(a, b, c)
    o2 = _orientation(a, b, d)
    o3 = _orientation(c, d, a)
    o4 = _orientation(c, d, b)
    if o1 != o2 and o3 != o4:
        return True
    return (
        (o1 == 0 and _on_segment(a, c, b))
        or (o2 == 0 and _on_segment(a, d, b))
        or (o3 == 0 and _on_segment(c, a, d))
        or (o4 == 0 and _on_segment(c, b, d))
    )


def is_simple_polygon(points: Sequence[Sequence[float]]) -> bool:
    """Return False when non-neighbouring polygon edges intersect."""
    count = len(points)
    if count < 3:
        return False
    for first in range(count):
        a = points[first]
        b = points[(first + 1) % count]
        for second in range(first + 1, count):
            if second in {first, (first + 1) % count}:
                continue
            if first == 0 and second == count - 1:
                continue
            c = points[second]
            d = points[(second + 1) % count]
            if _segments_intersect(a, b, c, d):
                return False
    return True


def scale_points(
    points: Iterable[Sequence[float]],
    source_size: tuple[int, int],
    target_size: tuple[int, int],
) -> list[list[int]]:
    """Scale points from one (width, height) coordinate space to another."""
    source_width, source_height = source_size
    target_width, target_height = target_size
    if min(source_width, source_height, target_width, target_height) <= 0:
        raise ValueError("Frame dimensions must be positive")
    scale_x = target_width / source_width
    scale_y = target_height / source_height
    return [
        [int(round(float(x) * scale_x)), int(round(float(y) * scale_y))]
        for x, y in points
    ]


def validate_config(data: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a polygon configuration loaded from JSON."""
    if not isinstance(data, dict):
        raise ValueError("Polygon config must be a JSON object")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Unsupported schema_version: {data.get('schema_version')!r}")

    coordinate_space = data.get("coordinate_space")
    if not isinstance(coordinate_space, dict):
        raise ValueError("coordinate_space is required")
    width = coordinate_space.get("width")
    height = coordinate_space.get("height")
    if not isinstance(width, int) or not isinstance(height, int) or min(width, height) <= 0:
        raise ValueError("coordinate_space width and height must be positive integers")

    polygons = data.get("polygons")
    if not isinstance(polygons, list):
        raise ValueError("polygons must be a list")

    normalized_polygons: list[dict[str, Any]] = []
    identifiers: set[str] = set()
    for index, polygon in enumerate(polygons):
        if not isinstance(polygon, dict):
            raise ValueError(f"polygons[{index}] must be an object")
        identifier = polygon.get("id")
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError(f"polygons[{index}].id must be a non-empty string")
        identifier = identifier.strip()
        identity_key = identifier.casefold()
        if identity_key in identifiers:
            raise ValueError(f"Duplicate polygon id: {identifier}")
        identifiers.add(identity_key)

        polygon_type = polygon.get("type")
        if polygon_type not in POLYGON_TYPES:
            raise ValueError(
                f"Polygon {identifier} has invalid type {polygon_type!r}; "
                f"expected one of {sorted(POLYGON_TYPES)}"
            )
        raw_points = polygon.get("points")
        if not isinstance(raw_points, list) or len(raw_points) < 3:
            raise ValueError(f"Polygon {identifier} needs at least three points")

        points: list[list[int]] = []
        for point_index, point in enumerate(raw_points):
            if (
                not isinstance(point, (list, tuple))
                or len(point) != 2
                or not all(isinstance(value, (int, float)) for value in point)
            ):
                raise ValueError(
                    f"Polygon {identifier} point {point_index} must be [x, y]"
                )
            x, y = int(round(point[0])), int(round(point[1]))
            if not 0 <= x < width or not 0 <= y < height:
                raise ValueError(
                    f"Polygon {identifier} point {[x, y]} is outside {width}x{height}"
                )
            points.append([x, y])

        if len({tuple(point) for point in points}) < 3:
            raise ValueError(f"Polygon {identifier} needs three distinct points")
        if polygon_area(points) < 1.0:
            raise ValueError(f"Polygon {identifier} has zero area")
        if not is_simple_polygon(points):
            raise ValueError(f"Polygon {identifier} is self-intersecting")
        normalized_polygons.append(
            {"id": identifier, "type": polygon_type, "points": points}
        )

    source = data.get("source", {})
    if not isinstance(source, dict):
        raise ValueError("source must be an object")
    return {
        "schema_version": SCHEMA_VERSION,
        "configured": bool(
            any(item["type"] == "parking_space" for item in normalized_polygons)
        ),
        "source": source,
        "coordinate_space": {"width": width, "height": height},
        "polygons": normalized_polygons,
    }


def load_config(path: Path) -> dict[str, Any]:
    """Read and validate a UTF-8 polygon JSON file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    return validate_config(data)


def save_config(
    path: Path,
    *,
    width: int,
    height: int,
    polygons: list[dict[str, Any]],
    source_video: str,
    reference_frame: str,
    timestamp_seconds: float,
) -> dict[str, Any]:
    """Validate and atomically save a polygon config, retaining one backup."""
    data = validate_config(
        {
            "schema_version": SCHEMA_VERSION,
            "source": {
                "video": source_video,
                "reference_frame": reference_frame,
                "timestamp_seconds": round(float(timestamp_seconds), 3),
            },
            "coordinate_space": {"width": width, "height": height},
            "polygons": polygons,
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if path.exists():
        backup = path.with_suffix(".previous.json")
        shutil.copy2(path, backup)
    temporary.replace(path)
    return data
