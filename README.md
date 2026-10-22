# nyc-911-archive

Navigator, building map and school air-testing timeline for the ~64,000 records in NYC Law
Department's [9/11 Document Portal](https://sept11documents.cityofnewyork.us/). Static site; lives
at https://projects.thealphabetagency.com/nyc-911-archive/ (GitHub Pages, served from `docs/`).

## Run / rebuild
```
pip install requests streamlit
python harvest.py --estimate        # volume check (free public API)
python harvest.py                   # ~680 requests, ~25 min, resumable (data/raw/manifest.jsonl)
python harvest.py --merge           # data/raw/*.json -> data/docs.jsonl (dedupes asc/desc overlap)
python build_site.py                # -> docs/data/{tree,buildings,schools}.json + docs/data/docs/*.json
cd docs && python -m http.server 8777   # preview at http://localhost:8777/

streamlit run app.py                # separate: live search client over the city's API
```
`data/` is gitignored (160 MB raw). `docs/data/` is committed; it is what the page reads.

## API notes (reverse-engineered 2026-10-21)
- `POST https://sept11documents.cityofnewyork.us/api/v2/search`. No auth, no CORS.
- ~128k indexed items = ~64k PDFs + ~64k `.md` OCR twins. `extension:pdf` in the query dedupes.
- Query syntax: `"air quality" agency:"Environmental Protection, Dept. of" production_volume:NYC-WTC0008`.
- Properties: `title agency production_volume box_name folder_name page_count pdf_size mes:lang`,
  `content` (HTML snippet, `content_sample_length` caps near 2,000 chars), `mes:thumbnail` (base64 JPEG).
- `count` caps at 100. Offsets are silently ignored unless the request has `user_context.service_id`
  AND `result_pages.qeng_ids[0].digest` copied from page 1's `per_service_dataset[0].paging_state.digest`.
  Offsets past ~30k fail, hence ascending + descending halves for DOE (34k docs).
- Wildcards on `title` do not work (tokenizer strips leading zeros). `mes:date` is the index date, useless.
- PDF: `https://sept11documents.cityofnewyork.us/apps/content/September11_MD/<title>`.

## What's in the corpus
- DOE ~34k: one-page TEM asbestos air count sheets (ATC Associates for Board of Ed), no box/folder tags.
- DEP ~25k: 68 "DEP Box NN" plus Asbestos/GRM boxes; folders are building addresses (Block/Lot/BIN),
  Gilsenan/Avaltroni email binders, EPA indoor-air working group, lab batches, FEMA cost matrix.
- DCAS ~3k: box "7DCAS", the OEM emergency operations center at 7 WTC (change orders, lease, minutes).
- Long tail: DDC, Law, FDNY (7 WTC blueprints/hazmat), OMB, OEM, DOHMH.

## Site data
- `tree.json` agency -> box -> folder counts. DOE count sheets collapsed into one virtual folder.
- `docs/<agency>.json` doc lists keyed `box|folder` (title, pages, volume, 160-char snippet).
- `buildings.json` GeoJSON of DEP address folders via NYC GeoSearch (cache in `data/geocode_cache.json`).
- `schools.json` count sheets per ISO week + ATC project index (dates parsed from OCR; floor, not census).

## Lab readings pipeline (added 2026-10-21/22)
```
python fetch_reports.py            # 1,911 multi-page DOE/ATC report PDFs -> data/reports/ (resumable, free, ~550 MB)
python classify_reports.py         # template tags per PDF -> data/reports_index.json
python parse_reports.py            # typed tables + narratives -> data/readings.jsonl (+ coverage json)
# handwritten pass: render pages, batch, transcribe with vision subagents (data/pages/{batches,out})
python merge_handwritten.py        # -> data/custody.jsonl, data/plm_bulk.jsonl, data/readings_located.jsonl
python build_site.py               # -> docs/data/readings.json (dedupes duplicate scans; joins rooms to asbestos samples)
```
Flat tables for reuse: `docs/data/custody.jsonl` (chain-of-custody rows) and `docs/data/plm_bulk.jsonl` (PLM bulk sheets).
Handwritten rows carry `legibility` good/fair/poor and the transcriber's notes live in `data/pages/out/*.jsonl`.
