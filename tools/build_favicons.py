"""Build the project's favicon and app-icon variants from one PNG source."""

from __future__ import annotations

import argparse
import struct
import zlib
from pathlib import Path


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _paeth(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    left_distance = abs(estimate - left)
    above_distance = abs(estimate - above)
    upper_left_distance = abs(estimate - upper_left)
    if left_distance <= above_distance and left_distance <= upper_left_distance:
        return left
    if above_distance <= upper_left_distance:
        return above
    return upper_left


def _read_png(path: Path) -> tuple[int, int, int, bytes]:
    data = path.read_bytes()
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError(f"{path} is not a PNG file")

    position = len(PNG_SIGNATURE)
    width = height = bit_depth = color_type = interlace = None
    compressed = bytearray()
    while position < len(data):
        length = struct.unpack(">I", data[position : position + 4])[0]
        chunk_type = data[position + 4 : position + 8]
        chunk = data[position + 8 : position + 8 + length]
        position += 12 + length
        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", chunk
            )
            if compression != 0 or filtering != 0 or interlace != 0:
                raise ValueError("Only non-interlaced standard PNG files are supported")
        elif chunk_type == b"IDAT":
            compressed.extend(chunk)
        elif chunk_type == b"IEND":
            break

    if width is None or height is None or bit_depth != 8:
        raise ValueError("Only 8-bit PNG files are supported")
    if color_type not in (2, 6):
        raise ValueError("Only RGB and RGBA PNG files are supported")

    channels = 3 if color_type == 2 else 4
    stride = width * channels
    raw = zlib.decompress(compressed)
    expected = height * (stride + 1)
    if len(raw) != expected:
        raise ValueError("PNG scanline data has an unexpected length")

    rows: list[bytearray] = []
    position = 0
    previous = bytearray(stride)
    for _ in range(height):
        filter_type = raw[position]
        encoded = raw[position + 1 : position + 1 + stride]
        position += stride + 1
        row = bytearray(stride)
        for index, value in enumerate(encoded):
            left = row[index - channels] if index >= channels else 0
            above = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = above
            elif filter_type == 3:
                predictor = (left + above) // 2
            elif filter_type == 4:
                predictor = _paeth(left, above, upper_left)
            else:
                raise ValueError(f"Unsupported PNG filter type: {filter_type}")
            row[index] = (value + predictor) & 0xFF
        rows.append(row)
        previous = row

    return width, height, channels, b"".join(rows)


def _resize(
    pixels: bytes,
    source_width: int,
    source_height: int,
    channels: int,
    target_width: int,
    target_height: int,
) -> bytes:
    result = bytearray(target_width * target_height * channels)
    for target_y in range(target_height):
        source_y = (target_y + 0.5) * source_height / target_height - 0.5
        y0 = max(0, min(source_height - 1, int(source_y)))
        y1 = min(source_height - 1, y0 + 1)
        y_fraction = max(0.0, min(1.0, source_y - y0))
        for target_x in range(target_width):
            source_x = (target_x + 0.5) * source_width / target_width - 0.5
            x0 = max(0, min(source_width - 1, int(source_x)))
            x1 = min(source_width - 1, x0 + 1)
            x_fraction = max(0.0, min(1.0, source_x - x0))
            target_offset = (target_y * target_width + target_x) * channels
            top_left = (y0 * source_width + x0) * channels
            top_right = (y0 * source_width + x1) * channels
            bottom_left = (y1 * source_width + x0) * channels
            bottom_right = (y1 * source_width + x1) * channels
            for channel in range(channels):
                top = pixels[top_left + channel] * (1 - x_fraction)
                top += pixels[top_right + channel] * x_fraction
                bottom = pixels[bottom_left + channel] * (1 - x_fraction)
                bottom += pixels[bottom_right + channel] * x_fraction
                result[target_offset + channel] = round(
                    top * (1 - y_fraction) + bottom * y_fraction
                )
    return bytes(result)


def _chunk(chunk_type: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + chunk_type
        + payload
        + struct.pack(">I", zlib.crc32(chunk_type + payload) & 0xFFFFFFFF)
    )


def _write_png(
    path: Path,
    pixels: bytes,
    width: int,
    height: int,
    channels: int,
) -> bytes:
    color_type = 2 if channels == 3 else 6
    scanlines = bytearray()
    stride = width * channels
    for row in range(height):
        scanlines.append(0)
        start = row * stride
        scanlines.extend(pixels[start : start + stride])
    payload = (
        PNG_SIGNATURE
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(bytes(scanlines), 9))
        + _chunk(b"IEND", b"")
    )
    path.write_bytes(payload)
    return payload


def _write_ico(path: Path, images: list[tuple[int, bytes]]) -> None:
    header = struct.pack("<HHH", 0, 1, len(images))
    entries = bytearray()
    offset = 6 + 16 * len(images)
    for size, image in images:
        entries.extend(
            struct.pack(
                "<BBBBHHII",
                0 if size == 256 else size,
                0 if size == 256 else size,
                0,
                0,
                1,
                24,
                len(image),
                offset,
            )
        )
        offset += len(image)
    path.write_bytes(header + bytes(entries) + b"".join(image for _, image in images))


def build(source: Path, output_dir: Path) -> None:
    source_width, source_height, channels, pixels = _read_png(source)
    output_dir.mkdir(parents=True, exist_ok=True)
    pngs: dict[int, bytes] = {}
    filenames = {
        16: "favicon-16.png",
        32: "favicon-32.png",
        64: "favicon-64.png",
        180: "favicon-180.png",
        192: "favicon-192.png",
        512: "favicon-512.png",
    }
    for size, filename in filenames.items():
        resized = _resize(
            pixels,
            source_width,
            source_height,
            channels,
            size,
            size,
        )
        pngs[size] = _write_png(
            output_dir / filename,
            resized,
            size,
            size,
            channels,
        )

    icon_sizes = (16, 32, 48)
    icon_images = []
    for size in icon_sizes:
        if size not in pngs:
            resized = _resize(
                pixels,
                source_width,
                source_height,
                channels,
                size,
                size,
            )
            temporary = output_dir / f".favicon-{size}.png"
            icon_images.append(
                (size, _write_png(temporary, resized, size, size, channels))
            )
            temporary.unlink()
        else:
            icon_images.append((size, pngs[size]))
    _write_ico(output_dir / "favicon.ico", icon_images)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build favicon and app-icon variants from a square PNG."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("static/icons/favicon-512.png"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("static/icons"),
    )
    args = parser.parse_args()
    build(args.source, args.output_dir)
    print(f"Built favicon assets in {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
