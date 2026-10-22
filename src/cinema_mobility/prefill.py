"""Evidence-based pre-fill of a review file (TMDB API + Wikipedia API). Writes a NEW file; never edits the input.

Policy (set by the reviewer): keep the current label unless clear, reliable evidence contradicts it; flag
is_ambiguous only when something suggests a problem; say "no evidence found" rather than guess.
"""
from __future__ import annotations
import json, os, re, sqlite3, time
from pathlib import Path
import pandas as pd
import requests

from .resolver import load_env

UA = {"User-Agent": "cinema-mobility-research/0.1 (student research project; polite, rate-limited)"}
CACHE = "data/review/prefill_cache.sqlite"
OUT_COLS_AFTER = "confidence"
# language name -> code (ISO 639-1 where one exists)
NAMES = {"hindi": "hi", "urdu": "ur", "tamil": "ta", "telugu": "te", "malayalam": "ml", "kannada": "kn", "bengali": "bn",
         "bangla": "bn", "marathi": "mr", "punjabi": "pa", "gujarati": "gu", "odia": "or", "oriya": "or", "assamese": "as",
         "sanskrit": "sa", "sindhi": "sd", "nepali": "ne", "kashmiri": "ks", "konkani": "kok", "tulu": "tcy", "bhojpuri": "bho",
         "english": "en", "japanese": "ja", "korean": "ko", "mandarin": "zh", "chinese": "zh", "cantonese": "cn", "french": "fr",
         "german": "de", "spanish": "es", "italian": "it", "russian": "ru", "arabic": "ar", "persian": "fa", "turkish": "tr",
         "thai": "th", "portuguese": "pt", "dutch": "nl", "swedish": "sv", "polish": "pl", "sinhala": "si"}
INDIAN = {"hi", "ur", "ta", "te", "ml", "kn", "bn", "mr", "pa", "gu", "or", "as", "sa", "sd", "ne", "ks", "kok", "tcy", "bho"}


class Net:
    """Rate-limited GETs with backoff; remembers hosts that block us and caches every response."""

    def __init__(self, cache=CACHE, tmdb_interval=0.25, wiki_interval=1.5):
        Path(cache).parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(cache)
        self.con.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")
        self.interval = {"tmdb": tmdb_interval, "wiki": wiki_interval}
        self.last = {"tmdb": 0.0, "wiki": 0.0}
        self.blocked: dict[str, str] = {}
        self.session = requests.Session()
        self.session.headers.update(UA)
        load_env()
        self.tmdb_key = os.getenv("TMDB_API_KEY")
        self.requests = 0

    def get(self, host, cache_key, url, params, deadline):
        row = self.con.execute("SELECT v FROM kv WHERE k=?", (cache_key,)).fetchone()
        if row:
            return json.loads(row[0])
        if host in self.blocked:
            return {"_error": self.blocked[host]}
        for attempt in range(4):
            if time.time() > deadline:
                return {"_error": "time budget exhausted"}
            wait = self.last[host] + self.interval[host] - time.time()
            if wait > 0:
                time.sleep(wait)
            self.last[host] = time.time()
            self.requests += 1
            try:
                r = self.session.get(url, params=params, timeout=20)
            except requests.RequestException as e:
                time.sleep(2 ** attempt)
                err = f"network error ({type(e).__name__})"
                continue
            if r.status_code in (429, 503):
                time.sleep(min(float(r.headers.get("Retry-After", 2 ** (attempt + 1))), 60))
                err = f"throttled (HTTP {r.status_code})"
                continue
            if r.status_code in (401, 403):
                self.blocked[host] = f"{host} blocked access (HTTP {r.status_code})"
                return {"_error": self.blocked[host]}
            if r.status_code == 404:
                out = {"_not_found": True}
            else:
                r.raise_for_status()
                out = r.json()
            self.con.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (cache_key, json.dumps(out)))
            self.con.commit()
            return out
        return {"_error": err}


