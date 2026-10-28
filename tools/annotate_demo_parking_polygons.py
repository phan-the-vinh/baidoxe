"""Interactive OpenCV editor for standalone demo parking-space polygons."""

from __future__ import annotations

import argparse
import ctypes
import re
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from parking_polygon_config import load_config, polygon_area, save_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VIDEO = PROJECT_ROOT / "demo_videos" / "0922(1).mp4"
DEFAULT_OUTPUT = PROJECT_ROOT / "config" / "demo_parking" / "0922_1_polygons.json"
DEFAULT_REFERENCE = (
    PROJECT_ROOT / "config" / "demo_parking" / "0922_1_reference_t05s.jpg"
)
WINDOW_NAME = "Demo parking polygon editor"
PARKING_SPACE = "parking_space"
EXCLUSION_ZONE = "exclusion_zone"
HUD_HEIGHT = 116
WINDOW_MARGIN_X = 48
WINDOW_MARGIN_Y = 80


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, default=DEFAULT_VIDEO)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--timestamp", type=float, default=5.0)
    parser.add_argument(
        "--display-width",
        type=int,
        default=None,
        help="Optional maximum window width; defaults to the screen work area.",
    )
    parser.add_argument(
        "--display-height",
        type=int,
        default=None,
        help="Optional maximum window height; defaults to the screen work area.",
    )
    parser.add_argument(
        "--extract-only",
        action="store_true",
        help="Extract the reference frame and exit without opening a window.",
    )
    return parser.parse_args()


def relative_to_project(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(resolved)


def screen_work_area() -> tuple[int, int]:
    """Return the usable primary-screen size, excluding the Windows taskbar."""
    if sys.platform == "win32":
        try:
            from ctypes import wintypes

            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except (AttributeError, OSError):
                pass
            rectangle = wintypes.RECT()
            ok = ctypes.windll.user32.SystemParametersInfoW(
                0x0030, 0, ctypes.byref(rectangle), 0
            )
            if ok:
                width = int(rectangle.right - rectangle.left)
                height = int(rectangle.bottom - rectangle.top)
                if width > 0 and height > 0:
                    return width, height
        except (AttributeError, OSError, ValueError):
            pass
    return 1600, 900


def calculate_viewport(
    source_size: tuple[int, int],
    work_area: tuple[int, int],
    maximum_window_size: tuple[int | None, int | None] = (None, None),
) -> dict[str, Any]:
    """Calculate a centered, aspect-preserving image viewport below the HUD."""
    source_width, source_height = source_size
    work_width, work_height = work_area
    maximum_width, maximum_height = maximum_window_size
    if min(source_width, source_height, work_width, work_height) <= 0:
        raise ValueError("Source and screen dimensions must be positive")

    canvas_width = max(320, work_width - WINDOW_MARGIN_X)
    canvas_height = max(HUD_HEIGHT + 180, work_height - WINDOW_MARGIN_Y)
    if maximum_width is not None:
        canvas_width = min(canvas_width, maximum_width)
    if maximum_height is not None:
        canvas_height = min(canvas_height, maximum_height)
    if canvas_width <= 0 or canvas_height <= HUD_HEIGHT:
        raise ValueError("The requested display size is too small")

    image_area_height = canvas_height - HUD_HEIGHT
    scale = min(
        1.0,
        canvas_width / source_width,
        image_area_height / source_height,
    )
    display_width = max(1, int(round(source_width * scale)))
    display_height = max(1, int(round(source_height * scale)))
    offset_x = (canvas_width - display_width) // 2
    offset_y = HUD_HEIGHT + (image_area_height - display_height) // 2
    return {
        "canvas_size": (canvas_width, canvas_height),
        "display_size": (display_width, display_height),
        "offset": (offset_x, offset_y),
        "scale": scale,
    }


def source_to_display_point(
    point: list[int] | tuple[int, int],
    *,
    offset: tuple[int, int],
    display_size: tuple[int, int],
    source_size: tuple[int, int],
) -> tuple[int, int]:
    """Map an original-frame point into the centered display viewport."""
    source_width, source_height = source_size
    display_width, display_height = display_size
    mapped_x = round(point[0] * (display_width - 1) / max(1, source_width - 1))
    mapped_y = round(point[1] * (display_height - 1) / max(1, source_height - 1))
    return (
        int(mapped_x) + offset[0],
        int(mapped_y) + offset[1],
    )


def display_to_source_point(
    point: tuple[int, int],
    *,
    offset: tuple[int, int],
    display_size: tuple[int, int],
    source_size: tuple[int, int],
) -> list[int] | None:
    """Map a viewport point to the source frame, rejecting margin/HUD clicks."""
    x, y = point
    offset_x, offset_y = offset
    display_width, display_height = display_size
    if not (
        offset_x <= x < offset_x + display_width
        and offset_y <= y < offset_y + display_height
    ):
        return None
    source_width, source_height = source_size
    source_x = int(
        round((x - offset_x) * (source_width - 1) / max(1, display_width - 1))
    )
    source_y = int(
        round((y - offset_y) * (source_height - 1) / max(1, display_height - 1))
    )
    return [
        min(source_width - 1, max(0, source_x)),
        min(source_height - 1, max(0, source_y)),
    ]


def read_reference_frame(
    video_path: Path, reference_path: Path, timestamp_seconds: float
) -> tuple[np.ndarray, float]:
    video_path = video_path.resolve()
    if not video_path.is_file():
        raise FileNotFoundError(f"Video not found: {video_path}")
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"OpenCV cannot open video: {video_path}")
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = frame_count / fps if fps > 0 else 0.0
        frame_interval = 1.0 / fps if fps > 0 else 0.0
        actual_timestamp = min(
            max(0.0, timestamp_seconds), max(0.0, duration - frame_interval)
        )
        capture.set(cv2.CAP_PROP_POS_MSEC, actual_timestamp * 1000.0)
        ok, frame = capture.read()
        if not ok or frame is None:
            raise RuntimeError(f"Cannot read video frame at {actual_timestamp:.3f}s")
    finally:
        capture.release()

    reference_path = reference_path.resolve()
    reference_path.parent.mkdir(parents=True, exist_ok=True)
    if not reference_path.exists():
        if not cv2.imwrite(str(reference_path), frame):
            raise RuntimeError(f"Cannot write reference frame: {reference_path}")
    return frame, actual_timestamp


