"""Build static site data from data/docs.jsonl.

    python build_site.py            # writes site/data/*.json (geocodes new DEP folders, cached)

Outputs
  site/data/tree.json            agency -> box -> folder counts (finding aid skeleton)
  site/data/docs/<agency>.json   doc lists per agency, keyed by box|folder (DOE count sheets collapsed)
  site/data/buildings.json       building folders geocoded (NYC GeoSearch, free) with doc counts
  site/data/schools.json         DOE lab testing timeline (count sheets per week) + ATC project index
"""
import collections, datetime as dt, html, json, re, time
from pathlib import Path
import requests

DOCS = Path("data/docs.jsonl"); SITE = Path("docs/data"); CACHE = Path("data/geocode_cache.json")
GEO = "https://geosearch.planninglabs.nyc/v2/search"


def slug(s): return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def clean(c): return html.unescape(re.sub(r"<[^>]+>", "", c or "")).replace("\r", "")


def snippet(c, n=160):
    t = re.sub(r"\s+", " ", clean(c)).replace("NYC 9/11 Public Portal Document", "").strip()
    return t[:n]


def load():
    for line in DOCS.open(encoding="utf-8"):
        r = json.loads(line)
        r["box_name"] = html.unescape(r["box_name"] or ""); r["folder_name"] = html.unescape(r["folder_name"] or "")
        yield r


# ---------- finding aid ----------
def is_count_sheet(r): return r["agency"].startswith("Education") and "Count S" in r["content"]


def build_tree(rows):
    tree = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    docs = collections.defaultdict(lambda: collections.defaultdict(list))
    sheets = collections.Counter()
    for r in rows:
        box = r["box_name"] or "(no box)"; folder = r["folder_name"] or "(no folder)"
        if is_count_sheet(r):
            box, folder = "Lab results", "TEM asbestos air count sheets"; sheets[r["agency"]] += 1
            tree[r["agency"]][box][folder] += 1
            continue
        tree[r["agency"]][box][folder] += 1
        docs[r["agency"]][f"{box}|{folder}"].append(
            {"t": r["title"], "p": int(r["page_count"] or 0), "v": r["production_volume"], "s": snippet(r["content"])})
    out = {"agencies": []}
    for ag, boxes in sorted(tree.items(), key=lambda kv: -sum(sum(f.values()) for f in kv[1].values())):
        n = sum(sum(f.values()) for f in boxes.values())
        out["agencies"].append({"name": ag, "slug": slug(ag), "n": n, "boxes": [
            {"name": b, "n": sum(f.values()),
             "folders": [{"name": fn, "n": c} for fn, c in sorted(f.items(), key=lambda kv: -kv[1])]}
            for b, f in sorted(boxes.items(), key=lambda kv: (kv[0] == "(no box)", -sum(kv[1].values())))]})
    (SITE / "docs").mkdir(parents=True, exist_ok=True)
    (SITE / "tree.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    for ag, groups in docs.items():
        for g in groups.values(): g.sort(key=lambda d: d["t"])
        (SITE / "docs" / f"{slug(ag)}.json").write_text(json.dumps(groups, ensure_ascii=False), encoding="utf-8")
    print(f"tree: {len(out['agencies'])} agencies, {sum(a['n'] for a in out['agencies'])} docs, "
          f"{sum(sheets.values())} count sheets collapsed")


# ---------- building map ----------
ADDR = re.compile(r"^\s*(\d+(?:-\d+)?)\s+((?:[A-Za-z.']+\s?){1,4}?)(?=\s*(?:Block|BIN|BN#|\d{7}|\d+ OF \d+|[;,(]|$))", re.I)
BLOCK = re.compile(r"Block:?\s*(\d+)\s*Lot:?\s*(\d+)|\b(\d{7}),?\s*(\d+)/(\d+)", re.I)
BIN_ = re.compile(r"\b(1\d{6})\b")
STOP = {"of", "x", "a", "up", "to", "ex", "extract", "scan", "xtract", "originals"}


def parse_folder(name):
    m = ADDR.match(name)
    if not m: return None
    street = m.group(2).strip().rstrip(".,;")
    if len(street) < 3 or street.split()[0].lower() in STOP: return None
    b = BLOCK.search(name); bin_ = BIN_.search(name)
    return {"addr": f"{m.group(1)} {street.title()}", "bin": bin_.group(1) if bin_ else None,
            "block": (b.group(1) or b.group(4)) if b else None, "lot": (b.group(2) or b.group(5)) if b else None}


def geocode(addr, cache):
    if addr in cache: return cache[addr]
    try:
        r = requests.get(GEO, params={"text": addr + ", Manhattan, NY", "size": 1}, timeout=20); r.raise_for_status()
        f = r.json().get("features") or []
        hit = f[0] if f and f[0]["properties"].get("borough") == "Manhattan" else None
        cache[addr] = None if not hit else {
            "lon": hit["geometry"]["coordinates"][0], "lat": hit["geometry"]["coordinates"][1],
            "label": hit["properties"]["label"],
            "bin": (hit["properties"].get("addendum") or {}).get("pad", {}).get("bin")}
    except Exception as e:  # noqa: BLE001
        print("  geocode fail", addr, e); return None
    time.sleep(0.15); return cache[addr]


def number_ok(addr, label):
    """GeoSearch snaps unknown numbers to the nearest address; keep only exact or range-end matches."""
    want = addr.split()[0].split("-"); got = label.split()[0]
    return got in want


def build_map(rows):
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    per = {}
    for r in rows:
        p = parse_folder(r["folder_name"])
        if not p: continue
        d = per.setdefault(p["addr"], {"n": 0, "folders": collections.Counter(), "boxes": collections.Counter(),
                                       "agencies": collections.Counter(), "bin": p["bin"], "block": p["block"], "lot": p["lot"]})
        d["n"] += 1; d["folders"][r["folder_name"]] += 1; d["boxes"][r["box_name"]] += 1; d["agencies"][r["agency"]] += 1
    feats, miss = [], []
    for addr, d in per.items():
        g = geocode(addr, cache)
        if not g or not number_ok(addr, g["label"]): miss.append(addr); continue
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [g["lon"], g["lat"]]},
                      "properties": {"addr": addr, "label": g["label"], "n": d["n"], "bin": d["bin"] or g.get("bin"),
                                     "block": d["block"], "lot": d["lot"],
                                     "folders": d["folders"].most_common(), "boxes": d["boxes"].most_common(),
                                     "agencies": d["agencies"].most_common()}})
    CACHE.write_text(json.dumps(cache))
    (SITE / "buildings.json").write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False), encoding="utf-8")
    print(f"map: {len(feats)} buildings geocoded, {len(miss)} unresolved {miss[:8]}, "
          f"{sum(f['properties']['n'] for f in feats)} docs pinned")


