from __future__ import annotations
import os, sqlite3, threading, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pandas as pd
import requests

CACHE_DB = "data/raw/label_cache.sqlite"

class TooManyFailures(RuntimeError):
    pass


def load_env(path=".env"):
    """Minimal .env reader (no extra dependency); existing environment variables win."""
    p = Path(path)
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def _connect(cache_db):
    Path(cache_db).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(cache_db)
    con.execute("CREATE TABLE IF NOT EXISTS tmdb (imdb_id TEXT PRIMARY KEY, tmdb_id TEXT, media_type TEXT, language TEXT, status TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS wikidata (imdb_id TEXT PRIMARY KEY, qid TEXT, language TEXT, status TEXT)")
    return con


class _Limiter:
    def __init__(self, interval):
        self.interval, self.lock, self.next = interval, threading.Lock(), 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            self.next = max(self.next, now) + self.interval
            delay = self.next - self.interval - now
        if delay > 0:
            time.sleep(delay)


def _progress(label, done, total, t0):
    rate = done / max(time.time() - t0, 1e-9)
    eta = (total - done) / rate if rate else 0
    print(f"\r  {label}: {done:,}/{total:,}  {rate:.1f}/s  eta {eta/60:.1f} min", end="", flush=True)


def tmdb_lookup(imdb_ids, api_key=None, cache_db=CACHE_DB, interval=0.1, workers=6, max_consecutive_failures=5):
    """TMDB /find by IMDb ID, movie match preferred over TV. Cached; only successful responses are stored,
    so an interrupted or failed run resumes without repeating finished lookups."""
    load_env()
    api_key = api_key or os.getenv("TMDB_API_KEY")
    if not api_key:
        raise RuntimeError("TMDB_API_KEY not set (expected in .env)")
    con = _connect(cache_db)
    done = {r[0] for r in con.execute("SELECT imdb_id FROM tmdb")}
    todo = [i for i in sorted(set(imdb_ids)) if i not in done]
    if todo:
        limiter, session = _Limiter(interval), requests.Session()
        state = {"fails": 0}
        lock = threading.Lock()

        def fetch(imdb_id):
            if state["fails"] >= max_consecutive_failures:
                return imdb_id, None
            for attempt in range(3):
                limiter.wait()
                try:
                    r = session.get(f"https://api.themoviedb.org/3/find/{imdb_id}",
                                    params={"api_key": api_key, "external_source": "imdb_id"}, timeout=30)
                    if r.status_code == 401:
                        raise TooManyFailures("TMDB rejected the API key (401)")
                    if r.status_code == 429:
                        time.sleep(float(r.headers.get("Retry-After", 5)))
                        continue
                    r.raise_for_status()
                    with lock:
                        state["fails"] = 0
                    return imdb_id, r.json()
                except TooManyFailures:
                    raise
                except Exception:
                    time.sleep(1 + attempt)
            with lock:
                state["fails"] += 1
            return imdb_id, None

        t0, n = time.time(), 0
        with ThreadPoolExecutor(workers) as ex:
            for imdb_id, payload in ex.map(fetch, todo):
                if payload is not None:
                    movie, tv = payload.get("movie_results") or [], payload.get("tv_results") or []
                    item, mtype = (movie[0], "movie") if movie else ((tv[0], "tv") if tv else ({}, None))
                    con.execute("INSERT OR REPLACE INTO tmdb VALUES (?,?,?,?,?)",
                                (imdb_id, item.get("id"), mtype, item.get("original_language"),
                                 "matched" if item else "not_found"))
                    n += 1
                    if n % 200 == 0:
                        con.commit()
                        _progress("TMDB", n, len(todo), t0)
                elif state["fails"] >= max_consecutive_failures:
                    break
        con.commit()
        print()
        if state["fails"] >= max_consecutive_failures:
            raise TooManyFailures(f"TMDB: {state['fails']} consecutive failures after {n:,} lookups; cache kept, rerun to resume")
    rows = con.execute("SELECT imdb_id, tmdb_id, media_type, language, status FROM tmdb").fetchall()
    con.close()
    return pd.DataFrame(rows, columns=["title_id", "tmdb_id", "tmdb_media", "tmdb_language", "tmdb_status"])


