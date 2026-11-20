"""
The site's icon: a dark Indiana on a light circle, with a red pin on Gary, home of the state's
two deadliest polluters (US Steel Gary Works and Cleveland-Cliffs Burns Harbor).
Writes site/favicon.svg (browser tabs), site/favicon.ico (Google and older browsers; 16, 32
and 48 px) and site/apple-touch-icon.png (iPhone home screens).

The outline was traced from the Census county outlines (places.counties(): the border edges
that belong to only one county), then simplified to 47 points so it stays clean when tiny.

    python3 scripts/make_icons.py
"""

import re
from pathlib import Path

from PIL import Image, ImageDraw

SITE = Path(__file__).resolve().parent.parent / "site"

INDIANA = ("M21.2 6.7L23.4 7.8L28.2 6.0L48.4 6.0L48.2 40.6L47.5 41.2L48.1 42.4L47.7 43.2L48.6 43.6"
           "L48.3 44.8L44.7 46.1L42.1 45.5L42.2 48.1L40.4 49.3L39.6 51.2L38.1 51.4L37.2 54.7L36.0 55.6"
           "L33.7 54.3L32.8 52.4L33.2 53.2L31.8 53.5L31.3 55.9L30.0 57.1L28.3 55.0L26.1 56.2L25.3 57.9"
           "L20.5 55.3L20.2 57.2L19.6 56.3L18.1 56.6L17.4 56.0L16.9 58.0L15.5 57.4L16.1 57.2L15.4 56.3"
           "L16.2 56.4L16.0 54.4L17.3 52.9L16.5 51.7L18.0 51.3L21.4 45.3L21.3 42.6L19.8 40.2L20.6 39.1"
           "L20.2 38.0L21.1 37.4Z")   # in a 64 x 64 box: 10 units per degree of longitude, 13 per degree of latitude
GARY = (23.1, 8.0)                   # Gary Works (87.33 W, 41.61 N) in the same box
SCALE = 0.78                         # Indiana is drawn at 78% of the box, around its center, to sit inside the circle
PIN = 5.5                            # pin radius; the 16 px icon gets a bigger pin so it doesn't vanish
HALO = 2                             # light ring around the pin, separating it from the state
LIGHT, DARK, RED = "#f3efe8", "#0d0e11", "#ff4b3a"


def fit(x, y):
    return 32 + SCALE * (x - 32), 32 + SCALE * (y - 32)


def points():
    return [fit(*map(float, m)) for m in re.findall(r"([\d.]+) ([\d.]+)", INDIANA)]


def svg():
    d = "M" + "L".join(f"{x:.1f} {y:.1f}" for x, y in points()) + "Z"
    gx, gy = fit(*GARY)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            f'<circle cx="32" cy="32" r="32" fill="{LIGHT}"/><path d="{d}" fill="{DARK}"/>'
            f'<circle cx="{gx:.1f}" cy="{gy:.1f}" r="{PIN}" fill="{RED}" stroke="{LIGHT}" stroke-width="{HALO}"/></svg>\n')


def png(size, pin=PIN, square=False):
    """Drawn 8x larger, then shrunk, for smooth edges. square: fill the corners too (iOS rounds them itself)."""
    k = size * 8 / 64
    im = Image.new("RGBA", (size * 8, size * 8), LIGHT if square else (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse([0, 0, 64 * k - 1, 64 * k - 1], fill=LIGHT)
    d.polygon([(x * k, y * k) for x, y in points()], fill=DARK)
    gx, gy = fit(*GARY)
    for r, color in ((pin + HALO / 2, LIGHT), (pin - HALO / 2, RED)):   # the halo straddles the pin's edge, as in the SVG
        d.ellipse([(gx - r) * k, (gy - r) * k, (gx + r) * k, (gy + r) * k], fill=color)
    return im.resize((size, size), Image.LANCZOS)


def main():
    (SITE / "favicon.svg").write_text(svg())
    small, mid, big = png(16, pin=10), png(32, pin=7), png(48)
    big.save(SITE / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)], append_images=[small, mid])
    png(180, square=True).save(SITE / "apple-touch-icon.png")
    print("Wrote site/favicon.svg, site/favicon.ico and site/apple-touch-icon.png")


if __name__ == "__main__":
    main()
