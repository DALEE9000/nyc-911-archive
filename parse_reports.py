"""Extract readings from the typed tables in the DOE/ATC reports (data/reports/*.pdf).

    python parse_reports.py          # -> data/readings.jsonl, data/readings_coverage.json

Uses the city's existing text layer, re-ordered by word coordinates into lines (the raw layer
scrambles table order). One record per sample x analyte:
  file, page, doc (portal id of that page), template, school, project_no, date, sample, location,
  analyte, value, qual ('<' below detection / 'BDL' / ''), unit, extra
Handwritten pages (field reports, chain of custody, PLM bulk sheets) are out of scope here.
"""
import collections, json, os, re
import pymupdf

REPORTS = "data/reports"; OUT = "data/readings.jsonl"; COV = "data/readings_coverage.json"

METALS = "Aluminum|Cadmium|Chromium|Cobalt|Copper|Iron|Lead|Magnesium|Manganese|Molybdenum|Nickel|Zinc"
RX = {
    "tem": re.compile(r"^(\S{1,8})\s+(\d{3,5}(?:\.\d+)?)\s+(0\.\d{3,4})\s+(\d+)\s+(.*?)\s+(\d+)\s+(\d+)\s+(\d\.\d{4})\s+(<?\s?\d+\.\d+)\s+(<?\s?\d+\.\d+)"),
    "dioxin": re.compile(r"^(.*?[A-Za-z].*?)\s+(\d+\.\d+)\s*(?:ng/m\S*)?\s*$"),
    "dioxin_range": re.compile(r"ranged from\s+(\d+\.\d+)\s+to\s+(\d+\.\d+)\s+nanograms", re.I),
    "iaq_head": re.compile(r"^(Temperature|Relative Humidity|Carbon Dioxide|Carbon Monoxide|Respirable Particulate|T\S*OC Concentration|Total Volatile)", re.I),
    "iaq_range": re.compile(r"(?:ranged?|range was|were|was)\s+(?:from\s+|between\s+)?(-?\d+(?:[.,]\d+)?)\s*(?:°F|ppm|percent|%|[μµup]g/m\S*)?\s*(?:to|and|-)\s+(-?\d+(?:[.,]\d+)?)", re.I),
    "iaq_single": re.compile(r"(?:was|were)\s+(\d+(?:\.\d+)?)\s*(?:°F|ppm|percent|%|[μµup]g/m\S*)\s+for all", re.I),
    "iaq_n": re.compile(r"\((\d+)\)\s+(?:sets of )?(?:real-time )?(?:environmental )?measurements", re.I),
    "iaq_above": re.compile(r"\((\d+)\)\s+(?:indoor\s+)?(?:measurements|readings|CO\S*\s+measurements)\s+(?:for \w+ )?were above", re.I),
    "mercury": re.compile(r"^(Hg[-\s]?\w*)\s+(\d\.\d+)\s+(\d+)\s+(\d\.\d+)\s+(\*{0,2}BDL|\d\.\d+)"),
    "lead": re.compile(r"^([A-Za-z0-9-]{2,8})\s+(\d{5})\s+(.+?)\s+(<?\s?\d+\.\d+)\s*(?:ug)?\s+(\d+\.\d+)\s*(?:ug)?\s*$"),
    "metal": re.compile(rf"^\W?({METALS})\W?\s+(<?\s?\d[\d.,]*)\s+(<?\s?\d[\d.,-]*)"),
    "client": re.compile(r"Client ID[:;.]?\s*(\S+)\s+Lab ID.*?Date Sampled[:;]?\s*(\S+).*?Air Vol\S*\s*(\S+)", re.I),
    "pm": re.compile(r"24-hour average concentration of PM\S*\s+(?:in|for)\s+(?:the\s+)?(.+?)\s+was\s+(\d+(?:\.\d+)?)\s*[μµu]g/m", re.I),
    "project_no": re.compile(r"Project No\.?\s*[:;]?\s*(\d{2}-\d{5}-\d{3,4})|PROJECT NO\.?\s*(\d{5}-\d{3,4})|Client Reference:\s*(19125-\d{4})|Project ?#\s*[:;]?\s*(14584-\d{3,4})"),
    "project_line": re.compile(r"Project:\s*(.+)"),
    "re_line": re.compile(r"^Re:\s*(.+)"),
    "date_long": re.compile(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s*(200[1-4])\b"),
    "date_short": re.compile(r"\b(\d{1,2})/(\d{1,2})/(0[1-4]|200[1-4])\b"),
    "analysis_date": re.compile(r"Analysis Date:\s*(\d{1,2}/\d{1,2}/\d{2,4})"),
    "footer": re.compile(r"NYC-WTC_(\d{9})"),
}
MONTHS = {m: i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"], 1)}


def page_lines(pg):
    rows = collections.defaultdict(list)
    for x0, y0, x1, y1, s, *_ in pg.get_text("words"):
        rows[round(y0 / 6)].append((x0, s))
    return [" ".join(s for x, s in sorted(rows[k])) for k in sorted(rows)]


def iso(m):
    if m.lastindex == 3 and m.group(1) in MONTHS:
        return f"{m.group(3)}-{MONTHS[m.group(1)]:02d}-{int(m.group(2)):02d}"
    mo, d, y = m.group(1), m.group(2), m.group(3)
    return f"{'20' + y[-2:]}-{int(mo):02d}-{int(d):02d}"


def norm_date(s):
    m = RX["date_long"].search(s) or RX["date_short"].search(s)
    return iso(m) if m else None


def qual_val(tok):
    tok = tok.replace(" ", "").replace(",", ".")
    if "BDL" in tok: return "BDL", None
    q = "<" if tok.startswith("<") else ""
    try: return q, float(tok.lstrip("<").rstrip("-"))
    except ValueError: return q, None


def parse_file(path):
    d = pymupdf.open(path); recs = []
    ctx = {"school": None, "project_no": None, "date": None, "sample_date": None}
    all_lines = []
    for pi, pg in enumerate(d):
        lines = page_lines(pg); all_lines.append(lines)
        txt = "\n".join(lines)
        m = RX["project_no"].search(txt)
        if m and not ctx["project_no"]:
            ctx["project_no"] = m.group(1) or m.group(2) or (m.group(3) and "15-" + m.group(3)) or m.group(4)
        for i, l in enumerate(lines):
            if RX["re_line"].match(l) and not ctx["school"] and i + 1 < len(lines):
                nxt = lines[i + 1]
                if not RX["project_no"].search(nxt) and len(nxt) < 70: ctx["school"] = nxt.strip()
        m = RX["project_line"].search(txt)
        if m and not ctx["school"]: ctx["school"] = m.group(1).strip()[:80]
        if not ctx["date"]:
            ctx["date"] = norm_date(txt)
    for pi, lines in enumerate(all_lines):
        foot = RX["footer"].findall("\n".join(lines)); doc = f"NYC-WTC_{foot[-1]}" if foot else None
        base = {"file": os.path.basename(path), "page": pi + 1, "doc": doc, "school": ctx["school"], "project_no": ctx["project_no"]}
        txt = "\n".join(lines)
        adate = RX["analysis_date"].search(txt)
        sect, sdate, client = None, None, None
        for i, l in enumerate(lines):
            if "DIOXIN WIPE SAMPLE RESULTS" in l: sect = "dioxin"; sdate = None; continue
            if "Blank-Corrected" in l and "Hg" in l: sect = "mercury"; sdate = None; continue
            if "ANALYSIS RESULTS" in l and "LEAD" in txt: sect = "lead"; continue
            if "Analytical Results" in l or "A.nalytical Results" in l: sect = "metals"; continue
            if "Asbestos Fiber Analysis" in l or "(S/cc)" in l: sect = "tem"; continue
            if sect in ("dioxin", "mercury") and len(l) < 40:
                nd = norm_date(l)
                if nd: sdate = nd; continue
            if sect == "dioxin":
                if l.startswith("The New York State") or "NYSDOH" in l: sect = None; continue
                m = RX["dioxin"].match(l)
                if m and "CONCENTRATION" not in l.upper() and "EQUIVALENTS" not in l.upper():
                    loc = re.sub(r"\s*/\s*DX\S*$", "", m.group(1)).strip(" _/")
                    recs.append({**base, "template": "dioxin_wipe", "date": sdate or ctx["date"], "sample": None, "location": loc,
                                 "analyte": "dioxin_TCDD_eq", "value": float(m.group(2)), "qual": "", "unit": "ng/m2",
                                 "extra": {"blank": "blank" in loc.lower()}})
            elif sect == "mercury":
                m = RX["mercury"].match(l)
                if m:
                    q, v = qual_val(m.group(5)); loc = lines[i + 1] if i + 1 < len(lines) and not RX["mercury"].match(lines[i + 1]) else None
                    recs.append({**base, "template": "mercury_air", "date": sdate or ctx["date"], "sample": m.group(1), "location": loc,
                                 "analyte": "mercury", "value": v, "qual": q, "unit": "ug/m3", "extra": {"hg_ug": float(m.group(2)), "vol_L": int(m.group(3))}})
                if "Below Detection" in l: sect = None
            elif sect == "lead":
                m = RX["lead"].match(l)
                if m:
                    q, v = qual_val(m.group(4)); unit = "ug/ft2" if ("ft" in txt or "wipe" in txt.lower()) else "ug/m3"
                    sd = re.search(r"SAMPLE DATE:?\s*(\d{2}-\d{2}-\d{2})", txt)
                    recs.append({**base, "template": "lead", "date": f"20{sd.group(1)[6:]}-{sd.group(1)[:2]}-{sd.group(1)[3:5]}" if sd else ctx["date"],
                                 "sample": m.group(1), "location": m.group(3), "analyte": "lead", "value": v, "qual": q, "unit": unit,
                                 "extra": {"detection_limit": float(m.group(5))}})
                if l.startswith("NOTE"): sect = None
            elif sect == "metals":
                c = RX["client"].search(l)
                if c: client = {"sample": c.group(1), "date": norm_date(c.group(2)), "vol": c.group(3)}; continue
                m = RX["metal"].match(l)
                if m and client:
                    q, v = qual_val(m.group(3)); qu, ug = qual_val(m.group(2))
                    recs.append({**base, "template": "metals_air", "date": client["date"], "sample": client["sample"], "location": None,
                                 "analyte": m.group(1).lower(), "value": v, "qual": q, "unit": "mg/m3", "extra": {"ug": ug, "vol_L": client["vol"]}})
            elif sect == "tem":
                m = RX["tem"].match(l)
                if m:
                    q, v = qual_val(m.group(10)); _, smm = qual_val(m.group(9))
                    recs.append({**base, "template": "asbestos_tem_air", "date": iso(RX["date_short"].search(adate.group(1))) if adate and RX["date_short"].search(adate.group(1)) else ctx["date"],
                                 "sample": m.group(1), "location": None, "analyte": "asbestos", "value": v, "qual": q, "unit": "S/cc",
                                 "extra": {"vol_L": float(m.group(2)), "area_mm2": float(m.group(3)), "type": m.group(5), "n_05_5": int(m.group(6)), "n_gt5": int(m.group(7)),
                                           "sensitivity_S_cc": float(m.group(8)), "S_mm2": smm, "S_mm2_qual": m.group(9).strip().startswith("<")}})
        for m in RX["pm"].finditer(txt):
            recs.append({**base, "template": "pm_narrative", "date": ctx["date"], "sample": None, "location": m.group(1), "analyte": "PM2.5",
                         "value": float(m.group(2)), "qual": "", "unit": "ug/m3", "extra": {}})
        # dioxin: validate table values against the page's own narrative range; fix dropped-decimal OCR slips
        rng = RX["dioxin_range"].findall(txt)
        if rng:
            lo = min(float(a) for a, b in rng) * 0.9; hi = max(float(b) for a, b in rng) * 1.1
            for r in recs:
                if r["template"] == "dioxin_wipe" and r["page"] == pi + 1 and not r["extra"].get("blank") and not (lo <= r["value"] <= hi):
                    sv = f"{r['value']:.6f}".rstrip("0"); i = next((k for k, ch in enumerate(sv) if ch not in "0."), None)
                    cands = [r["value"] / 10, r["value"] / 100] + ([float(sv[:i] + "0" + sv[i + 1:])] if i is not None else [])
                    fixed = next((c for c in cands if lo <= c <= hi), None)
                    if fixed is not None: r["extra"].update({"ocr_corrected_from": r["value"]}); r["value"] = fixed
                    else: r["extra"]["outside_narrative_range"] = [lo / 0.9, hi / 1.1]
        # dioxin narrative range as fallback when the table was OCR-garbled
        if "DIOXIN WIPE SAMPLE RESULT" in txt and not any(r["template"] == "dioxin_wipe" and r["page"] == pi + 1 for r in recs):
            m = RX["dioxin_range"].search(txt)
            if m:
                recs.append({**base, "template": "dioxin_wipe_range", "date": ctx["date"], "sample": None, "location": "building (range)",
                             "analyte": "dioxin_TCDD_eq", "value": float(m.group(2)), "qual": "", "unit": "ng/m2", "extra": {"min": float(m.group(1)), "max": float(m.group(2))}})
        # indoor-air narrative: one paragraph per parameter, indoor range then outdoor range
        if "Real-Time" in txt or "ranged from" in txt:
            paras, cur = [], None
            for l in lines:
                h = RX["iaq_head"].match(l.strip())
                if h and len(l) < 60: cur = [h.group(1).lower(), ""]; paras.append(cur); continue
                if cur: cur[1] += " " + l
            keymap = {"temperature": ("temperature", "F"), "relative humidity": ("relative_humidity", "%"), "carbon dioxide": ("CO2", "ppm"),
                      "carbon monoxide": ("CO", "ppm"), "respirable particulate": ("PM2.5", "ug/m3"), "total volatile": ("TVOC", "ppm")}
            for head, body in paras:
                key = next((v for k, v in keymap.items() if head.startswith(k)), ("TVOC", "ppm") if "oc concentration" in head else None)
                if not key: continue
                body = body.replace(",", "")
                rngs = RX["iaq_range"].findall(body); single = RX["iaq_single"].search(body)
                nd = re.search(r"not detected[^.]*\(0(?:\.0)? ppm\)", body, re.I)
                vals = []
                if rngs: vals = [(float(a), float(b)) for a, b in rngs]
                elif single: vals = [(float(single.group(1)), float(single.group(1)))]
                elif nd: vals = [(0.0, 0.0)]
                if not vals: continue
                above = RX["iaq_above"].search(body)
                ind, outd = vals[0], (vals[1] if len(vals) > 1 and "outdoor" in body.lower() else None)
                recs.append({**base, "template": "iaq_narrative", "date": ctx["date"], "sample": None, "location": "indoor (range)", "analyte": key[0],
                             "value": max(ind), "qual": "", "unit": key[1],
                             "extra": {"min": min(ind), "max": max(ind), "outdoor_min": outd and min(outd), "outdoor_max": outd and max(outd),
                                       "n_above_guideline": int(above.group(1)) if above else None}})
    # indoor-air: one record per analyte per report, within physical bounds (OCR drops decimals: 84.4 -> 844)
    bounds = {"temperature": (30, 120), "relative_humidity": (0, 100), "CO2": (150, 10000), "CO": (0, 100), "PM2.5": (0, 2000), "TVOC": (0, 100)}
    seen, kept = set(), []
    for r in recs:
        if r["template"] != "iaq_narrative": kept.append(r); continue
        lo, hi = bounds[r["analyte"]]
        if not (lo <= r["extra"]["min"] <= hi and lo <= r["extra"]["max"] <= hi): continue
        if r["analyte"] in seen: continue
        seen.add(r["analyte"]); kept.append(r)
    recs = kept
    school = canon_school(ctx["school"], ctx["project_no"])
    for r in recs: r["school_raw"] = r["school"]; r["school"] = school
    return recs, ctx


SCHOOLS = [  # (regex on raw name, canonical label, DOE building code)
    (r"stuy|stry|m477|chambers", "Stuyvesant High School", "M477"), (r"(?<!\d)89\s?m?(?!\d)|warren|ssm 20i|\bbom\b", "P.S. 89M", "M089"),
    (r"(?<!\d)234\s?m?(?!\d)|292 greenwich", "P.S. 234M", "M234"), (r"(?<!\d)150\s?m?(?!\d)|isom", "P.S. 150M", "M150"),
    (r"econ|m833|833m|100 trinity", "High School of Economics and Finance", "M833"),
    (r"leadership|m894|894m|90 trinity", "High School for Leadership and Public Service", "M894"),
    (r"desoto|\b130\s?m?\b", "P.S. 130M (DeSoto)", "M130"), (r"\b131\s?m?\b|13im|sun yat", "I.S. 131M (Dr. Sun Yat Sen)", "M131"),
    (r"meyer london|\b2\s?m\b|122 henry", "P.S. 2M (Meyer London)", "M002"), (r"bergt|m520|berg raum", "Murray Bergtraum High School", "M520"),
    (r"altman|\b42\s?m?\b", "P.S. 42M (Benjamin Altman)", "M042"), (r"riis|\b126\s?m?\b", "P.S. 126M (Jacob Riis)", "M126"),
    (r"\b124\s?m?\b", "P.S. 124M (Yung Wing)", "M124"), (r"p\. ?s\. ?1 \(|\bp\.? ?s\.? ?1m\b", "P.S. 1M (Alfred E. Smith)", "M001"),
    (r"\b65\s?q\b", "P.S. 65Q", "Q065"), (r"academy of technology", "Manhattan Academy of Technology", "M"),
    (r"various", "Various school locations", ""),
]
PROJECT_SCHOOL = {"15-19125-0246": "Stuyvesant High School", "15-19125-0279": "P.S. 234M", "15-19125-0289": "P.S. 150M",
                  "15-19125-0290": "High School for Leadership and Public Service", "15-19125-0291": "High School of Economics and Finance",
                  "15-19125-0297": "P.S. 89M", "15-19125-0341": "P.S. 2M (Meyer London)", "15-19125-0342": "P.S. 42M (Benjamin Altman)",
                  "15-19125-0343": "P.S. 126M (Jacob Riis)", "15-19125-0345": "P.S. 130M (DeSoto)", "15-19125-0346": "I.S. 131M (Dr. Sun Yat Sen)",
                  "15-19125-0350": "P.S. 124M (Yung Wing)", "15-19125-0351": "Murray Bergtraum High School", "15-19125-0340": "P.S. 1M (Alfred E. Smith)",
                  "14584-1823": "P.S. 234M", "14584-0824": "P.S. 150M", "14584-0823": "P.S. 234M", "14584-871": "P.S. 42M (Benjamin Altman)",
                  "14584-869": "P.S. 1M (Alfred E. Smith)"}
JUNK = re.compile(r"^(h\.?s\.?|hs|new|of|of and|dear mr\.? orlan:?|nyc-?boe|warren street,?|warren|chambers ny|p\. s\. \(manhattan\)|nycsca|boe h\.s\. for & public services)$", re.I)


def canon_school(raw, project_no):
    if raw:
        low = raw.lower()
        for rx, label, code in SCHOOLS:
            if re.search(rx, low): return label
    if PROJECT_SCHOOL.get(project_no): return PROJECT_SCHOOL[project_no]
    return None if (not raw or JUNK.match(raw.strip())) else raw.strip()


if __name__ == "__main__":
    idx = {r["file"]: r for r in json.load(open("data/reports_index.json", encoding="utf-8"))}
    n = 0; per_tpl = collections.Counter(); files_hit = collections.Counter(); files_tagged = collections.Counter(); no_school = 0
    parsed = []
    for name in sorted(os.listdir(REPORTS)):
        if not name.endswith(".pdf"): continue
        recs, ctx = parse_file(os.path.join(REPORTS, name))
        for t in idx.get(name, {}).get("tags", []): files_tagged[t] += 1
        if recs:
            for t in set(r["template"] for r in recs): files_hit[t] += 1
        parsed.append((int(name[8:17]), name, recs))
    # detached lab attachments carry no school; inherit from the nearest earlier packet within 12 portal ids, flagged
    parsed.sort(); last = None
    for num, name, recs in parsed:
        school = next((r["school"] for r in recs if r["school"]), None)
        if school: last = (num, school)
        elif recs and last and num - last[0] <= 12:
            for r in recs: r["school"] = last[1]; r["school_inferred"] = True
        elif recs: no_school += 1
    with open(OUT, "w", encoding="utf-8") as f:
        for num, name, recs in parsed:
            for r in recs:
                per_tpl[r["template"]] += 1; f.write(json.dumps(r, ensure_ascii=False) + "\n"); n += 1
    cov = {"records": n, "per_template": per_tpl, "files_with_records_by_template": files_hit, "files_tagged": files_tagged, "files_with_records_no_school": no_school}
    json.dump(cov, open(COV, "w"), indent=1)
    print(json.dumps(cov, indent=1))
