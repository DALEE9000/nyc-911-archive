"""Build static site data from data/docs.jsonl.

    python build_site.py            # writes site/data/*.json (geocodes new DEP folders, cached)

Outputs
  site/data/tree.json            agency -> box -> folder counts (navigator skeleton)
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


# ---------- navigator ----------
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


# ---------- lab readings (from parse_reports.py) ----------
READINGS = Path("data/readings.jsonl")
GUIDELINES = {  # analyte -> (value, unit, label) drawn as a reference line on the chart
    "asbestos": (0.01, "S/cc", "typical lab sensitivity ~0.004–0.01 S/cc; AHERA clearance is 70 S/mm²"),
    "dioxin_TCDD_eq": (25, "ng/m2", "NYSDOH surface guideline 25 ng/m²"),
    "CO2": (1000, "ppm", "ASHRAE 62: outdoor + 700 ppm (≈1,000–1,100)"),
    "CO": (9, "ppm", "EPA NAAQS / ASHRAE 9 ppm"),
    "PM2.5": (65, "ug/m3", "EPA 24-h NAAQS 65 µg/m³ (40 for sensitive groups)"),
    "TVOC": (1, "ppm", "AIHA 1 ppm"),
    "lead": (40, "ug/ft2", "HUD/NYC floor clearance 40 µg/ft² (air samples: OSHA action 30 µg/m³)"),
    "temperature": (79, "F", "ASHRAE 55 summer 73–79 °F"), "relative_humidity": (60, "%", "ASHRAE 55 30–60 %"),
    "asbestos_bulk": (1, "%", "NYC/EPA: material is asbestos-containing at >1 %"),
}


def handwritten_rows():
    """Readings transcribed from handwritten pages (merge_handwritten.py): settled-dust fiberglass/asbestos
    results and lead wipes from chain-of-custody forms, and PLM bulk asbestos sheets."""
    out = []
    p = Path("data/custody.jsonl")
    if p.exists():
        for line in p.open(encoding="utf-8"):
            c = json.loads(line)
            if c["form"] != "bulk" or not c.get("result"): continue
            res = str(c["result"]).strip().upper(); base = {"file": c["file"], "page": c["page"], "doc": None, "school": c["school"], "date": c["date"],
                                                           "sample": c["sample_id"], "location": c["location"], "template": "custody_bulk",
                                                           "extra": {"legibility": c["legibility"], "handwritten": True}}
            m = re.match(r"^(<?)\s*(\d+(?:\.\d+)?)", res)
            if "UG" in res or (m and float(m.group(2)) >= 1):            # lead dust wipe, µg/ft²
                if not m: continue
                out.append({**base, "analyte": "lead", "unit": "ug/ft2", "qual": m.group(1), "value": float(m.group(2))})
            elif "CHRYS" in res or "ASB" in res:
                out.append({**base, "analyte": "dust_asbestos_plm", "unit": "PLM", "qual": "", "value": 1.0, "extra": {**base["extra"], "result_text": res}})
            elif res.startswith(("ND", "NONE", "NO FIB", "N.D")):
                out.append({**base, "analyte": "dust_fiberglass", "unit": "PLM", "qual": "<", "value": 0.1})
            elif res.startswith(("TR", "TRACE")):
                out.append({**base, "analyte": "dust_fiberglass", "unit": "PLM", "qual": "", "value": 0.5, "extra": {**base["extra"], "result_text": res}})
    p = Path("data/plm_bulk.jsonl")
    if p.exists():
        for line in p.open(encoding="utf-8"):
            s = json.loads(line); det = str(s.get("asbestos_detected")).lower() == "yes"
            v = s.get("chrysotile_pct") or s.get("amosite_pct")
            out.append({"file": s["file"], "page": s["page"], "doc": None, "school": s["school"], "date": s.get("analysis_date"),
                        "sample": s.get("field_no"), "location": s.get("sample_id"), "template": "plm_bulk_sheet", "analyte": "asbestos_bulk",
                        "unit": "%", "qual": "" if det else "<", "value": (v if det and v else (1.0 if det else 0.1)),
                        "extra": {"legibility": s.get("legibility"), "handwritten": True, "cellulose_pct": s.get("cellulose_pct"),
                                  "fiberglass_pct": s.get("fiberglass_pct"), "other_fibrous": s.get("other_fibrous")}})
    for r in out:
        d = str(r["date"] or "")
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", d) or d < "2001-09-11":   # pre-attack dates are transcription/writer slips
            if d: r["extra"]["date_as_written"] = d
            r["date"] = None
    return out


def build_readings():
    if not READINGS.exists(): print("no readings.jsonl; skip"); return
    out, reports, seen_keys, dup = [], set(), set(), {}
    src = Path("data/readings_located.jsonl") if Path("data/readings_located.jsonl").exists() else READINGS
    rows_in = [json.loads(l) for l in src.open(encoding="utf-8")]
    rows_in += handwritten_rows()
    for r in rows_in:
        reports.add(r["file"])
        e = r.get("extra", {})
        if r["template"] == "metals_air" and r["analyte"] == "lead": r["analyte"] = "lead_metals_panel"
        if r["analyte"] == "asbestos" and r["value"] == 0 and not r["qual"]: r["qual"] = "<"; r["value"] = e.get("sensitivity_S_cc") or 0.0044
        if r["location"]: r["location"] = re.sub(r"(?<=\d)[^\w\s,/()&#.-]{1,3}", "", r["location"]).replace("Mam ", "Main ").strip(" ,")
        # the portal holds several scanned copies of many reports; keep one per (school, date, analyte, location/sample, value)
        key = (r["school"], r["date"], r["analyte"], (r["location"] or "").lower().replace(" ", ""), r["sample"], r["value"], r["qual"])
        if key in seen_keys: dup[key] = dup.get(key, 1) + 1; continue
        seen_keys.add(key)
        out.append({"s": r["school"] or "Unknown", "d": r["date"], "t": r["template"], "a": r["analyte"], "v": r["value"], "q": r["qual"],
                    "u": r["unit"], "l": r["location"], "n": r["sample"], "f": r["file"], "p": r["page"], "doc": r["doc"],
                    "x": {k: e[k] for k in ("min", "max", "outdoor_min", "outdoor_max", "type", "n_05_5", "n_gt5", "vol_L", "blank",
                                            "ocr_corrected_from", "outside_narrative_range", "detection_limit", "custody_doc", "legibility",
                                            "handwritten", "result_text", "cellulose_pct", "fiberglass_pct") if k in e and e[k] not in (None, False)},
                    **({"inf": 1} if r.get("school_inferred") else {})})
    analytes = collections.Counter(r["a"] for r in out)
    (SITE / "readings.json").write_text(json.dumps({"n_reports": len(reports), "guidelines": GUIDELINES, "analytes": analytes.most_common(),
                                                   "rows": out}, ensure_ascii=False), encoding="utf-8")
    print(f"readings: {len(out)} unique rows ({sum(dup.values()) - len(dup)} duplicate copies dropped) from {len(reports)} reports; analytes {analytes.most_common(8)}")


if __name__ == "__main__":
    rows = list(load()); print(len(rows), "docs loaded")
    build_tree(rows); build_map(rows); build_schools(rows); build_readings()
