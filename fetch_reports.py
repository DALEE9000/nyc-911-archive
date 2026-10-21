"""Download the DOE multi-page lab/consultant reports (not the one-page count sheets) from the city server.

    python fetch_reports.py --estimate
    python fetch_reports.py            # resumable; rerun after any interruption

Free public endpoint. One PDF per file in data/reports/, manifest at data/reports/manifest.jsonl.
"""
import argparse, json, random, time
from pathlib import Path
import requests

PDF = "https://sept11documents.cityofnewyork.us/apps/content/September11_MD/"
DOCS = Path("data/docs.jsonl"); OUT = Path("data/reports"); MANIFEST = OUT / "manifest.jsonl"
STATUS_EVERY_S = 600; MAX_RETRIES = 5
S = requests.Session()


def list_chunks():
    out = []
    for line in DOCS.open(encoding="utf-8"):
        r = json.loads(line)
        if r["agency"].startswith("Education") and "Count S" not in r["content"] and int(r["page_count"] or 0) >= 2:
            out.append(r["title"])
    return sorted(out)


def load_manifest():
    done = {}
    if MANIFEST.exists():
        for line in MANIFEST.read_text().splitlines():
            rec = json.loads(line); done[rec["id"]] = rec
    return done


def record(cid, status, **extra):
    with MANIFEST.open("a") as f: f.write(json.dumps({"id": cid, "status": status, **extra}) + "\n")


def fetch(title):
    path = OUT / title; tmp = path.with_suffix(".part")
    r = S.get(PDF + title, timeout=120); r.raise_for_status()
    if not r.content.startswith(b"%PDF"): raise RuntimeError("not a PDF")
    tmp.write_bytes(r.content); tmp.replace(path); return len(r.content)


def run(retry_failed=False):
    OUT.mkdir(parents=True, exist_ok=True)
    chunks = list_chunks(); done = load_manifest()
    todo = [c for c in chunks if done.get(c, {}).get("status") != "ok" and (retry_failed or done.get(c, {}).get("status") != "failed")]
    print(f"{len(chunks)} reports, {len(chunks) - len(todo)} done, {len(todo)} to fetch", flush=True)
    t0 = last = time.time(); nbytes = 0
    for i, c in enumerate(todo, 1):
        for attempt in range(MAX_RETRIES):
            try:
                n = fetch(c); nbytes += n; record(c, "ok", bytes=n); break
            except Exception as e:  # noqa: BLE001
                wait = min(60, 2 ** attempt) + random.random()
                print(f"  {c}: attempt {attempt + 1} failed ({e}); retry in {wait:.0f}s", flush=True); time.sleep(wait)
        else:
            record(c, "failed")
        time.sleep(0.1)
        now = time.time()
        if now - last > STATUS_EVERY_S or i == len(todo):
            rate = i / (now - t0)
            print(f"[{i}/{len(todo)}] {(now - t0) / 60:.1f} min, {nbytes / 1e6:.0f} MB, ETA {(len(todo) - i) / rate / 60:.1f} min", flush=True)
            last = now


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--estimate", action="store_true"); ap.add_argument("--retry-failed", action="store_true")
    a = ap.parse_args()
    if a.estimate:
        n = len(list_chunks()); print(f"{n} PDFs x ~180 KB = ~{n * 0.18:.0f} MB. Free public server. ~{n * 1.0 / 60:.0f} min.")
    else:
        run(a.retry_failed)
