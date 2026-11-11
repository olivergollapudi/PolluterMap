"""
Indiana's 92 counties: their outlines (Census cartographic boundaries, 1:500,000), and which
county a point falls in. Used to give every polluter and town a county, and to draw county
outlines in share images.

The Census publishes the outlines as KML (an XML format), so Python's own XML reader is enough.
"""

import math
import re
import zipfile
import xml.etree.ElementTree as ET

from common import RAW_DIR, download

COUNTIES_URL = "https://www2.census.gov/geo/tiger/GENZ2023/kml/cb_2023_us_county_500k.zip"
COUNTIES_FILE = RAW_DIR / "census" / "cb_2023_us_county_500k.zip"
INDIANA = "18"
KML = "{http://www.opengis.net/kml/2.2}"


def counties():
    """{county name: [ring, ...]} for Indiana, each ring a list of (lon, lat)."""
    download(COUNTIES_URL, COUNTIES_FILE)
    with zipfile.ZipFile(COUNTIES_FILE) as z:
        name = next(n for n in z.namelist() if n.endswith(".kml"))
        root = ET.fromstring(z.read(name))
    out = {}
    for pm in root.iter(KML + "Placemark"):
        data = {d.get("name"): (d.text or "") for d in pm.iter(KML + "SimpleData")}
        if data.get("STATEFP") != INDIANA:
            continue
        rings = []
        for poly in pm.iter(KML + "Polygon"):
            outer = poly.find(f"{KML}outerBoundaryIs/{KML}LinearRing/{KML}coordinates")
            rings.append([tuple(map(float, p.split(",")[:2])) for p in outer.text.split()])
        out[data["NAME"]] = rings
    if len(out) != 92:
        raise SystemExit(f"ERROR: expected Indiana's 92 counties in the Census file, found {len(out)}")
    return out


def _inside(lon, lat, ring):
    hit = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


def county_of(lon, lat, shapes):
    """The county a point is in; a point just outside the state (a plant on the border, a rounded
    location) gets the nearest county."""
    for name, rings in shapes.items():
        if any(_inside(lon, lat, r) for r in rings):
            return name
    return min(shapes, key=lambda n: min(miles(lat, lon, y, x) for r in shapes[n] for x, y in r[::5]))


def miles(lat1, lon1, lat2, lon2):
    """Distance along the ground, in miles."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(a))


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower().replace("'", "")).strip("-")