def tmdb_facts(net, tconst, tmdb_id, media, deadline):
    """original_language, spoken_languages, title/overview from the TMDB movie (or tv) record."""
    if not net.tmdb_key:
        return {"error": "TMDB_API_KEY not set"}
    if not tmdb_id or pd.isna(tmdb_id):
        f = net.get("tmdb", f"find:{tconst}", f"https://api.themoviedb.org/3/find/{tconst}",
                    {"api_key": net.tmdb_key, "external_source": "imdb_id"}, deadline)
        if "_error" in f:
            return {"error": f["_error"]}
        hit = (f.get("movie_results") or []) and ("movie", f["movie_results"][0]["id"]) or \
              (f.get("tv_results") or []) and ("tv", f["tv_results"][0]["id"])
        if not hit:
            return {}
        media, tmdb_id = hit
    media = media if media in ("movie", "tv") else "movie"
    d = net.get("tmdb", f"{media}:{int(tmdb_id)}", f"https://api.themoviedb.org/3/{media}/{int(tmdb_id)}",
                {"api_key": net.tmdb_key, "language": "en-US"}, deadline)
    if "_error" in d:
        return {"error": d["_error"]}
    if "_not_found" in d:
        return {}
    spoken = [s.get("iso_639_1") for s in d.get("spoken_languages", []) if s.get("iso_639_1")]
    return {"original": d.get("original_language"), "spoken": spoken, "title": d.get("title") or d.get("name"),
            "overview": d.get("overview") or ""}


def _lang_codes(text):
    text = re.sub(r"<ref[^>]*?/>|<ref.*?</ref>|<!--.*?-->", " ", text, flags=re.S)
    return sorted({code for name, code in NAMES.items() if re.search(rf"\b{name}\b", text, flags=re.I)})


WIKI = "https://en.wikipedia.org/w/api.php"


def _infobox(net, titles, deadline):
    """Lead-section wikitext of the given pages (one request); returns [(title, wikitext)] for film infoboxes."""
    d = net.get("wiki", f"wlead:{'|'.join(titles)}", WIKI,
                {"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main", "rvsection": 0,
                 "titles": "|".join(titles), "format": "json", "formatversion": 2, "redirects": 1}, deadline)
    if "_error" in d:
        return d
    out = []
    for pg in d.get("query", {}).get("pages", []):
        try:
            text = pg["revisions"][0]["slots"]["main"]["content"]
        except (KeyError, IndexError):
            continue
        if re.search(r"\{\{\s*infobox (film|television film)", text, flags=re.I):
            out.append((pg["title"], text))
    return out


def _read_infobox(page, text):
    m = re.search(r"\|\s*language\s*=\s*(.*?)(?=\n\s*\||\n\}\})", text, flags=re.S | re.I)
    rel = re.search(r"\|\s*released\s*=\s*(.*?)(?=\n\s*\||\n\}\})", text, flags=re.S | re.I)
    return {"page": page, "languages": _lang_codes(m.group(1)) if m else [],
            "raw": re.sub(r"\s+", " ", m.group(1))[:60] if m else "", "released": rel.group(1) if rel else ""}


