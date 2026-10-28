"""Create a Chrome-compatible H.264 copy of an existing annotated demo video.

This utility uses the Windows Media Foundation encoder already available through
OpenCV. It does not run inference or alter any rendered video content.
"""

import argparse
import struct
from pathlib import Path

import cv2


CONTAINER_BOXES = {b"moov", b"trak", b"mdia", b"minf", b"stbl"}


def fourcc_text(value):
    value = int(value)
    return "".join(chr((value >> (8 * index)) & 0xFF) for index in range(4))


def box_header(data, offset, limit):
    if offset + 8 > limit:
        raise RuntimeError("MP4 box header is truncated.")
    size, kind = struct.unpack_from(">I4s", data, offset)
    header_size = 8
    if size == 1:
        if offset + 16 > limit:
            raise RuntimeError("Extended MP4 box header is truncated.")
        size = struct.unpack_from(">Q", data, offset + 8)[0]
        header_size = 16
    elif size == 0:
        size = limit - offset
    if size < header_size or offset + size > limit:
        raise RuntimeError(f"Invalid MP4 box {kind!r} at byte {offset}.")
    return size, kind, header_size


def top_level_boxes(path):
    boxes = []
    file_size = path.stat().st_size
    with path.open("rb") as source:
        offset = 0
        while offset < file_size:
            source.seek(offset)
            header = source.read(8)
            if len(header) != 8:
                raise RuntimeError("Top-level MP4 box header is truncated.")
            size, kind = struct.unpack(">I4s", header)
            header_size = 8
            if size == 1:
                extended = source.read(8)
                if len(extended) != 8:
                    raise RuntimeError("Extended MP4 box header is truncated.")
                size = struct.unpack(">Q", extended)[0]
                header_size = 16
            elif size == 0:
                size = file_size - offset
            if size < header_size or offset + size > file_size:
                raise RuntimeError("Top-level MP4 box exceeds file size.")
            boxes.append((kind, offset, size, header_size))
            offset += size
    return boxes


def patch_chunk_offsets(moov_data, delta):
    patched = bytearray(moov_data)
    root_size, root_kind, root_header = box_header(patched, 0, len(patched))
    if root_kind != b"moov" or root_size != len(patched):
        raise RuntimeError("Expected one complete moov box.")
    tables = 0

    def walk(start, end):
        nonlocal tables
        position = start
        while position < end:
            size, kind, header_size = box_header(patched, position, end)
            content = position + header_size
            box_end = position + size
            if kind in (b"stco", b"co64"):
                if content + 8 > box_end:
                    raise RuntimeError("Chunk offset table is truncated.")
                count = struct.unpack_from(">I", patched, content + 4)[0]
                width = 4 if kind == b"stco" else 8
                entries = content + 8
                if entries + count * width > box_end:
                    raise RuntimeError("Chunk offset entries are truncated.")
                for index in range(count):
                    entry = entries + index * width
                    fmt = ">I" if width == 4 else ">Q"
                    old_offset = struct.unpack_from(fmt, patched, entry)[0]
                    new_offset = old_offset + delta
                    if width == 4 and new_offset > 0xFFFFFFFF:
                        raise RuntimeError("stco offset overflow; co64 is required.")
                    struct.pack_into(fmt, patched, entry, new_offset)
                tables += 1
            elif kind in CONTAINER_BOXES:
                walk(content, box_end)
            position = box_end

    walk(root_header, len(patched))
    if not tables:
        raise RuntimeError("No stco/co64 table found inside moov box.")
    return bytes(patched)


def copy_region(source, destination, offset, size, chunk_size=4 * 1024 * 1024):
    source.seek(offset)
    remaining = size
    while remaining:
        block = source.read(min(chunk_size, remaining))
        if not block:
            raise RuntimeError("Unexpected end of MP4 while copying boxes.")
        destination.write(block)
        remaining -= len(block)


def make_faststart(source_path, output_path):
    boxes = top_level_boxes(source_path)
    moov_boxes = [box for box in boxes if box[0] == b"moov"]
    mdat_boxes = [box for box in boxes if box[0] == b"mdat"]
    if len(moov_boxes) != 1 or len(mdat_boxes) != 1:
        raise RuntimeError("Expected exactly one moov and one mdat box.")
    moov = moov_boxes[0]
    mdat = mdat_boxes[0]

    if moov[1] < mdat[1]:
        source_path.replace(output_path)
        return

    with source_path.open("rb") as source:
        source.seek(moov[1])
        moov_data = source.read(moov[2])
        patched_moov = patch_chunk_offsets(moov_data, moov[2])
        with output_path.open("xb") as destination:
            for box in boxes:
                if box == mdat:
                    destination.write(patched_moov)
                if box == moov:
                    continue
                copy_region(source, destination, box[1], box[2])