def suggest_identifier(polygons: list[dict[str, Any]], kind: str) -> str:
    prefix = "D" if kind == PARKING_SPACE else "X"
    used = {str(item["id"]).casefold() for item in polygons}
    number = 1
    while f"{prefix}{number:02d}".casefold() in used:
        number += 1
    return f"{prefix}{number:02d}"


def point_inside_polygon(point: tuple[int, int], points: list[list[int]]) -> bool:
    contour = np.asarray(points, dtype=np.float32)
    return cv2.pointPolygonTest(contour, point, False) >= 0


class PolygonEditor:
    def __init__(
        self,
        frame: np.ndarray,
        *,
        video_path: Path,
        reference_path: Path,
        output_path: Path,
        timestamp_seconds: float,
        display_width: int | None,
        display_height: int | None,
    ) -> None:
        self.frame = frame
        self.height, self.width = frame.shape[:2]
        self.video_path = video_path.resolve()
        self.reference_path = reference_path.resolve()
        self.output_path = output_path.resolve()
        self.timestamp_seconds = timestamp_seconds
        work_area = screen_work_area()
        layout = calculate_viewport(
            (self.width, self.height),
            work_area,
            (display_width, display_height),
        )
        self.canvas_size = layout["canvas_size"]
        self.display_size = layout["display_size"]
        self.image_offset = layout["offset"]
        self.scale = layout["scale"]
        self.polygons: list[dict[str, Any]] = []
        self.current_points: list[list[int]] = []
        self.current_kind = PARKING_SPACE
        self.mode = "draw"
        self.selected_index: int | None = None
        self.drag_vertex_index: int | None = None
        self.id_buffer: str | None = None
        self.pending_points: list[list[int]] | None = None
        self.pending_kind: str | None = None
        self.redraw_original: tuple[int, dict[str, Any]] | None = None
        self.last_deleted: tuple[int, dict[str, Any]] | None = None
        self.dirty = False
        self.status = "Left click: add points. Enter: finish polygon."
        self.confirm_action: str | None = None
        self._load_if_present()
        print(
            "Display layout: "
            f"work_area={work_area[0]}x{work_area[1]}, "
            f"window={self.canvas_size[0]}x{self.canvas_size[1]}, "
            f"image={self.display_size[0]}x{self.display_size[1]}, "
            f"offset={self.image_offset}, scale={self.scale:.4f}"
        )

    def _load_if_present(self) -> None:
        if not self.output_path.exists():
            return
        config = load_config(self.output_path)
        space = config["coordinate_space"]
        if (space["width"], space["height"]) != (self.width, self.height):
            raise ValueError(
                "Existing JSON coordinate space does not match the reference frame: "
                f"{space['width']}x{space['height']} vs {self.width}x{self.height}"
            )
        self.polygons = config["polygons"]
        self.status = f"Loaded {len(self.polygons)} polygons from JSON."

    def display_to_source(self, x: int, y: int) -> list[int] | None:
        return display_to_source_point(
            (x, y),
            offset=self.image_offset,
            display_size=self.display_size,
            source_size=(self.width, self.height),
        )

    def source_to_display(self, point: list[int]) -> tuple[int, int]:
        return source_to_display_point(
            point,
            offset=self.image_offset,
            display_size=self.display_size,
            source_size=(self.width, self.height),
        )

    def _vertex_hit(self, point: list[int]) -> tuple[int, int] | None:
        radius = max(12.0, 16.0 / self.scale)
        best: tuple[float, int, int] | None = None
        candidates = (
            range(len(self.polygons))
            if self.selected_index is None
            else [self.selected_index]
        )
        for polygon_index in candidates:
            for vertex_index, vertex in enumerate(self.polygons[polygon_index]["points"]):
                distance = float(np.linalg.norm(np.asarray(vertex) - np.asarray(point)))
                if distance <= radius and (best is None or distance < best[0]):
                    best = (distance, polygon_index, vertex_index)
        return None if best is None else (best[1], best[2])

    def on_mouse(self, event: int, x: int, y: int, flags: int, _param: Any) -> None:
        if self.id_buffer is not None or self.confirm_action is not None:
            return
        point = self.display_to_source(x, y)
        if point is None:
            if event == cv2.EVENT_LBUTTONUP:
                self.drag_vertex_index = None
            elif event == cv2.EVENT_LBUTTONDOWN:
                self.status = "Click inside the displayed image, not the margin or HUD."
            return
        if self.mode == "draw":
            if event == cv2.EVENT_LBUTTONDOWN:
                self.current_points.append(point)
                self.status = f"Current polygon: {len(self.current_points)} points."
            elif event == cv2.EVENT_RBUTTONDOWN and self.current_points:
                self.current_points.pop()
                self.status = "Removed the last point."
            return

        if event == cv2.EVENT_LBUTTONDOWN:
            hit = self._vertex_hit(point)
            if hit is not None:
                self.selected_index, self.drag_vertex_index = hit
                self.status = f"Editing vertex of {self.polygons[hit[0]]['id']}."
                return
            self.selected_index = None
            for index in range(len(self.polygons) - 1, -1, -1):
                if point_inside_polygon(tuple(point), self.polygons[index]["points"]):
                    self.selected_index = index
                    self.status = f"Selected {self.polygons[index]['id']}."
                    break
        elif event == cv2.EVENT_MOUSEMOVE and self.drag_vertex_index is not None:
            assert self.selected_index is not None
            self.polygons[self.selected_index]["points"][self.drag_vertex_index] = point
            self.dirty = True
        elif event == cv2.EVENT_LBUTTONUP and self.drag_vertex_index is not None:
            self.drag_vertex_index = None
            self.status = "Vertex moved. Press S to validate and save."

    def _start_id_entry(self) -> None:
        if len(self.current_points) < 3:
            self.status = "A polygon needs at least three points."
            return
        if polygon_area(self.current_points) < 25.0:
            self.status = "Polygon area is too small. Clear and redraw it."
            return
        if self.redraw_original is not None:
            self.pending_points = [point[:] for point in self.current_points]
            self.pending_kind = self.redraw_original[1]["type"]
            self.id_buffer = str(self.redraw_original[1]["id"])
        else:
            self.pending_points = [point[:] for point in self.current_points]
            self.pending_kind = self.current_kind
            self.id_buffer = suggest_identifier(self.polygons, self.current_kind)
        self.status = "Type an ID, then Enter. Backspace edits; Esc cancels."

    def _commit_id_entry(self) -> None:
        assert self.id_buffer is not None
        identifier = self.id_buffer.strip()
        if not identifier or not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
            self.status = "ID may contain only letters, digits, underscore, or hyphen."
            return
        if any(item["id"].casefold() == identifier.casefold() for item in self.polygons):
            self.status = f"ID {identifier} already exists. Choose another ID."
            return
        assert self.pending_points is not None and self.pending_kind is not None
        new_polygon = {
            "id": identifier,
            "type": self.pending_kind,
            "points": self.pending_points,
        }
        if self.redraw_original is not None:
            index, _original = self.redraw_original
            self.polygons.insert(min(index, len(self.polygons)), new_polygon)
        else:
            self.polygons.append(new_polygon)
        self.current_points = []
        self.pending_points = None
        self.pending_kind = None
        self.id_buffer = None
        self.redraw_original = None
        self.selected_index = len(self.polygons) - 1
        self.dirty = True
        self.status = f"Added {identifier}. Press S to save JSON."

    def _cancel_current(self) -> None:
        if self.redraw_original is not None:
            index, polygon = self.redraw_original
            self.polygons.insert(min(index, len(self.polygons)), polygon)
            self.redraw_original = None
            self.status = f"Redraw cancelled; restored {polygon['id']}."
        else:
            self.status = "Current polygon cancelled."
        self.current_points = []
        self.pending_points = None
        self.pending_kind = None
        self.id_buffer = None

    def _save(self) -> None:
        if self.current_points or self.redraw_original is not None:
            raise ValueError("Finish or cancel the current polygon before saving")
        data = save_config(
            self.output_path,
            width=self.width,
            height=self.height,
            polygons=self.polygons,
            source_video=relative_to_project(self.video_path),
            reference_frame=relative_to_project(self.reference_path),
            timestamp_seconds=self.timestamp_seconds,
        )
        reread = load_config(self.output_path)
        if reread != data:
            raise RuntimeError("Saved JSON did not pass the read-back check")
        self.dirty = False
        self.status = f"Saved and verified {len(self.polygons)} polygons."
        print(f"Saved and verified: {self.output_path}")

    def _redraw_selected(self) -> None:
        if self.selected_index is None:
            self.status = "Select a polygon in Edit mode first."
            return
        polygon = self.polygons.pop(self.selected_index)
        self.redraw_original = (self.selected_index, polygon)
        self.current_kind = polygon["type"]
        self.current_points = []
        self.selected_index = None
        self.mode = "draw"
        self.status = f"Redrawing {polygon['id']}; click new vertices."

    def _delete_selected(self) -> None:
        if self.selected_index is None:
            self.status = "Select a polygon in Edit mode first."
            return
        polygon = self.polygons.pop(self.selected_index)
        self.last_deleted = (self.selected_index, polygon)
        self.selected_index = None
        self.dirty = True
        self.status = f"Deleted {polygon['id']}. Press U to restore it."

    def _restore_deleted(self) -> None:
        if self.last_deleted is None:
            self.status = "There is no deleted polygon to restore."
            return
        index, polygon = self.last_deleted
        self.polygons.insert(min(index, len(self.polygons)), polygon)
        self.last_deleted = None
        self.dirty = True
        self.status = f"Restored {polygon['id']}."

    def _reload(self) -> None:
        if not self.output_path.exists():
            self.status = "JSON does not exist yet; save first."
            return
        config = load_config(self.output_path)
        space = config["coordinate_space"]
        if (space["width"], space["height"]) != (self.width, self.height):
            raise ValueError(
                "JSON coordinate space does not match this reference frame"
            )
        self.polygons = config["polygons"]
        self.current_points = []
        self.selected_index = None
        self.redraw_original = None
        self.last_deleted = None
        self.dirty = False
        self.status = f"Reloaded {len(self.polygons)} polygons from JSON."

    def _draw_polygon(
        self, canvas: np.ndarray, polygon: dict[str, Any], selected: bool
    ) -> None:
        source_color = (
            (0, 215, 255) if polygon["type"] == PARKING_SPACE else (255, 80, 220)
        )
        color = (255, 255, 255) if selected else source_color
        points = np.asarray(
            [self.source_to_display(point) for point in polygon["points"]], np.int32
        )
        overlay = canvas.copy()
        cv2.fillPoly(overlay, [points], color)
        cv2.addWeighted(overlay, 0.16, canvas, 0.84, 0, canvas)
        cv2.polylines(canvas, [points], True, color, 3 if selected else 2)
        for point in points:
            cv2.circle(canvas, tuple(point), 5 if selected else 4, color, -1)
        centroid = tuple(np.mean(points, axis=0).astype(int))
        cv2.putText(
            canvas,
            polygon["id"],
            centroid,
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            color,
            2,
            cv2.LINE_AA,
        )

    def render(self) -> np.ndarray:
        canvas_width, canvas_height = self.canvas_size
        canvas = np.full((canvas_height, canvas_width, 3), (30, 32, 32), dtype=np.uint8)
        resized_frame = cv2.resize(
            self.frame, self.display_size, interpolation=cv2.INTER_AREA
        )
        offset_x, offset_y = self.image_offset
        display_width, display_height = self.display_size
        canvas[
            offset_y : offset_y + display_height,
            offset_x : offset_x + display_width,
        ] = resized_frame
        cv2.rectangle(
            canvas,
            (max(0, offset_x - 1), max(HUD_HEIGHT, offset_y - 1)),
            (offset_x + display_width, offset_y + display_height),
            (100, 100, 100),
            1,
        )
        for index, polygon in enumerate(self.polygons):
            self._draw_polygon(canvas, polygon, index == self.selected_index)

        if self.redraw_original is not None:
            old = self.redraw_original[1]
            preview = {**old, "id": f"{old['id']} (old)"}
            self._draw_polygon(canvas, preview, False)
        if self.current_points:
            current = np.asarray(
                [self.source_to_display(point) for point in self.current_points], np.int32
            )
            for point in current:
                cv2.circle(canvas, tuple(point), 5, (0, 140, 255), -1)
            if len(current) > 1:
                cv2.polylines(canvas, [current], False, (0, 140, 255), 2)

        parking_count = sum(item["type"] == PARKING_SPACE for item in self.polygons)
        exclusion_count = sum(item["type"] == EXCLUSION_ZONE for item in self.polygons)
        kind_label = "SLOT" if self.current_kind == PARKING_SPACE else "EXCLUSION"
        dirty_label = "UNSAVED" if self.dirty else "saved"
        lines = [
            f"Mode: {self.mode.upper()} / {kind_label} | slots={parking_count} exclusions={exclusion_count} | {dirty_label}",
            "Draw: left=point, right/U=undo, Enter=finish, C=clear | P=slot, X=exclusion",
            "E=edit/select, drag vertex, R=redraw, D=delete, U=restore | S=save, L=reload, Q=quit",
            self.status,
        ]
        if self.id_buffer is not None:
            lines[-1] = f"Polygon ID: {self.id_buffer}_  (Enter=accept, Esc=cancel)"
        if self.confirm_action == "quit":
            lines[-1] = "Unsaved changes: press Y to quit without saving, N to stay."
        elif self.confirm_action == "reload":
            lines[-1] = "Unsaved changes: press Y to reload/discard, N to stay."
        cv2.rectangle(canvas, (0, 0), (canvas.shape[1], HUD_HEIGHT), (20, 25, 25), -1)
        for index, line in enumerate(lines):
            cv2.putText(
                canvas,
                line,
                (12, 24 + index * 27),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.57,
                (245, 245, 245) if index < 3 else (80, 220, 255),
                1,
                cv2.LINE_AA,
            )
        return canvas

    def handle_key(self, key: int) -> bool:
        if key < 0:
            return True
        low_byte = key & 0xFF

        if self.confirm_action is not None:
            if low_byte in (ord("n"), 27):
                self.confirm_action = None
                self.status = "Action cancelled."
            elif low_byte == ord("y"):
                action = self.confirm_action
                self.confirm_action = None
                if action == "quit":
                    return False
                self._reload()
            return True

        if self.id_buffer is not None:
            if low_byte in (10, 13):
                self._commit_id_entry()
            elif low_byte == 27:
                self._cancel_current()
            elif low_byte in (8, 127):
                self.id_buffer = self.id_buffer[:-1]
            elif 32 <= low_byte <= 126:
                character = chr(low_byte)
                if re.fullmatch(r"[A-Za-z0-9_-]", character):
                    self.id_buffer += character
            return True

        if low_byte in (ord("q"), 27):
            if self.current_points or self.redraw_original is not None:
                self._cancel_current()
            elif self.dirty:
                self.confirm_action = "quit"
            else:
                return False
        elif low_byte == ord("s"):
            try:
                self._save()
            except (OSError, ValueError, RuntimeError) as exc:
                self.status = f"Save failed: {exc}"
                print(self.status)
        elif low_byte == ord("l"):
            if self.dirty:
                self.confirm_action = "reload"
            else:
                try:
                    self._reload()
                except (OSError, ValueError) as exc:
                    self.status = f"Reload failed: {exc}"
        elif low_byte == ord("p"):
            if self.current_points or self.redraw_original is not None:
                self._cancel_current()
            self.mode = "draw"
            self.current_kind = PARKING_SPACE
            self.selected_index = None
            self.status = "Drawing a parking-space polygon."
        elif low_byte == ord("x"):
            if self.current_points or self.redraw_original is not None:
                self._cancel_current()
            self.mode = "draw"
            self.current_kind = EXCLUSION_ZONE
            self.selected_index = None
            self.status = "Drawing an exclusion-zone polygon."
        elif low_byte == ord("e"):
            if self.current_points or self.redraw_original is not None:
                self._cancel_current()
            self.mode = "edit"
            self.status = "Edit mode: click a polygon or drag one of its vertices."
        elif low_byte in (10, 13) and self.mode == "draw":
            self._start_id_entry()
        elif low_byte == ord("c") and self.mode == "draw":
            self.current_points = []
            self.status = "Cleared current points."
        elif low_byte == ord("u"):
            if self.mode == "draw" and self.current_points:
                self.current_points.pop()
                self.status = "Removed the last point."
            else:
                self._restore_deleted()
        elif low_byte == ord("r") and self.mode == "edit":
            self._redraw_selected()
        elif low_byte in (ord("d"), 46) and self.mode == "edit":
            self._delete_selected()
        return True

    def run(self) -> None:
        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_AUTOSIZE)
        cv2.setMouseCallback(WINDOW_NAME, self.on_mouse)
        try:
            running = True
            while running:
                cv2.imshow(WINDOW_NAME, self.render())
                running = self.handle_key(cv2.waitKeyEx(20))
                if cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
                    break
        finally:
            cv2.destroyAllWindows()


def main() -> None:
    args = parse_args()
    if args.timestamp < 0:
        raise SystemExit("--timestamp must be non-negative")
    if args.display_width is not None and args.display_width <= 0:
        raise SystemExit("--display-width must be positive")
    if args.display_height is not None and args.display_height <= 0:
        raise SystemExit("--display-height must be positive")
    frame, actual_timestamp = read_reference_frame(
        args.video, args.reference, args.timestamp
    )
    print(
        f"Reference frame: {args.reference.resolve()} "
        f"({frame.shape[1]}x{frame.shape[0]}, t={actual_timestamp:.3f}s)"
    )
    print(f"Polygon JSON: {args.output.resolve()}")
    if args.extract_only:
        return
    editor = PolygonEditor(
        frame,
        video_path=args.video,
        reference_path=args.reference,
        output_path=args.output,
        timestamp_seconds=actual_timestamp,
        display_width=args.display_width,
        display_height=args.display_height,
    )
    editor.run()


if __name__ == "__main__":
    main()
