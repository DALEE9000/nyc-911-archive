"""Index the downloaded DOE reports: page count, text-layer size, template tags, project line, first date.

    python classify_reports.py      # -> data/reports_index.json
"""
import collections, glob, json, os, re
import pymupdf

KEYS = [
    ("asbestos_tem", r"Transmission Electron Microscopy|\(S/cc\)|AHERA"),
    ("dioxin", r"DIOXIN WIPE|TCDD"),
    ("mercury", r"Mercury Vapor|Mercury"),
    ("hydrocarbons", r"HYDROCARBONS|Hydrocarbons|VOC"),
    ("pm", r"PM ?2\.5|PM ?10|Respirable Particulate"),
    ("lead_dust", r"LEAD-IN-DUST|Lead-in-Dust|Lead in Dust|lead wipe"),
    ("iaq_field", r"INDOOR AIR QUALITY FIELD REPORT"),
    ("iaq_assess", r"TARGET GUIDELINES FOR INDOOR AIR QUALITY|Indoor Air Quality Assessment"),
    ("chain_custody", r"CHAIN OF CUSTODY"),
    ("plm_bulk", r"Polarized Light|PLM"),
    ("pcb", r"\bPCB"),
]
DATE = re.compile(r"\b(\d{1,2}/\d{1,2}/0[1-4])\b")
PROJECT = re.compile(r"Project:\s*(.+)")

rows, cnt, textless = [], collections.Counter(), 0
for f in sorted(glob.glob("data/reports/*.pdf")):
    d = pymupdf.open(f)
    full = "\n".join(pg.get_text() for pg in d)
    tags = [k for k, rx in KEYS if re.search(rx, full)]
    if len(full) < 200 * len(d): textless += 1
    proj = PROJECT.search(full); date = DATE.search(full)
    rows.append({"file": os.path.basename(f), "pages": len(d), "chars": len(full), "tags": tags,
                 "project": proj.group(1).strip()[:80] if proj else None, "date": date.group(1) if date else None})
    for k in tags: cnt[k] += 1
    if not tags: cnt["untagged"] += 1
json.dump(rows, open("data/reports_index.json", "w", encoding="utf-8"), ensure_ascii=False)
print("reports", len(rows), "thin text layer", textless)
print(cnt.most_common())
print("with project", sum(1 for r in rows if r["project"]), "with date", sum(1 for r in rows if r["date"]))
print(collections.Counter(tuple(r["tags"]) for r in rows).most_common(12))
