"""Builds docs/assets/tour.gif from the README screenshots (run `make screenshots`)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
SHOTS = ROOT / "docs/assets/screenshots"
ORDER = [
    "01-overview",
    "02-explorer",
    "03-models",
    "04-prompts",
    "05-optimizer",
    "06-lab",
    "07-budgets",
    "08-events",
    "09-settings",
    "10-overview-light",
]
WIDTH = 960

frames = []
for name in ORDER:
    im = Image.open(SHOTS / f"{name}.png").convert("RGB")
    im = im.resize((WIDTH, round(im.height * WIDTH / im.width)), Image.LANCZOS)
    frames.append(im.quantize(colors=128, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE))
out = ROOT / "docs/assets/tour.gif"
frames[0].save(out, save_all=True, append_images=frames[1:], duration=2200, loop=0, optimize=True)
print(f"wrote {out} ({out.stat().st_size // 1024} KB, {len(frames)} frames)")
