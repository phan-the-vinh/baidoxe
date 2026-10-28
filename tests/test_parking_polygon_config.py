"""Tests for the standalone parking polygon JSON format."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from parking_polygon_config import (  # noqa: E402
    load_config,
    save_config,
    scale_points,
    validate_config,
)


class ParkingPolygonConfigTests(unittest.TestCase):
    def test_save_and_load_round_trip(self) -> None:
        polygons = [
            {
                "id": "D01",
                "type": "parking_space",
                "points": [[100, 100], [300, 100], [300, 250], [100, 250]],
            },
            {
                "id": "X01",
                "type": "exclusion_zone",
                "points": [[500, 500], [700, 500], [650, 650]],
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "polygons.json"
            saved = save_config(
                path,
                width=3840,
                height=2160,
                polygons=polygons,
                source_video="demo_videos/0922(1).mp4",
                reference_frame="config/demo_parking/reference.jpg",
                timestamp_seconds=5.0,
            )
            self.assertEqual(load_config(path), saved)
            self.assertTrue(saved["configured"])
            self.assertEqual(saved["coordinate_space"], {"width": 3840, "height": 2160})

            polygons.append(
                {
                    "id": "D02",
                    "type": "parking_space",
                    "points": [[800, 500], [950, 500], [950, 650], [800, 650]],
                }
            )
            save_config(
                path,
                width=3840,
                height=2160,
                polygons=polygons,
                source_video="demo_videos/0922(1).mp4",
                reference_frame="config/demo_parking/reference.jpg",
                timestamp_seconds=5.0,
            )
            self.assertTrue(path.with_suffix(".previous.json").is_file())

    def test_scale_points_to_processing_resolution(self) -> None:
        points = [[0, 0], [3840, 2160], [1920, 1080]]
        self.assertEqual(
            scale_points(points, (3840, 2160), (1920, 1080)),
            [[0, 0], [1920, 1080], [960, 540]],
        )

    def test_duplicate_ids_are_rejected_case_insensitively(self) -> None:
        config = {
            "schema_version": 1,
            "source": {},
            "coordinate_space": {"width": 1000, "height": 500},
            "polygons": [
                {
                    "id": "D01",
                    "type": "parking_space",
                    "points": [[0, 0], [100, 0], [100, 100]],
                },
                {
                    "id": "d01",
                    "type": "parking_space",
                    "points": [[200, 0], [300, 0], [300, 100]],
                },
            ],
        }
        with self.assertRaisesRegex(ValueError, "Duplicate polygon id"):
            validate_config(config)

    def test_out_of_bounds_and_self_intersection_are_rejected(self) -> None:
        base = {
            "schema_version": 1,
            "source": {},
            "coordinate_space": {"width": 100, "height": 100},
            "polygons": [
                {
                    "id": "D01",
                    "type": "parking_space",
                    "points": [[0, 0], [99, 0], [101, 99]],
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, "outside"):
            validate_config(base)

        base["polygons"][0]["points"] = [[0, 0], [90, 90], [0, 90], [90, 0]]
        with self.assertRaisesRegex(ValueError, "zero area|self-intersecting"):
            validate_config(base)

    def test_invalid_json_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Invalid JSON"):
                load_config(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
