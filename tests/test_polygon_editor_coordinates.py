"""Coordinate conversion tests for the centered polygon-editor viewport."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from annotate_demo_parking_polygons import (  # noqa: E402
    HUD_HEIGHT,
    calculate_viewport,
    display_to_source_point,
    source_to_display_point,
)


class PolygonEditorCoordinateTests(unittest.TestCase):
    def test_4k_frame_fits_below_hud_without_cropping(self) -> None:
        layout = calculate_viewport((3840, 2160), (1536, 816))
        canvas_width, canvas_height = layout["canvas_size"]
        display_width, display_height = layout["display_size"]
        offset_x, offset_y = layout["offset"]

        self.assertGreaterEqual(offset_y, HUD_HEIGHT)
        self.assertGreaterEqual(offset_x, 0)
        self.assertLessEqual(offset_x + display_width, canvas_width)
        self.assertLessEqual(offset_y + display_height, canvas_height)
        self.assertAlmostEqual(display_width / display_height, 16 / 9, places=2)

    def test_horizontal_and_vertical_coordinates_round_trip(self) -> None:
        source_size = (3840, 2160)
        layout = calculate_viewport(source_size, (1536, 816))
        points = [(0, 0), (3839, 0), (0, 2159), (3839, 2159), (1920, 1080)]

        for source_point in points:
            with self.subTest(source_point=source_point):
                display_point = source_to_display_point(
                    source_point,
                    offset=layout["offset"],
                    display_size=layout["display_size"],
                    source_size=source_size,
                )
                recovered = display_to_source_point(
                    display_point,
                    offset=layout["offset"],
                    display_size=layout["display_size"],
                    source_size=source_size,
                )
                self.assertIsNotNone(recovered)
                assert recovered is not None
                self.assertLessEqual(abs(recovered[0] - source_point[0]), 2)
                self.assertLessEqual(abs(recovered[1] - source_point[1]), 2)

    def test_letterbox_and_hud_clicks_are_rejected(self) -> None:
        source_size = (3840, 2160)
        layout = calculate_viewport(source_size, (1536, 816))
        offset_x, offset_y = layout["offset"]
        display_width, display_height = layout["display_size"]

        outside_points = [
            (offset_x - 1, offset_y),
            (offset_x, offset_y - 1),
            (offset_x + display_width, offset_y),
            (offset_x, offset_y + display_height),
            (10, 10),
        ]
        for display_point in outside_points:
            with self.subTest(display_point=display_point):
                self.assertIsNone(
                    display_to_source_point(
                        display_point,
                        offset=layout["offset"],
                        display_size=layout["display_size"],
                        source_size=source_size,
                    )
                )

    def test_optional_window_cap_adds_vertical_centering_offset(self) -> None:
        layout = calculate_viewport((3840, 1000), (1536, 816), (1000, 700))
        _offset_x, offset_y = layout["offset"]
        self.assertGreater(offset_y, HUD_HEIGHT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
