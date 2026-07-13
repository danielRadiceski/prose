"""Prose iconography.

Two artworks, used at different sizes:

* The **logo** (assets/prose_logo.png) — the full badge with cursive wordmark.
  Beautiful, but the script turns to mush below ~48px.
* A **procedural glyph** — the same gradient disc with a clean mic, drawn at
  runtime. Stays crisp at 16px and can be tinted to show state.

`build_ico()` combines them: glyph for the small entries, logo for the large
ones. The tray always uses the glyph (Windows draws it at 16px).
"""

import math
from pathlib import Path

from PIL import Image, ImageDraw

ASSETS = Path(__file__).parent / "assets"
LOGO_PATH = ASSETS / "prose_logo.png"

# Sampled from the logo's own gradient (135° cyan -> purple)
BRAND_CYAN = (33, 168, 238)
BRAND_PURPLE = (107, 54, 214)

# Disc gradient per app state; the mic glyph stays white.
_STATE_GRADIENT = {
    "idle": (BRAND_CYAN, BRAND_PURPLE),        # brand colors — ready
    "listening": ((255, 106, 92), (214, 40, 57)),   # red — recording
    "processing": ((255, 194, 71), (240, 138, 20)),  # amber — transcribing
    "disabled": ((150, 155, 165), (95, 100, 112)),   # gray — paused
}

_SS = 8  # supersampling factor: draw big, shrink down => smooth edges at 16px


def _gradient_disc(px: int, c0, c1) -> Image.Image:
    """A circular disc filled with a 135° linear gradient, antialiased."""
    grad = Image.new("RGB", (px, px))
    d = ImageDraw.Draw(grad)
    for i in range(2 * px):  # diagonal sweep: t follows (x + y)
        t = i / (2 * px - 1)
        d.line(
            [(i, 0), (0, i)],
            fill=tuple(round(a + (b - a) * t) for a, b in zip(c0, c1)),
        )
    mask = Image.new("L", (px, px), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, px - 1, px - 1], fill=255)
    disc = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    disc.paste(grad, (0, 0), mask)
    return disc


def _draw_mic(img: Image.Image, px: int, detailed: bool) -> None:
    """White microphone on a stand.

    The cradle arc is drawn only when `detailed` — below ~32px it collapses into
    the stem and base and the whole thing reads as a smudge.
    """
    d = ImageDraw.Draw(img)
    W = (255, 255, 255, 255)
    cx = px / 2
    stroke = max(2, round(px * 0.085))

    # The cradle needs vertical room, so the capsule sits higher when detailed.
    cap_bot = px * (0.55 if detailed else 0.60)
    stem_top = px * (0.735 if detailed else 0.60)
    base_y = px * (0.815 if detailed else 0.80)

    cap_w, cap_top = px * 0.26, px * 0.19
    d.rounded_rectangle(
        [cx - cap_w / 2, cap_top, cx + cap_w / 2, cap_bot], radius=cap_w / 2, fill=W
    )

    if detailed:
        # bottom half of a circle centered above the stem: wraps the capsule,
        # its lowest point meeting the top of the stem
        arc_r, arc_cy = px * 0.225, px * 0.51
        d.arc(
            [cx - arc_r, arc_cy - arc_r, cx + arc_r, arc_cy + arc_r],
            start=0, end=180, fill=W, width=max(2, round(px * 0.06)),
        )

    d.line([cx, stem_top, cx, base_y - stroke / 2], fill=W, width=stroke)
    base_w = px * 0.38
    d.line([cx - base_w / 2, base_y, cx + base_w / 2, base_y], fill=W, width=stroke)


def make_glyph(size: int = 32, state: str = "idle") -> Image.Image:
    """Crisp mic-in-disc glyph, tinted for `state`. Used by the tray and small ICO sizes."""
    c0, c1 = _STATE_GRADIENT.get(state, _STATE_GRADIENT["idle"])
    px = size * _SS
    img = _gradient_disc(px, c0, c1)
    _draw_mic(img, px, detailed=size >= 48)
    return img.resize((size, size), Image.LANCZOS)


def load_logo() -> Image.Image | None:
    """The full Gemini badge, or None if the asset is missing."""
    if LOGO_PATH.exists():
        return Image.open(LOGO_PATH).convert("RGBA")
    return None


def build_ico(path: Path) -> None:
    """Write a multi-resolution .ico: glyph at small sizes, logo at large ones."""
    logo = load_logo()
    small, large = [16, 24, 32], [48, 64, 128, 256]
    frames = [make_glyph(s, "idle") for s in small]
    for s in large:
        frames.append(logo.resize((s, s), Image.LANCZOS) if logo else make_glyph(s, "idle"))
    biggest = frames[-1]
    biggest.save(
        path,
        format="ICO",
        sizes=[(f.size[0], f.size[0]) for f in frames],
        append_images=frames[:-1],
    )


if __name__ == "__main__":
    # Preview every state at tray size, zoomed
    Z, states = 8, list(_STATE_GRADIENT)
    sheet = Image.new("RGB", (len(states) * (16 * Z + 12) + 12, 16 * Z + 24), (240, 240, 243))
    for i, st in enumerate(states):
        g = make_glyph(16, st).resize((16 * Z, 16 * Z), Image.NEAREST)
        sheet.paste(g, (12 + i * (16 * Z + 12), 12), g)
    sheet.save("scratch_glyph_states.png")
    print("wrote scratch_glyph_states.png")
