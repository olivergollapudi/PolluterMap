"""
Draw a link-preview ("share") image for every polluter page: the picture Facebook, X, iMessage,
Slack, Reddit and others show when someone shares a link to the site.

Each image (1200 x 630, the size every network expects) shows the polluter's name, its rank and
estimated deaths a year, next to a dot map of every Indiana polluter with this one glowing red.
Polluters with almost no deaths lead with their biggest other harm instead.

Reads site/site-data.js and data/processed/site_places.json (both made by export_site_data.py)
and writes:
  - site/p/img/<slug>.png, one per polluter page
  - site/p/img/county-<name>.png and town-<name>.png, one per county and town page: that place's
    polluters lit up (and the county's outline)
  - site/p/img/home.png, for the home page and the lists

Needs Pillow (pip install pillow), the one non-standard library in the project, because Python
can't draw text into images by itself. Uses the Inter font (SIL Open Font License, in
scripts/fonts/), so the images can be published freely.

    python3 scripts/make_share_images.py
"""

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).parent))
from export_site_data import deaths_words, harms  # same wording and rounding as the pages
from places import counties

ROOT = Path(__file__).resolve().parent.parent
FONT = Path(__file__).parent / "fonts" / "Inter.ttf"
DATA = ROOT / "site" / "site-data.js"
PLACES = ROOT / "data" / "processed" / "site_places.json"
OUT = ROOT / "site" / "p" / "img"
W, H = 1200, 630
LEFT_W = 700                                   # text column; the map sits to its right
MAP_BOX = (790, 56, 1170, 560)
URL_TEXT = "indianapollution.org"

BG, INK, MUTED, RED, DEEP, DOT = (13, 14, 17), (243, 239, 232), (155, 150, 142), (255, 75, 58), (196, 40, 27), (92, 78, 74)

_fonts = {}


def font(size, weight=400):
    k = (size, weight)
    if k not in _fonts:
        f = ImageFont.truetype(str(FONT), size)
        f.set_variation_by_axes([min(32, max(14, size)), weight])
        _fonts[k] = f
    return _fonts[k]


def load():
    text = DATA.read_text(encoding="utf-8")
    return json.loads(text.split("=", 1)[1].strip().rstrip(";"))["facilities"]


def wrap(draw, text, f, width, max_lines):
    """Split text into lines that fit; shorten the last line with … if it runs over."""
    words, lines = text.split(), []
    while words and len(lines) < max_lines:
        line = words.pop(0)
        while words and draw.textlength(line + " " + words[0], font=f) <= width:
            line += " " + words.pop(0)
        lines.append(line)
    if words:
        last = lines[-1]
        while last and draw.textlength(last + "…", font=f) > width:
            last = last[:-1].rstrip()
        lines[-1] = last.rstrip(" -–,·&") + "…"
    return lines


def fit(draw, text, width, sizes, weight, max_lines):
    """The biggest size (from sizes) at which text fits in max_lines lines without cutting it off."""
    for s in sizes:
        f = font(s, weight)
        lines = wrap(draw, text, f, width, max_lines)
        if not lines[-1].endswith("…"):
            break
    return f, lines


def headline(f):
    """The big number and its label: deaths if there are any to speak of, else the polluter's biggest harm."""
    if f["deaths"] >= 0.1:
        return deaths_words(f["deaths"]), "estimated deaths a year", True
    v, label, _ = harms(f)[0]
    return v, label, False


def where(f):
    return " · ".join(x for x in (f["kind"], ", ".join(x for x in (f["city"], "Indiana") if x)) if x)


def map_xy(lat, lon):
    """Place a point in MAP_BOX (Indiana spans about 37.77–41.76 N, 84.78–88.10 W). Longitude is
    shrunk by cos(40°) so the state keeps its shape."""
    x0, y0, x1, y1 = MAP_BOX
    s = min((x1 - x0) / ((88.10 - 84.78) * 0.76), (y1 - y0) / (41.76 - 37.77))
    return (x0 + x1) / 2 + (lon + 86.44) * 0.76 * s, (y0 + y1) / 2 - (lat - 39.765) * s


def glow(img, xy, r, color, blur, alpha):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    x, y = xy
    ImageDraw.Draw(layer).ellipse((x - r, y - r, x + r, y + r), fill=(*color, alpha))
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur)))


