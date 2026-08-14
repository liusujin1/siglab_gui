"""Generate the three portable TestKit application icons without Pillow.

The raster/ICO writer is shared with the repository's existing vector-style
icon generator. Keeping this script in source control makes the artwork
reproducible on the offline Windows build machine.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from generate_suite_icons import (  # noqa: E402
    Canvas,
    _background,
    _ico_bytes,
    _write_png,
)


def draw_samba() -> Canvas:
    """Controller/status identity: a cyan signal trace over a navy panel."""

    canvas = Canvas()
    _background(canvas, ((21, 119, 143), (11, 31, 65)))
    trace = []
    for i in range(360):
        x = 150 + i * (724 / 359)
        n = i / 359
        y = 520 - 175 * math.sin(n * math.tau * 1.8) - 45 * math.sin(n * math.tau * 5.2)
        trace.append((x, y))
    canvas.stroke_polyline(trace, 48, (5, 29, 59, 150))
    canvas.stroke_polyline(trace, 26, (234, 255, 250, 245))
    canvas.stroke_polyline(trace, 14, (49, 224, 202, 255))
    for x, y in (trace[70], trace[180], trace[290]):
        canvas.fill_circle(x, y, 24, (255, 255, 255, 245))
        canvas.fill_circle(x, y, 12, (18, 126, 145, 255))
    for x in (270, 512, 754):
        canvas.fill_rounded_rect(x - 24, 700, x + 24, 780, 12, (224, 255, 248, 210))
    return canvas


def draw_sidmat() -> Canvas:
    """Measurement identity: spectral bars and an H1-style response curve."""

    canvas = Canvas()
    _background(canvas, ((89, 75, 178), (28, 25, 78)))
    heights = [120, 220, 300, 470, 390, 275, 190, 130]
    for index, height in enumerate(heights):
        x = 200 + index * 84
        canvas.fill_rounded_rect(x - 22, 760 - height, x + 22, 760, 12, (226, 219, 255, 205))
    curve = []
    for i in range(300):
        x = 164 + i * (696 / 299)
        n = i / 299
        y = 390 - 150 * math.exp(-((n - 0.48) / 0.12) ** 2) + 38 * math.sin(n * math.tau * 2.5)
        curve.append((x, y))
    canvas.stroke_polyline(curve, 42, (19, 22, 73, 135))
    canvas.stroke_polyline(curve, 22, (255, 249, 222, 245))
    canvas.stroke_polyline(curve, 11, (255, 190, 75, 255))
    canvas.stroke_polyline([(170, 800), (854, 800)], 18, (231, 224, 255, 210))
    canvas.stroke_polyline([(170, 800), (170, 170)], 18, (231, 224, 255, 210))
    return canvas


def draw_commserver() -> Canvas:
    """Communication Server identity: a central serial node and clients."""

    canvas = Canvas()
    _background(canvas, ((188, 104, 43), (52, 38, 54)))
    center = (512, 500)
    nodes = [(246, 286), (778, 286), (250, 730), (774, 730)]
    for node in nodes:
        canvas.stroke_polyline([node, center], 34, (255, 237, 199, 115))
        canvas.fill_circle(node[0], node[1], 62, (255, 245, 218, 235))
        canvas.fill_circle(node[0], node[1], 34, (224, 119, 45, 255))
    canvas.fill_circle(center[0], center[1], 136, (255, 248, 226, 245))
    canvas.fill_circle(center[0], center[1], 88, (216, 91, 45, 255))
    canvas.fill_rounded_rect(440, 458, 584, 542, 22, (255, 238, 189, 245))
    canvas.fill_rounded_rect(474, 412, 550, 458, 16, (255, 238, 189, 245))
    canvas.fill_rounded_rect(474, 542, 550, 588, 16, (255, 238, 189, 245))
    return canvas


def _write_set(canvas: Canvas, stem: str, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    _write_png(output / f"{stem}.png", canvas.pixels, canvas.size, canvas.size)
    (output / f"{stem}.ico").write_bytes(_ico_bytes(canvas.pixels, canvas.size))


def _write_sources(output: Path) -> None:
    """Write small editable SVG companions for review/design handoff."""

    svgs = {
        "samba_icon": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024"><rect x="74" y="74" width="876" height="876" rx="164" fill="#0b1f41"/><path d="M150 520c150-330 250 330 400 0s250 330 324 0" fill="none" stroke="#31e0ca" stroke-width="28" stroke-linecap="round"/></svg>',
        "sidmat_icon": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024"><rect x="74" y="74" width="876" height="876" rx="164" fill="#1c194e"/><g fill="#e2dbff"><path d="M178 760h44V640h-44zM262 760h44V540h-44zM346 760h44V410h-44zM430 760h44V290h-44zM514 760h44V390h-44zM598 760h44V500h-44zM682 760h44V610h-44z"/></g><path d="M164 450c150-180 260-120 350 10s210 90 346-80" fill="none" stroke="#ffbe4b" stroke-width="24" stroke-linecap="round"/></svg>',
        "commserver_icon": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024"><rect x="74" y="74" width="876" height="876" rx="164" fill="#342636"/><g stroke="#ffedc7" stroke-width="28" fill="none"><path d="M246 286 512 500 778 286M250 730 512 500 774 730"/></g><circle cx="512" cy="500" r="126" fill="#d85b2d" stroke="#ffedc7" stroke-width="24"/></svg>',
    }
    for stem, content in svgs.items():
        (output / f"{stem}.svg").write_text(content + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "assets")
    args = parser.parse_args(argv)
    output = args.output.resolve()
    _write_set(draw_samba(), "samba_icon", output)
    _write_set(draw_sidmat(), "sidmat_icon", output)
    _write_set(draw_commserver(), "commserver_icon", output)
    _write_sources(output)
    for path in sorted(output.glob("*_icon.*")):
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
