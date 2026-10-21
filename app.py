"""Browse NYC's 9/11 Document Portal via its (open, unauthenticated) Mindbreeze JSON API.
Run:  streamlit run app.py
"""
import html, re
import requests, streamlit as st

API = "https://sept11documents.cityofnewyork.us/api/v2/search"
PDF = "https://sept11documents.cityofnewyork.us/apps/content/September11_MD/"
PAGE = 20  # server caps count at 100


@st.cache_data(ttl=3600, show_spinner=False)
def search(q, agency, volume, start, digest=None):
    """Mindbreeze quirk: offsets > 0 only work when the request carries the `digest`
    the server returned for page 1 of the same query, plus user_context.service_id."""
    terms = ([q] if q else []) + ["extension:pdf"]   # drop the .md OCR twins
    if agency: terms.append(f'agency:"{agency}"')
    if volume: terms.append(f"production_volume:{volume}")
    if start and not digest:
        digest = search(q, agency, volume, 0)[3]
    sid = API.rsplit("/", 1)[0] + "/"
    body = {
        "user_context": {"service_id": sid},
        "user": {"query": {"unparsed": " ".join(terms)}},
        "count": PAGE,
        "properties": [{"name": n, "formats": ["VALUE"]} for n in
                       ("title", "agency", "production_volume", "page_count", "mes:date")]
                      + [{"name": "content", "formats": ["HTML"]},
                         {"name": "mes:thumbnail", "formats": ["HTML"]}],
        "facets": [{"name": "agency"}, {"name": "production_volume"}],
        "result_pages": {"qeng_ids": [{"service_id": sid, "qeng_id": "https://localhost:23301",
                                       **({"digest": digest} if digest else {})}],
                         "pages": [{"starts": [start], "counts": [PAGE], "current_page": True}]},
    }
    r = requests.post(API, json=body, timeout=30)
    r.raise_for_status()
    d = r.json()
    rows = []
    rs = d.get("resultset", {})
    for res in rs.get("results", []):
        p = {x["id"]: x["data"][0] for x in res["properties"] if x["data"] and x["data"][0]}
        v = lambda k: p.get(k, {}).get("value", {}).get("str", "")
        rows.append({"title": v("title"), "agency": v("agency"), "vol": v("production_volume"),
                     "pages": v("page_count"), "snippet": p.get("content", {}).get("html", ""),
                     "thumb": p.get("mes:thumbnail", {}).get("html", "")})
    facets = {f["id"]: [(e["html"], e["count"]) for e in f["entries"]] for f in d.get("facets", [])}
    psd = rs.get("per_service_dataset") or [{}]
    digest = psd[0].get("paging_state", {}).get("digest")
    return d.get("estimated_count", 0), rows, facets, digest


st.set_page_config("9/11 Document Portal", layout="wide")
st.title("9/11 Document Portal browser")
q = st.text_input("Search", placeholder='e.g. "air quality" OR asbestos')
if "start" not in st.session_state: st.session_state.start = 0
if st.session_state.get("last_q") != q: st.session_state.start, st.session_state.last_q = 0, q

# facets from an unfiltered pass so the dropdowns don't collapse to the current filter
_, _, facets, _ = search(q, "", "", 0)
c1, c2 = st.columns(2)
agency = c1.selectbox("Agency", [""] + [f"{a}" for a, n in facets.get("agency", [])],
                      format_func=lambda a: a or "All")
volume = c2.selectbox("Production volume", [""] + [v for v, n in facets.get("production_volume", [])],
                      format_func=lambda v: v or "All")

total, rows, _, _ = search(q, agency, volume, st.session_state.start)
st.caption(f"{total:,} documents · showing {st.session_state.start + 1}–{st.session_state.start + len(rows)}")

for r in rows:
    left, right = st.columns([1, 6])
    if r["thumb"]: left.markdown(r["thumb"], unsafe_allow_html=True)
    right.markdown(f"**[{r['title']}]({PDF}{r['title']})** — {r['agency']} · {r['vol']} · {r['pages']} pp")
    snip = html.unescape(re.sub(r"\r?\n", " ", r["snippet"]))
    right.markdown(snip.replace("<em>", "**").replace("</em>", "**"))
    st.divider()

b1, b2, _ = st.columns([1, 1, 4])
if b1.button("◀ Prev", disabled=st.session_state.start == 0):
    st.session_state.start -= PAGE; st.rerun()
if b2.button("Next ▶", disabled=st.session_state.start + PAGE >= total):
    st.session_state.start += PAGE; st.rerun()