def wiki_facts(net, tconst, title, year, deadline):
    """English Wikipedia film article and its infobox language(s).

    Strong match: an article that links to this exact IMDb title page. Weak match (informational only, never
    used to contradict a label): search hit whose title equals the film title and whose release year matches.
    """
    u = net.get("wiki", f"wext:{tconst}", WIKI, {"action": "query", "list": "exturlusage", "euquery": f"www.imdb.com/title/{tconst}",
                                                 "eunamespace": 0, "eulimit": 5, "format": "json"}, deadline)
    if "_error" in u:
        return {"error": u["_error"]}
    linked = [x["title"] for x in u.get("query", {}).get("exturlusage", [])]
    if linked:
        pages = _infobox(net, linked, deadline)
        if isinstance(pages, dict):
            return {"error": pages["_error"]}
        if pages:
            return {**_read_infobox(*pages[0]), "match": "id"}
    s = net.get("wiki", f"wsearch:{title}|{year}", WIKI,
                {"action": "query", "list": "search", "srsearch": f'"{title}" {year} film', "srlimit": 2, "format": "json"}, deadline)
    if "_error" in s:
        return {"error": s["_error"]}
    hits = [h["title"] for h in s.get("query", {}).get("search", [])]
    if not hits:
        return {}
    pages = _infobox(net, hits, deadline)
    if isinstance(pages, dict):
        return {"error": pages["_error"]}
    for page, text in pages:
        info = _read_infobox(page, text)
        base = re.sub(r"\s*\(.*film\)$", "", page).casefold()
        years = {str(year), str(int(year) - 1), str(int(year) + 1)}
        if base == title.casefold() and any(y in info["released"] for y in years):
            return {**info, "match": "title+year"}
    return {}


def decide(row, tm, wk):
    """Apply the policy (ambiguous only for a source conflict or a source saying bilingual/multilingual); returns (is_dubbed, is_ambiguous, corrected_language, confidence, notes)."""
    cur = (row["language"] or "").strip().lower()
    src = row["current_label_source"]
    parts, dubbed, ambig, corrected = [], 0, 0, ""
    # --- what each source says
    t_orig = (tm.get("original") or "").lower() if tm else ""
    t_spoken = tm.get("spoken", []) if tm else []
    if tm and tm.get("error"):
        parts.append(f"TMDB unavailable: {tm['error']}")
    elif tm and t_orig:
        parts.append(f"TMDB original_language={t_orig}, spoken_languages={t_spoken or 'none listed'}")
    elif src != "T1":
        parts.append("TMDB: no record")
    w_ok = bool(wk) and not wk.get("error") and bool(wk.get("languages"))
    if wk and wk.get("error"):
        parts.append(f"Wikipedia unavailable: {wk['error']}")
    elif w_ok:
        how = "IMDb-id match" if wk["match"] == "id" else "title+year match only, weak"
        parts.append(f"Wikipedia '{wk['page']}' ({how}) infobox language={wk['languages']}")
    elif wk and wk.get("match"):
        parts.append(f"Wikipedia '{wk['page']}' ({wk['match']}) infobox language unparseable ({wk['raw'] or 'empty'})")
    else:
        parts.append("Wikipedia: no film article found")
    # explicit dub marker in the entry's own title (an overview mentioning a dubbed version does not count)
    text = " ".join([row["primaryTitle"], (tm or {}).get("title") or ""])
    if re.search(r"\bdub(bed)?\b", text, flags=re.I):
        dubbed = 1
        parts.append("entry title says dubbed")
    # --- reduce to claims
    t_multi = len((set(t_spoken) & INDIAN)) >= 2
    w_multi = w_ok and wk["match"] == "id" and len(set(wk["languages"]) & INDIAN) >= 2
    w_strong = w_ok and wk["match"] == "id"
    w_langs = set(wk["languages"]) if w_ok else set()
    conf = "low"
    claims = []
    if t_orig:
        claims.append(("TMDB", {t_orig}))
    if w_ok and w_strong:
        claims.append(("Wikipedia", w_langs))
    elif w_ok and cur and w_langs != {cur}:
        parts.append("weak Wikipedia match differs from the label; not treated as a conflict")
    agree_cur = [n for n, s in claims if cur and s == {cur}]
    contradict = [n for n, s in claims if cur and cur not in s]
    if t_multi or w_multi:
        ambig = 1
        parts.append("a source lists several Indian languages (possibly bilingual)")
        conf = "low"
    elif cur and contradict:
        ambig = 1  # a source contradicts the label but it is not clear-cut: flag, do not change
        parts.append(f"sources conflict on label {cur}")
        conf = "low"
    elif cur and agree_cur:
        if "TMDB" in agree_cur and "Wikipedia" in agree_cur and w_strong:
            conf = "high"
        elif "TMDB" in agree_cur or ("Wikipedia" in agree_cur and w_strong):
            conf = "medium"
    elif cur and not claims:
        parts.append("no evidence found")
    elif not cur:
        if w_strong and len(w_langs) == 1:
            corrected = next(iter(w_langs))
            conf = "medium"
            parts.append(f"label was unknown; filled from Wikipedia")
        elif t_orig and not (w_ok and w_langs != {t_orig}):
            corrected = t_orig
            conf = "medium" if not w_ok else "high"
            parts.append("label was unknown; filled from TMDB")
        elif not claims:
            parts.append("no evidence found")
        else:
            conf = "low"
    # akas in several languages are usually dubbed releases: noted for the reviewer, never a reason to flag.
    if "multi_language_akas" in row["reason"]:
        parts.append("akas in several languages (likely dubbed releases; not a reason to flag)")
    if dubbed and t_orig and cur and t_orig != cur:
        corrected = t_orig
    if dubbed:
        conf = "medium" if conf == "low" else conf
    return str(dubbed), str(ambig), corrected, conf, "; ".join(parts)