def validate_video(path, expected):
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"Cannot open encoded video: {path}")
    actual = {
        "width": int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "height": int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        "fps": capture.get(cv2.CAP_PROP_FPS),
        "frames": int(capture.get(cv2.CAP_PROP_FRAME_COUNT)),
        "codec": fourcc_text(capture.get(cv2.CAP_PROP_FOURCC)),
        "pixel_format": fourcc_text(capture.get(cv2.CAP_PROP_CODEC_PIXEL_FORMAT)),
    }
    read_frames = 0
    while True:
        ok, _frame = capture.read()
        if not ok:
            break
        read_frames += 1
    capture.release()

    if actual["width"] != expected["width"] or actual["height"] != expected["height"]:
        raise RuntimeError(f"Encoded resolution mismatch: {actual}")
    if abs(actual["fps"] - expected["fps"]) > 0.01:
        raise RuntimeError(f"Encoded FPS mismatch: {actual}")
    if actual["frames"] != expected["frames"] or read_frames != expected["frames"]:
        raise RuntimeError(
            f"Encoded frame count mismatch: metadata={actual['frames']}, read={read_frames}."
        )
    if actual["codec"].lower() not in {"h264", "avc1"}:
        raise RuntimeError(f"Encoded codec is not H.264: {actual['codec']}")
    if actual["pixel_format"] != "I420":
        raise RuntimeError(f"Encoded pixel format is not yuv420p/I420: {actual['pixel_format']}")
    return actual


def transcode(source_path, output_path):
    source_path = source_path.resolve()
    output_path = output_path.resolve()
    if source_path == output_path:
        raise RuntimeError("Output must be a different file from the FMP4 source.")
    if not source_path.is_file():
        raise RuntimeError(f"Source video does not exist: {source_path}")
    if output_path.exists():
        raise RuntimeError(f"Refusing to overwrite existing output: {output_path}")

    encoded_path = output_path.with_name(output_path.stem + ".encoding.tmp.mp4")
    faststart_path = output_path.with_name(output_path.stem + ".faststart.tmp.mp4")
    if encoded_path.exists() or faststart_path.exists():
        raise RuntimeError("A temporary output already exists; inspect it before retrying.")

    capture = cv2.VideoCapture(str(source_path))
    if not capture.isOpened():
        raise RuntimeError(f"Cannot open source video: {source_path}")
    expected = {
        "width": int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "height": int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        "fps": capture.get(cv2.CAP_PROP_FPS),
        "frames": int(capture.get(cv2.CAP_PROP_FRAME_COUNT)),
    }
    writer = cv2.VideoWriter(
        str(encoded_path),
        cv2.CAP_MSMF,
        cv2.VideoWriter_fourcc(*"avc1"),
        expected["fps"],
        (expected["width"], expected["height"]),
    )
    if not writer.isOpened():
        capture.release()
        raise RuntimeError("Windows Media Foundation H.264 encoder is unavailable.")
    print(f"Encoder backend: {writer.getBackendName()}")

    written = 0
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        writer.write(frame)
        written += 1
        if written % 300 == 0:
            print(f"Encoded {written}/{expected['frames']} frames")
    capture.release()
    writer.release()
    if written != expected["frames"]:
        raise RuntimeError(f"Source decode stopped at {written}/{expected['frames']} frames.")

    make_faststart(encoded_path, faststart_path)
    if encoded_path.exists():
        encoded_path.unlink()
    faststart_path.replace(output_path)
    actual = validate_video(output_path, expected)
    atoms = [kind.decode("ascii", "replace") for kind, *_rest in top_level_boxes(output_path)]
    if atoms.index("moov") > atoms.index("mdat"):
        raise RuntimeError("Faststart verification failed: moov remains after mdat.")
    print(f"Created: {output_path}")
    print(f"Bytes: {output_path.stat().st_size}")
    print(f"Metadata: {actual}")
    print(f"Top-level MP4 boxes: {atoms}")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    transcode(arguments.source, arguments.output)
