from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path


SIZE = 1024
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def _clamp(value: float) -> int:
    return max(0, min(255, int(round(value))))


def _blend(dst: tuple[int, int, int, int], src: tuple[int, int, int, int], alpha: float) -> tuple[int, int, int, int]:
    sa = max(0.0, min(1.0, src[3] / 255.0 * alpha))
    da = dst[3] / 255.0
    out_a = sa + da * (1.0 - sa)
    if out_a <= 0.0:
        return (0, 0, 0, 0)
    out = []
    for idx in range(3):
        value = (src[idx] * sa + dst[idx] * da * (1.0 - sa)) / out_a
        out.append(_clamp(value))
    out.append(_clamp(out_a * 255.0))
    return tuple(out)  # type: ignore[return-value]


class Canvas:
    def __init__(self, size: int = SIZE):
        self.size = size
        self.pixels = [(0, 0, 0, 0)] * (size * size)

    def set_pixel(self, x: int, y: int, color: tuple[int, int, int, int], alpha: float = 1.0) -> None:
        if x < 0 or y < 0 or x >= self.size or y >= self.size:
            return
        index = y * self.size + x
        self.pixels[index] = _blend(self.pixels[index], color, alpha)

    def fill_rounded_rect(
        self,
        left: float,
        top: float,
        right: float,
        bottom: float,
        radius: float,
        color: tuple[int, int, int, int],
    ) -> None:
        min_x = max(0, int(math.floor(left)))
        max_x = min(self.size - 1, int(math.ceil(right)))
        min_y = max(0, int(math.floor(top)))
        max_y = min(self.size - 1, int(math.ceil(bottom)))
        for y in range(min_y, max_y + 1):
            cy = y + 0.5
            for x in range(min_x, max_x + 1):
                cx = x + 0.5
                dx = max(left + radius - cx, 0.0, cx - (right - radius))
                dy = max(top + radius - cy, 0.0, cy - (bottom - radius))
                dist = math.hypot(dx, dy)
                coverage = max(0.0, min(1.0, radius + 0.75 - dist))
                if coverage > 0.0:
                    self.set_pixel(x, y, color, coverage)

    def stroke_polyline(
        self,
        points: list[tuple[float, float]],
        width: float,
        color: tuple[int, int, int, int],
    ) -> None:
        radius = width / 2.0
        for start, end in zip(points, points[1:]):
            x1, y1 = start
            x2, y2 = end
            min_x = max(0, int(math.floor(min(x1, x2) - radius - 2)))
            max_x = min(self.size - 1, int(math.ceil(max(x1, x2) + radius + 2)))
            min_y = max(0, int(math.floor(min(y1, y2) - radius - 2)))
            max_y = min(self.size - 1, int(math.ceil(max(y1, y2) + radius + 2)))
            vx = x2 - x1
            vy = y2 - y1
            length_sq = vx * vx + vy * vy
            if length_sq <= 0.0:
                continue
            for y in range(min_y, max_y + 1):
                py = y + 0.5
                for x in range(min_x, max_x + 1):
                    px = x + 0.5
                    t = max(0.0, min(1.0, ((px - x1) * vx + (py - y1) * vy) / length_sq))
                    closest_x = x1 + t * vx
                    closest_y = y1 + t * vy
                    dist = math.hypot(px - closest_x, py - closest_y)
                    coverage = max(0.0, min(1.0, radius + 0.75 - dist))
                    if coverage > 0.0:
                        self.set_pixel(x, y, color, coverage)

    def fill_circle(self, cx: float, cy: float, radius: float, color: tuple[int, int, int, int]) -> None:
        min_x = max(0, int(math.floor(cx - radius - 1)))
        max_x = min(self.size - 1, int(math.ceil(cx + radius + 1)))
        min_y = max(0, int(math.floor(cy - radius - 1)))
        max_y = min(self.size - 1, int(math.ceil(cy + radius + 1)))
        for y in range(min_y, max_y + 1):
            for x in range(min_x, max_x + 1):
                dist = math.hypot((x + 0.5) - cx, (y + 0.5) - cy)
                coverage = max(0.0, min(1.0, radius + 0.75 - dist))
                if coverage > 0.0:
                    self.set_pixel(x, y, color, coverage)

    def fill_vertical_bar(
        self,
        center_x: float,
        bottom: float,
        height: float,
        width: float,
        color: tuple[int, int, int, int],
    ) -> None:
        self.fill_rounded_rect(center_x - width / 2, bottom - height, center_x + width / 2, bottom, width / 2, color)


def _background(canvas: Canvas, palette: tuple[tuple[int, int, int], tuple[int, int, int]]) -> None:
    size = canvas.size
    for y in range(size):
        ny = y / (size - 1)
        for x in range(size):
            nx = x / (size - 1)
            radial = max(0.0, 1.0 - math.hypot(nx - 0.34, ny - 0.22) / 0.95)
            mix = 0.18 + 0.72 * radial
            color = tuple(_clamp(palette[0][idx] * mix + palette[1][idx] * (1.0 - mix)) for idx in range(3))
            canvas.pixels[y * size + x] = (*color, 255)
    canvas.fill_rounded_rect(74, 74, 950, 950, 164, (255, 255, 255, 35))
    canvas.fill_rounded_rect(102, 102, 922, 922, 138, (0, 0, 0, 28))
    canvas.fill_rounded_rect(122, 122, 902, 902, 120, (255, 255, 255, 32))