def marker(img, xy):
    """The glowing red dot and ring for the polluter in the picture."""
    glow(img, xy, 46, RED, 26, 200)
    d = ImageDraw.Draw(img)
    x, y = xy
    d.ellipse((x - 13, y - 13, x + 13, y + 13), fill=RED)
    d.ellipse((x - 26, y - 26, x + 26, y + 26), outline=RED, width=3)


def base(everyone, statewide=True):
    """The background images share: the site's name, and the dot map of all polluters."""
    img = Image.new("RGBA", (W, H), (*BG, 255))
    d = ImageDraw.Draw(img)
    for o in everyone if statewide else ():
        if o["lat"] and o["lon"]:
            x, y = map_xy(o["lat"], o["lon"])
            r = 2 + min(10, (o["deaths"] or 0) ** 0.4 / 2)
            d.ellipse((x - r, y - r, x + r, y + r), fill=DOT)
    x = 64
    for t, col in (("Indiana's ", INK), ("Deadliest", RED), (" Polluters", INK)):
        d.text((x, 52), t, font=font(26, 900), fill=col)
        x += d.textlength(t, font=font(26, 900))
    d.text((W - 64, H - 40), URL_TEXT, font=font(24, 600), fill=MUTED, anchor="rs")
    return img


def pill(d, x, y, text):
    f = font(22, 800)
    w = d.textlength(text, font=f)
    d.rounded_rectangle((x, y, x + w + 32, y + 40), radius=20, fill=DEEP)
    d.text((x + 16, y + 20), text, font=f, fill=(255, 255, 255), anchor="lm")


def big_number(d, x, top, value, label, is_deaths):
    """The big number with its label under it, as large as fits between top and the bottom margin."""
    label_y = H - 64                      # baseline of the label
    num_y = label_y - 68                  # baseline of the number (room for the comma's tail)
    for s in (240, 220, 200, 180, 160, 140, 120, 100, 84, 72):
        f = font(s, 900)
        if d.textlength(value, font=f) <= LEFT_W and num_y - s * 0.73 >= top:
            break
    d.text((x - s * 0.04, num_y), value, font=f, fill=INK, anchor="ls")
    lf, ll = fit(d, label.upper(), LEFT_W, (30, 26, 22), 800, 1)
    d.text((x, label_y), ll[0], font=lf, fill=RED if is_deaths else INK, anchor="ls")


def text_block(img, pill_text, title, sub, value, label, is_deaths):
    d = ImageDraw.Draw(img)
    y = 118
    if pill_text:
        pill(d, 64, y, pill_text)
        y += 62
    nf, lines = fit(d, title, LEFT_W, (58, 52, 46, 40), 800, 2)
    for line in lines:
        d.text((64, y), line, font=nf, fill=INK)
        y += int(nf.size * 1.12)
    mf, ml = fit(d, sub, LEFT_W, (28, 24), 500, 1)
    d.text((64, y + 6), ml[0], font=mf, fill=MUTED)
    big_number(d, 64, y + 70, value, label, is_deaths)


def polluter_image(f, background):
    img = background.copy()
    if f["lat"] and f["lon"]:
        marker(img, map_xy(f["lat"], f["lon"]))
    value, label, is_deaths = headline(f)
    text_block(img, f"#{f['rank']} DEADLIEST IN INDIANA" if is_deaths else "", f["name"], where(f), value, label, is_deaths)
    return img


def home_image(everyone, background):
    """The home page's picture: the statewide total, with the ten deadliest polluters lit up."""
    img = background.copy()
    for f in sorted(everyone, key=lambda f: -f["deaths"])[:10]:
        if f["lat"] and f["lon"]:
            x, y = map_xy(f["lat"], f["lon"])
            r = 5 + min(12, f["deaths"] ** 0.4 / 2)
            glow(img, (x, y), r * 2, RED, 14, 150)
            ImageDraw.Draw(img).ellipse((x - r, y - r, x + r, y + r), fill=RED)
    total = sum(f["deaths"] for f in everyone)
    text_block(img, "", "Which Indiana polluters are linked to the most deaths?",
               f"{len(everyone):,} power plants, steel mills and factories, mapped",
               deaths_words(total), "estimated deaths a year", True)
    return img