# ---------- DOE school testing ----------
DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(0[1-4]|200[1-4])\b")
PROJECT = re.compile(r"Project:\s*(.+)")
SCHOOL = re.compile(r"\b(P\.? ?S\.?|I\.? ?S\.?|M\.? ?S\.?|H\.? ?S\.?|J\.? ?H\.? ?S\.?|School|Academy|High|Stuyvesant|BOE)\b", re.I)
ANALYST = re.compile(r"Analysis Performed By:\s*\n?\s*([A-Z][A-Z .]{3,40})")


def build_schools(rows):
    weeks, analysts = collections.Counter(), collections.Counter()
    projects = collections.defaultdict(lambda: {"n": 0, "docs": [], "dates": set(), "name": None})
    for r in rows:
        if not r["agency"].startswith("Education"): continue
        txt = clean(r["content"]); dates = []
        for m in DATE.finditer(txt):
            mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3)[-2:]) + 2000
            try:
                x = dt.date(y, mo, d)
                if dt.date(2001, 9, 11) <= x <= dt.date(2004, 12, 31): dates.append(x)
            except ValueError: pass
        if is_count_sheet(r) and dates:
            dd = min(dates); wk = dd - dt.timedelta(days=dd.weekday()); weeks[wk.isoformat()] += 1
            a = ANALYST.search(txt)
            if a: analysts[a.group(1).strip().title()] += 1
        pm = PROJECT.search(txt)
        if pm and SCHOOL.search(pm.group(1)):
            name = re.sub(r"\s+", " ", pm.group(1)).strip(" .,'\"")[:90]
            key = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
            p = projects[key]; p["n"] += 1; p["name"] = p["name"] or name
            p["docs"].append({"t": r["title"], "p": int(r["page_count"] or 0)})
            p["dates"].update(d.isoformat() for d in dates)
    out = {"weeks": sorted(weeks.items()), "analysts": analysts.most_common(12),
           "projects": sorted([{"name": p["name"], "n": p["n"], "first": min(p["dates"]) if p["dates"] else None,
                                "last": max(p["dates"]) if p["dates"] else None, "docs": p["docs"][:40]}
                               for p in projects.values()], key=lambda x: -x["n"])}
    (SITE / "schools.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"schools: {sum(weeks.values())} dated count sheets over {len(weeks)} weeks, {len(out['projects'])} projects")


if __name__ == "__main__":
    rows = list(load()); print(len(rows), "docs loaded")
    build_tree(rows); build_map(rows); build_schools(rows)
