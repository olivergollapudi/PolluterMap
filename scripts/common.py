"""
Shared helpers for the build scripts: paths, downloading, reading Excel files,
and writing tables into the shared Indiana database.

Every build script writes its own tables into ONE database,
data/processed/indiana.sqlite, so you can query across sources (e.g. join a
facility's violations to its toxic releases). Each script only replaces its own
tables, so they can be re-run independently and in any order.
"""

import csv
import re
import sqlite3
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import date
from pathlib import Path

STATE = "IN"

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
PROCESSED_DIR = ROOT / "data" / "processed"
DB_PATH = PROCESSED_DIR / "indiana.sqlite"


# ---------------------------------------------------------------- downloading

def download(url, dest, force=False):
    """Save url to dest, unless dest already exists (force=True re-downloads)."""
    if dest.exists() and not force:
        print(f"Using existing download: {dest.relative_to(ROOT)}  (use --force to re-download)")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    print(f"Downloading {url}")
    # census.gov rejects Python's default user agent, so identify as a normal client.
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (PolluterMap data script)"})
    with urllib.request.urlopen(req) as resp, open(tmp, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while chunk := resp.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  {done * 100 // total:3d}% of {total / 1e6:.0f} MB", end="", flush=True)
    tmp.replace(dest)  # only rename once complete, so a half download is never mistaken for a real one
    print("\n  done.")


# ---------------------------------------------------------------- Excel (.xlsx)

_M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def read_xlsx_rows(path, sheet_name=None):
    """Yield each row of one sheet as {column letter: text}, e.g. {"A": "Facility Id", "B": ...}.

    An .xlsx file is a zip of XML files, so the standard library can read it with
    no extra packages. sheet_name=None reads the first sheet.
    """
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            # Text cells store an index into this shared list of strings.
            shared = ["".join(t.text or "" for t in si.iter(_M + "t"))
                      for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(_M + "si")]
        rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
        sheets = {s.get("name"): rels[s.get(_R + "id")] for s in ET.fromstring(z.read("xl/workbook.xml")).iter(_M + "sheet")}
        if sheet_name is None:
            sheet_name = next(iter(sheets))
        if sheet_name not in sheets:
            raise KeyError(f"{path.name} has no sheet {sheet_name!r}; sheets are {list(sheets)}")
        target = sheets[sheet_name].lstrip("/")
        target = target if target.startswith("xl/") else "xl/" + target
        root = ET.fromstring(z.read(target))

    for row in root.iter(_M + "row"):
        cells = {}
        for c in row.findall(_M + "c"):
            col = re.match(r"[A-Z]+", c.get("r")).group()
            if c.get("t") == "inlineStr":
                cells[col] = "".join(x.text or "" for x in c.iter(_M + "t"))
                continue
            v = c.find(_M + "v")
            if v is not None:
                cells[col] = shared[int(v.text)] if c.get("t") == "s" else v.text
        yield cells


# ---------------------------------------------------------------- output

def write_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"  {path.relative_to(ROOT)}  ({path.stat().st_size / 1e6:.1f} MB, {len(rows):,} rows)")


def connect():
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS metadata (source TEXT, key TEXT, value TEXT, PRIMARY KEY (source, key))")
    return con


def replace_table(con, name, columns, rows, indexes=()):
    """Drop and recreate one table. columns = [(column_name, sql_type), ...]; rows = list of dicts.
    indexes = SQL fragments like "UNIQUE (registry_id)" or "(year)"."""
    con.execute(f"DROP TABLE IF EXISTS {name}")
    con.execute(f"CREATE TABLE {name} ({', '.join(f'{c} {t}' for c, t in columns)})")
    cols = [c for c, _ in columns]
    con.executemany(f"INSERT INTO {name} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                    ([r.get(c) for c in cols] for r in rows))
    for i, spec in enumerate(indexes):
        unique = "UNIQUE " if spec.startswith("UNIQUE") else ""
        con.execute(f"CREATE {unique}INDEX idx_{name}_{i} ON {name} {spec.removeprefix('UNIQUE ').strip()}")


def set_metadata(con, source, values):
    """Record where a source's data came from, e.g. its URL and data date."""
    con.execute("DELETE FROM metadata WHERE source = ?", (source,))
    values = {**values, "built_on": date.today().isoformat()}
    con.executemany("INSERT INTO metadata VALUES (?, ?, ?)", [(source, k, str(v)) for k, v in values.items()])