def draw_vianalysis() -> Canvas:
    canvas = Canvas()
    _background(canvas, ((23, 111, 138), (12, 33, 66)))
    for index, height in enumerate([150, 250, 330, 470, 360, 260, 180]):
        x = 230 + index * 94
        canvas.fill_vertical_bar(x, 744, height, 38, (116, 231, 205, 95))
    curve: list[tuple[float, float]] = []
    for i in range(360):
        x = 160 + i * (704 / 359)
        n = i / 359
        y = 600 - 248 * math.exp(-((n - 0.48) / 0.12) ** 2) + 76 * math.sin(n * math.tau * 4.0)
        curve.append((x, y))
    canvas.stroke_polyline(curve, 44, (9, 63, 92, 150))
    canvas.stroke_polyline(curve, 30, (238, 255, 247, 245))
    canvas.stroke_polyline(curve, 15, (38, 218, 195, 255))
    for cx, cy in (curve[76], curve[174], curve[260]):
        canvas.fill_circle(cx, cy, 24, (255, 255, 255, 245))
        canvas.fill_circle(cx, cy, 13, (28, 170, 159, 255))
    canvas.stroke_polyline([(196, 762), (828, 762)], 20, (222, 255, 248, 190))
    return canvas


def draw_diagnostic() -> Canvas:
    canvas = Canvas()
    _background(canvas, ((45, 93, 166), (28, 26, 74)))
    for angle in range(215, 326, 2):
        rad = math.radians(angle)
        inner = (512 + math.cos(rad) * 238, 628 + math.sin(rad) * 238)
        outer = (512 + math.cos(rad) * 316, 628 + math.sin(rad) * 316)
        color = (130, 183, 255, 60 + int((angle - 215) / 110 * 130))
        canvas.stroke_polyline([inner, outer], 16, color)
    arc: list[tuple[float, float]] = []
    for angle in range(210, 331):
        rad = math.radians(angle)
        arc.append((512 + math.cos(rad) * 302, 628 + math.sin(rad) * 302))
    canvas.stroke_polyline(arc, 34, (240, 248, 255, 210))
    canvas.stroke_polyline(arc, 18, (92, 155, 255, 255))
    needle_angle = math.radians(292)
    needle_tip = (512 + math.cos(needle_angle) * 250, 628 + math.sin(needle_angle) * 250)
    canvas.stroke_polyline([(512, 628), needle_tip], 32, (255, 255, 255, 245))
    canvas.stroke_polyline([(512, 628), needle_tip], 15, (255, 177, 67, 255))
    canvas.fill_circle(512, 628, 50, (255, 255, 255, 235))
    canvas.fill_circle(512, 628, 27, (55, 87, 173, 255))
    wave: list[tuple[float, float]] = []
    for i in range(300):
        x = 232 + i * (560 / 299)
        n = i / 299
        y = 398 + 34 * math.sin(n * math.tau * 3.0) + 12 * math.sin(n * math.tau * 9.0)
        wave.append((x, y))
    canvas.stroke_polyline(wave, 30, (17, 28, 73, 120))
    canvas.stroke_polyline(wave, 16, (112, 242, 255, 235))
    return canvas


def _write_png(path: Path, pixels: list[tuple[int, int, int, int]], width: int, height: int) -> None:
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            r, g, b, a = pixels[y * width + x]
            raw.extend((r, g, b, a))
    compressed = zlib.compress(bytes(raw), 9)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", compressed)
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)


def _resize_nearest(pixels: list[tuple[int, int, int, int]], source_size: int, target_size: int) -> list[tuple[int, int, int, int]]:
    resized: list[tuple[int, int, int, int]] = []
    for y in range(target_size):
        source_y = min(source_size - 1, int(y * source_size / target_size))
        for x in range(target_size):
            source_x = min(source_size - 1, int(x * source_size / target_size))
            resized.append(pixels[source_y * source_size + source_x])
    return resized


def _ico_bytes(pixels: list[tuple[int, int, int, int]], source_size: int) -> bytes:
    images: list[bytes] = []
    for size in ICON_SIZES:
        resized = _resize_nearest(pixels, source_size, size)
        raw_path = Path("__unused__.png")
        raw = bytearray()
        for y in range(size):
            raw.append(0)
            for x in range(size):
                r, g, b, a = resized[y * size + x]
                raw.extend((r, g, b, a))

        def chunk(kind: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

        images.append(
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b"")
        )
        _ = raw_path

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + len(images) * 16
    directory = bytearray()
    payload = bytearray()
    for size, image in zip(ICON_SIZES, images):
        directory.extend(
            struct.pack(
                "<BBBBHHII",
                0 if size == 256 else size,
                0 if size == 256 else size,
                0,
                0,
                1,
                32,
                len(image),
                offset,
            )
        )
        payload.extend(image)
        offset += len(image)
    return header + bytes(directory) + bytes(payload)


def write_icon_set(canvas: Canvas, stem: str) -> None:
    assets = Path("assets")
    assets.mkdir(exist_ok=True)
    png_path = assets / f"{stem}.png"
    ico_path = assets / f"{stem}.ico"
    _write_png(png_path, canvas.pixels, canvas.size, canvas.size)
    ico_path.write_bytes(_ico_bytes(canvas.pixels, canvas.size))
    print(f"wrote {png_path}")
    print(f"wrote {ico_path}")


def main() -> int:
    write_icon_set(draw_vianalysis(), "vianalysis_icon")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