def local_map(img, bbox, everyone, shapes, lit, county=None, center=None):
    """A close-up map in MAP_BOX: county lines, every polluter in view, this place's lit up red."""
    x0, y0, x1, y1 = MAP_BOX
    bw, bh = x1 - x0, y1 - y0
    lon0, lat0, lon1, lat1 = bbox
    k = 0.76                                          # a degree of longitude is shorter than one of latitude
    s = min(bw / ((lon1 - lon0) * k), bh / (lat1 - lat0))
    cx, cy = (lon0 + lon1) / 2, (lat0 + lat1) / 2
    xy = lambda lon, lat: (bw / 2 + (lon - cx) * k * s, bh / 2 - (lat - cy) * s)
    layer = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for name, rings in shapes.items():
        for ring in rings:
            pts = [xy(lon, lat) for lon, lat in ring]
            if name == county:
                d.polygon(pts, fill=(*RED, 28))
            d.line(pts + pts[:1], fill=(*RED, 255) if name == county else (52, 56, 66, 255), width=3 if name == county else 1)
    for f in sorted(everyone, key=lambda f: f["id"] in lit):
        x, y = xy(f["lon"], f["lat"])
        r = 3 + min(14, f["deaths"] ** 0.4 / 1.5)
        if f["id"] in lit:
            glow(layer, (x, y), r * 2, RED, 8, 110)
            ImageDraw.Draw(layer).ellipse((x - r, y - r, x + r, y + r), fill=RED)
        else:
            d.ellipse((x - r, y - r, x + r, y + r), fill=DOT)
    if center:
        x, y = xy(*center)
        r = 10 / 69 * s                               # the 10-mile circle
        ImageDraw.Draw(layer).ellipse((x - r, y - r, x + r, y + r), outline=INK, width=3)
        ImageDraw.Draw(layer).ellipse((x - 5, y - 5, x + 5, y + 5), fill=INK)
    img.alpha_composite(layer, (x0, y0))


def place_image(p, everyone, _unused, shapes, background):
    """A county or town: a close-up map with its polluters lit up."""
    img = background.copy()
    if p.get("county"):
        pts = [pt for ring in shapes[p["county"]] for pt in ring]
        lons, lats = [x for x, _ in pts], [y for _, y in pts]
        pad = max(max(lons) - min(lons), max(lats) - min(lats)) * 0.25
        bbox = (min(lons) - pad, min(lats) - pad, max(lons) + pad, max(lats) + pad)
    else:
        lon, lat = p["center"]
        bbox = (lon - 18 / 53, lat - 18 / 69, lon + 18 / 53, lat + 18 / 69)   # 18 miles each way
    local_map(img, bbox, everyone, shapes, set(p["lit"]), p.get("county"), p.get("center"))
    text_block(img, "", p["title"], p["sub"], p["value"], p["label"], not p.get("quiet"))
    return img


def save(img, path):
    # 256 colors is plenty for these flat pictures and keeps each file small
    img.convert("RGB").quantize(256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(path, optimize=True)


def main():
    everyone = load()
    OUT.mkdir(parents=True, exist_ok=True)
    places = json.loads(PLACES.read_text(encoding="utf-8"))
    keep = {f"{f['slug']}.png" for f in everyone} | {f"{p['image']}.png" for p in places} | {"home.png"}
    for old in OUT.glob("*.png"):           # drop images of polluters that were renamed or removed
        if old.name not in keep:
            old.unlink()
    background = base(everyone)
    save(home_image(everyone, background), OUT / "home.png")
    for i, f in enumerate(everyone, 1):
        save(polluter_image(f, background), OUT / f"{f['slug']}.png")
        if i % 200 == 0:
            print(f"  {i:,} of {len(everyone):,}")
    shapes, plain = counties(), base(everyone, statewide=False)
    for p in places:
        save(place_image(p, everyone, None, shapes, plain), OUT / f"{p['image']}.png")
    total = sum(p.stat().st_size for p in OUT.glob("*.png"))
    print(f"Wrote {len(everyone) + len(places) + 1:,} share images ({total / 1e6:.0f} MB) to {OUT.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
