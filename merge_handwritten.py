"""Merge the vision transcriptions of handwritten pages (data/pages/out/*.jsonl) into flat tables,
and attach room locations to the typed asbestos TEM readings via the chain-of-custody forms.

    python merge_handwritten.py      # -> data/custody.jsonl, data/plm_bulk.jsonl, data/readings_located.jsonl
"""
import collections, datetime as dt, glob, json, re
from parse_reports import canon_school

OUT_DIR = "data/pages/out"


def norm_sample(s):
    if s is None: return None
    s = str(s).strip().upper()
    if "BLANK" in s: return "BLANK"
    m = re.search(r"(\d+)\s*$", s); return str(int(m.group(1))) if m else s


def load_kind(prefix):
    rows = []
    for p in sorted(glob.glob(f"{OUT_DIR}/{prefix}_*.jsonl")):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line: rows.append(json.loads(line))
    return rows


def merge_custody():
    pages = load_kind("chain_custody"); out = []
    for pg in pages:
        school = canon_school(pg.get("project_name") or pg.get("project_address"), pg.get("project_no"))
        for r in pg.get("rows", []):
            out.append({"file": pg["file"], "page": pg["page"], "form": pg.get("form") or ("air" if pg.get("method") in ("TEM", "PCM", "both") else "bulk"),
                        "school": school, "school_raw": pg.get("project_name"), "project_no": pg.get("project_no"), "date": pg.get("date"),
                        "method": pg.get("method"), "type": pg.get("type"), "legibility": pg.get("legibility"),
                        "sample_id": r.get("sample_id"), "sample_norm": norm_sample(r.get("sample_id")), "location": r.get("location"),
                        "time_start": r.get("time_start"), "time_end": r.get("time_end"), "total_min": r.get("total_min"),
                        "flow_avg": r.get("flow_avg"), "air_vol_L": r.get("air_vol_L"), "result": r.get("conc")})
    with open("data/custody.jsonl", "w", encoding="utf-8") as f:
        for r in out: f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"custody: {len(pages)} pages, {len(out)} rows, forms {collections.Counter(r['form'] for r in out)}, "
          f"schools {collections.Counter(r['school'] for r in out).most_common(6)}")
    return out


def merge_plm():
    pages = load_kind("plm_bulk"); out = []
    for pg in pages:
        school = canon_school(pg.get("client_project"), pg.get("project_no"))
        for r in pg.get("rows", []):
            out.append({"file": pg["file"], "page": pg["page"], "school": school, "school_raw": pg.get("client_project"), "project_no": pg.get("project_no"),
                        "analysis_date": pg.get("analysis_date"), "batch_no": pg.get("batch_no"), "legibility": pg.get("legibility"), **r})
    with open("data/plm_bulk.jsonl", "w", encoding="utf-8") as f:
        for r in out: f.write(json.dumps(r, ensure_ascii=False) + "\n")
    if out:
        det = collections.Counter(str(r.get("asbestos_detected")).lower() for r in out)
        print(f"plm: {len(pages)} pages, {len(out)} samples, asbestos_detected {det}")
    return out


def attach_locations(custody):
    """TEM reading -> custody row: same school, same sample number, air volume within 12 L, custody date within 14 days."""
    idx = collections.defaultdict(list)
    for c in custody:
        if c["form"] != "air" or not c["school"] or c["sample_norm"] in (None, "BLANK"): continue
        idx[(c["school"], c["sample_norm"])].append(c)
    n = hit = 0; out = []
    for line in open("data/readings.jsonl", encoding="utf-8"):
        r = json.loads(line)
        if r["template"] == "asbestos_tem_air" and r["school"] and not r.get("location"):
            n += 1; best = None
            for c in idx.get((r["school"], norm_sample(r["sample"])), []):
                dv = abs((c["air_vol_L"] or 0) - (r["extra"].get("vol_L") or -1))
                try: dd = abs((dt.date.fromisoformat(c["date"]) - dt.date.fromisoformat(r["date"])).days) if c["date"] and r["date"] else 99
                except ValueError: dd = 99
                score = (dv > 12) * 100 + dd
                if dv <= 12 and dd <= 14 and (best is None or score < best[0]): best = (score, c)
            if best:
                hit += 1; c = best[1]
                r["location"] = c["location"]; r["extra"]["custody_doc"] = f"{c['file']}#page={c['page']}"; r["extra"]["custody_date"] = c["date"]
        out.append(r)
    with open("data/readings_located.jsonl", "w", encoding="utf-8") as f:
        for r in out: f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"TEM readings without location: {n}; matched to a custody row: {hit}")


if __name__ == "__main__":
    c = merge_custody(); merge_plm(); attach_locations(c)