def prefill(src, out, labels_path="data/interim/labels.parquet", max_year=2024, limit=None, batch=50, cache=CACHE, log=print):
    """Pre-fill `src` into `out` (a new file). Resumable: rows with a confidence value are not redone."""
    src, out = Path(src).resolve(), Path(out).resolve()
    if src == out:
        raise SystemExit("refusing to write over the input file")
    d = pd.read_csv(src, dtype=str, keep_default_na=False)
    for c in ["is_dubbed", "is_ambiguous", "corrected_language", "notes"]:
        if d[c].ne("").any():
            raise SystemExit(f"{src} already has {c} values; refusing to treat it as a blank review file")
    year = pd.to_numeric(d.year, errors="coerce")
    skip = d[(year > max_year) | year.isna()]
    if out.exists():
        prev = pd.read_csv(out, dtype=str, keep_default_na=False)
        if list(prev.tconst) != list(d.tconst):
            raise SystemExit(f"{out} exists but does not match {src}; not touching it")
        d = prev
    else:
        d.insert(d.columns.get_loc("notes") + 1, OUT_COLS_AFTER, "")
    labels = pd.read_parquet(labels_path, columns=["title_id", "tmdb_id", "tmdb_media"]).set_index("title_id")
    todo = [i for i in d.index if d.at[i, "confidence"] == "" and d.at[i, "tconst"] not in set(skip.tconst)]
    if limit:
        todo = todo[:limit]
    net = Net(cache)
    log(f"{len(d)} rows; {len(skip)} skipped (year > {max_year} or missing); {len(todo)} to process now")
    for b in range(0, len(todo), batch):
        t0 = time.time()
        for i in todo[b:b + batch]:
            row = d.loc[i]
            deadline = time.time() + 60
            lab = labels.loc[row.tconst] if row.tconst in labels.index else {}
            tm = tmdb_facts(net, row.tconst, lab.get("tmdb_id") if len(lab) else None, lab.get("tmdb_media") if len(lab) else None, deadline)
            wk = wiki_facts(net, row.tconst, row.primaryTitle, row.year, deadline)
            dub, amb, corr, conf, note = decide(row, tm, wk)
            d.loc[i, ["is_dubbed", "is_ambiguous", "corrected_language", "confidence", "notes"]] = [dub, amb, corr, conf, note]
        tmp = out.with_suffix(".csv.tmp")
        d.to_csv(tmp, index=False)
        tmp.replace(out)
        log(f"  saved after rows {b + 1}-{min(b + batch, len(todo))} of {len(todo)} ({time.time() - t0:.0f}s, {net.requests} requests so far)")
    d.to_csv(out.with_suffix(".csv.tmp"), index=False)
    out.with_suffix(".csv.tmp").replace(out)
    return d, skip