def wikidata_lookup(imdb_ids, cache_db=CACHE_DB, batch=100, interval=1.0, max_consecutive_failures=3):
    """Original language (P364 -> ISO 639-1 P218) of the item whose IMDb ID (P345) matches. Batched SPARQL, cached."""
    con = _connect(cache_db)
    done = {r[0] for r in con.execute("SELECT imdb_id FROM wikidata")}
    todo = [i for i in sorted(set(imdb_ids)) if i not in done]
    fails, t0 = 0, time.time()
    for k in range(0, len(todo), batch):
        ids = todo[k:k + batch]
        q = ('SELECT ?imdb ?item ?code WHERE { VALUES ?imdb { %s } ?item wdt:P345 ?imdb . '
             'OPTIONAL { ?item wdt:P364 ?l . ?l wdt:P218 ?code } }' % " ".join(f'"{i}"' for i in ids))
        try:
            r = requests.get("https://query.wikidata.org/sparql", params={"query": q, "format": "json"},
                             headers={"User-Agent": "cinema-mobility-research/0.1 (student project)"}, timeout=90)
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After", 30)))
                r = requests.get("https://query.wikidata.org/sparql", params={"query": q, "format": "json"},
                                 headers={"User-Agent": "cinema-mobility-research/0.1 (student project)"}, timeout=90)
            r.raise_for_status()
            fails = 0
        except Exception:
            fails += 1
            if fails >= max_consecutive_failures:
                con.commit()
                raise TooManyFailures(f"Wikidata: {fails} consecutive failures; cache kept, rerun to resume")
            continue
        found = {}
        for b in r.json()["results"]["bindings"]:
            d = found.setdefault(b["imdb"]["value"], {"qid": b["item"]["value"].rsplit("/", 1)[-1], "codes": set()})
            if "code" in b:
                d["codes"].add(b["code"]["value"])
        for i in ids:
            d = found.get(i)
            if not d:
                con.execute("INSERT OR REPLACE INTO wikidata VALUES (?,?,?,?)", (i, None, None, "not_found"))
            elif len(d["codes"]) == 1:
                con.execute("INSERT OR REPLACE INTO wikidata VALUES (?,?,?,?)", (i, d["qid"], next(iter(d["codes"])), "matched"))
            else:  # no original language, or several (multilingual): leave unlabeled
                con.execute("INSERT OR REPLACE INTO wikidata VALUES (?,?,?,?)",
                            (i, d["qid"], None, "no_language" if not d["codes"] else "multiple_languages"))
        con.commit()
        _progress("Wikidata", min(k + batch, len(todo)), len(todo), t0)
        time.sleep(interval)
    if todo:
        print()
    rows = con.execute("SELECT imdb_id, qid, language, status FROM wikidata").fetchall()
    con.close()
    return pd.DataFrame(rows, columns=["title_id", "wd_qid", "wd_language", "wd_status"])


def resolve_labels(title_ids, cache_db=CACHE_DB, **tmdb_kwargs) -> pd.DataFrame:
    """Label each title's original language: T1 TMDB, T2 Wikidata, else unknown.

    title.akas is deliberately not a labelling tier (its original-title rows carry no language); it is used
    only for candidate shortlisting and dub flagging. Cast is never used.
    """
    ids = sorted(set(title_ids))
    out = pd.DataFrame({"title_id": ids})
    out = out.merge(tmdb_lookup(ids, cache_db=cache_db, **tmdb_kwargs).query("title_id in @ids"), on="title_id", how="left")
    out["language"], out["label_source"] = None, "unknown"
    m1 = out.tmdb_language.notna()
    out.loc[m1, "language"], out.loc[m1, "label_source"] = out.loc[m1, "tmdb_language"], "T1"
    rest = out.loc[~m1, "title_id"]
    out = out.merge(wikidata_lookup(rest, cache_db=cache_db).query("title_id in @rest"), on="title_id", how="left")
    m2 = ~m1 & out.wd_language.notna()
    out.loc[m2, "language"], out.loc[m2, "label_source"] = out.loc[m2, "wd_language"], "T2"
    return out
