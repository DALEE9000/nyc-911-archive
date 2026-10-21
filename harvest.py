"""Harvest metadata + first ~2k chars of OCR text for every PDF in NYC's 9/11 Document Portal.

Usage:
    python harvest.py --estimate      # volume estimate, exits
    python harvest.py                 # run; resumes from data/raw/manifest.jsonl
    python harvest.py --retry-failed  # re-attempt failed pages
    python harvest.py --merge         # dedupe raw pages -> data/docs.jsonl

Free public endpoint, no cost. Chunk = (agency, sort direction, page offset); each page saved
as its own JSON so a hibernated laptop loses at most one 100-doc page.
Mindbreeze quirks: count caps at 100; offsets need the page-1 digest + user_context.service_id;
deep offsets past ~30k fail, so partitions > SPLIT_AT are fetched ascending AND descending by
date and deduped on merge.
"""
import argparse, json, random, re, time
from pathlib import Path
import requests

API = "https://sept11documents.cityofnewyork.us/api/v2/search"
SID = API.rsplit("/", 1)[0] + "/"
DATA_DIR = Path("data/raw"); MANIFEST = DATA_DIR / "manifest.jsonl"; OUT = Path("data/docs.jsonl")
PAGE = 100; SPLIT_AT = 20000; CONTENT_CHARS = 2000; STATUS_EVERY_S = 600; MAX_RETRIES = 5
PROPS = ["title", "agency", "production_volume", "box_name", "folder_name", "page_count", "pdf_size", "mes:lang"]
S = requests.Session()


def post(body):
    r = S.post(API, json=body, timeout=120); r.raise_for_status(); return r.json()


def facet(query, name):
    d = post({"user": {"query": {"unparsed": query}}, "count": 1, "facets": [{"name": name, "count": 200}]})
    return [(e["value"]["str"], e["count"]) for e in d["facets"][0]["entries"]] if d.get("facets") else []


def body(query, start, direction, digest=None):
    qe = {"service_id": SID, "qeng_id": "https://localhost:23301", **({"digest": digest} if digest else {})}
    return {"user": {"query": {"unparsed": query}}, "count": PAGE, "content_sample_length": CONTENT_CHARS,
            "orderby": "mes:date", "order_direction": direction, "user_context": {"service_id": SID},
            "properties": [{"name": p, "formats": ["VALUE"]} for p in PROPS] + [{"name": "content", "formats": ["HTML"]}],
            "result_pages": {"qeng_ids": [qe], "pages": [{"starts": [start], "counts": [PAGE], "current_page": True}]}}


def slug(s): return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def list_chunks():
    chunks = []
    for agency, n in facet("extension:pdf", "agency"):
        q = f'extension:pdf agency:"{agency}"'
        if n <= SPLIT_AT:
            plan = [("ASCENDING", n)]
        else:
            plan = [("ASCENDING", n // 2 + 500), ("DESCENDING", n // 2 + 500)]
        for direction, limit in plan:
            for start in range(0, limit, PAGE):
                chunks.append({"id": f"{slug(agency)}|{direction[0]}|{start:06d}", "q": q, "agency": agency,
                               "dir": direction, "start": start, "n": n})
    return chunks


_digests = {}


def fetch_chunk(c):
    key = (c["q"], c["dir"])
    if c["start"] and key not in _digests:
        d0 = post(body(c["q"], 0, c["dir"]))
        _digests[key] = d0["resultset"]["per_service_dataset"][0]["paging_state"]["digest"]
    d = post(body(c["q"], c["start"], c["dir"], _digests.get(key)))
    rs = d.get("resultset", {})
    if c["start"] and "per_service_dataset" in rs:
        _digests[key] = rs["per_service_dataset"][0]["paging_state"]["digest"]
    rows = []
    for r in rs.get("results", []):
        p = {x["id"]: x["data"][0] for x in r["properties"] if x.get("data") and x["data"][0]}
        v = lambda k: p.get(k, {}).get("value", {}).get("str", "")
        rows.append({"id": r["id"], **{k: v(k) for k in PROPS}, "content": p.get("content", {}).get("html", "")})
    if c["start"] and rows and rows[0]["title"] == first_title(c):   # paging silently ignored => bad data
        raise RuntimeError("offset ignored (digest stale?)")
    path = DATA_DIR / (c["id"].replace("|", "_") + ".json"); tmp = path.with_suffix(".part")
    tmp.write_text(json.dumps(rows), encoding="utf-8"); tmp.replace(path)
    return path, len(rows)


_first = {}


def first_title(c):
    key = (c["q"], c["dir"])
    if key not in _first:
        p = DATA_DIR / (f"{slug(c['agency'])}_{c['dir'][0]}_000000.json")
        _first[key] = json.loads(p.read_text(encoding="utf-8"))[0]["title"] if p.exists() else None
    return _first[key]


def load_manifest():
    out = {}
    if MANIFEST.exists():
        for line in MANIFEST.read_text().splitlines():
            rec = json.loads(line); out[rec["id"]] = rec
    return out


def record(cid, status, **extra):
    with MANIFEST.open("a") as f: f.write(json.dumps({"id": cid, "status": status, **extra}) + "\n")


def run(retry_failed=False):
    chunks = list_chunks(); done = load_manifest()
    todo = [c for c in chunks if done.get(c["id"], {}).get("status") != "ok"
            and (retry_failed or done.get(c["id"], {}).get("status") != "failed")]
    print(f"{len(chunks)} pages total, {len(chunks) - len(todo)} done, {len(todo)} to fetch", flush=True)
    t0 = last = time.time()
    for i, c in enumerate(todo, 1):
        for attempt in range(MAX_RETRIES):
            try:
                path, n = fetch_chunk(c); record(c["id"], "ok", path=str(path), n=n); break
            except Exception as e:  # noqa: BLE001
                wait = min(60, 2 ** attempt) + random.random()
                print(f"  {c['id']}: attempt {attempt + 1} failed ({e}); retry in {wait:.0f}s", flush=True)
                _digests.pop((c["q"], c["dir"]), None); time.sleep(wait)
        else:
            record(c["id"], "failed")
        now = time.time()
        if now - last > STATUS_EVERY_S or i == len(todo):
            rate = i / (now - t0); print(f"[{i}/{len(todo)}] {(now - t0) / 60:.1f} min elapsed, ETA {(len(todo) - i) / rate / 60:.1f} min", flush=True)
            last = now


def merge():
    seen, n = set(), 0
    with OUT.open("w", encoding="utf-8") as f:
        for p in sorted(DATA_DIR.glob("*.json")):
            for row in json.loads(p.read_text(encoding="utf-8")):
                if row["id"] in seen: continue
                seen.add(row["id"]); f.write(json.dumps(row, ensure_ascii=False) + "\n"); n += 1
    print(f"{n} unique docs -> {OUT}")


def estimate():
    chunks = list_chunks(); mb = len(chunks) * PAGE * (CONTENT_CHARS + 400) / 1e6
    print(f"{len(chunks)} requests x 100 docs, ~{mb:.0f} MB JSON. Free public API, no cost. ~{len(chunks) * 2 / 60:.0f} min at ~2 s/request.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--estimate", action="store_true")
    ap.add_argument("--retry-failed", action="store_true"); ap.add_argument("--merge", action="store_true")
    a = ap.parse_args()
    estimate() if a.estimate else merge() if a.merge else run(a.retry_failed)
