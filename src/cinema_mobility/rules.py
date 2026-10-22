"""Predefined ambiguity rules for focal-language titles, plus their evaluation and sensitivity analysis.

Rules (a title is flagged if any ACTIVE rule fires):
  spoken    TMDB spoken_languages contains two or more focal languages (hi, ta, te, ml, kn)
  wikidata  Wikidata lists two or more original languages (P364) for the title
  spoken_wiki (a2)  rule `spoken` AND the English Wikipedia film infobox (article found via its IMDb link) also lists two or
            or more focal languages; no IMDb-matched article means not flagged
  wikidata_focal  Wikidata lists two or more FOCAL languages (hi, ta, te, ml, kn) as original languages; en and
            other non-focal languages are ignored (the stricter, less noisy version of `wikidata`)
  disagree  TMDB original_language is missing from Wikidata's original languages (both present)
Flagged titles stay in the credits as collaboration ties with no language (label_source = rule_ambiguous).
"""
from __future__ import annotations
import json, threading, time
from concurrent.futures import ThreadPoolExecutor
from itertools import product
import numpy as np
import pandas as pd
import requests

from .resolver import CACHE_DB, _connect, _Limiter, _progress, TooManyFailures, load_env
from .pipeline import qualifying_years, select_pairs

import os

FOCAL = ("hi", "ta", "te", "ml", "kn")
RULES = ("spoken", "spoken_wiki", "wikidata", "wikidata_focal", "disagree")
ALIASES = {"a": "spoken", "a2": "spoken_wiki", "b": "wikidata", "bf": "wikidata_focal", "b-focal": "wikidata_focal",
           "b_focal": "wikidata_focal", "c": "disagree", **{r: r for r in RULES}}
PRIMARY = ("spoken", "wikidata_focal", "disagree")        # the rule set behind credits_final
PRIMARY_V2 = ("spoken_wiki", "wikidata_focal", "disagree")  # the rule set behind credits_final_v2
SOURCE_LABEL = "rule_ambiguous"


def parse_rules(text: str) -> tuple[str, ...]:
    """'all', 'none', or a comma list of rule names / letters a,b,c."""
    t = (text or "none").strip().lower()
    if t == "all":
        return RULES
    if t in ("none", ""):
        return ()
    bad = [x for x in t.split(",") if x.strip() not in ALIASES]
    if bad:
        raise ValueError(f"unknown rule(s) {bad}; choose from {list(RULES)}, all or none")
    return tuple(r for r in RULES if r in {ALIASES[x.strip()] for x in t.split(",")})


def _tables(con):
    con.execute("CREATE TABLE IF NOT EXISTS tmdb_detail (imdb_id TEXT PRIMARY KEY, tmdb_id TEXT, media TEXT, "
                "original_language TEXT, spoken_json TEXT, status TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS wikidata_all (imdb_id TEXT PRIMARY KEY, qids_json TEXT, langs_json TEXT, status TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS wikipedia_id (imdb_id TEXT PRIMARY KEY, status TEXT, page TEXT, langs_json TEXT)")


# --------------------------------------------------------------------------- fetching (cached, resumable)
def fetch_tmdb_details(items: pd.DataFrame, cache_db=CACHE_DB, interval=0.05, workers=8, max_consecutive_failures=5, api_key=None):
    """items: title_id, tmdb_id, tmdb_media. Stores original_language and spoken_languages from /movie/{id} or /tv/{id}."""
    load_env()
    api_key = api_key or os.getenv("TMDB_API_KEY")
    if not api_key:
        raise RuntimeError("TMDB_API_KEY not set (expected in .env)")
    con = _connect(cache_db)
    _tables(con)
    done = {r[0] for r in con.execute("SELECT imdb_id FROM tmdb_detail")}
    todo = items[~items.title_id.isin(done)].sort_values("title_id")
    if len(todo):
        limiter, session, lock, state = _Limiter(interval), requests.Session(), threading.Lock(), {"fails": 0}

        def fetch(row):
            if state["fails"] >= max_consecutive_failures:
                return row, None
            media = row.tmdb_media if row.tmdb_media in ("movie", "tv") else "movie"
            for attempt in range(3):
                limiter.wait()
                try:
                    r = session.get(f"https://api.themoviedb.org/3/{media}/{int(float(row.tmdb_id))}",
                                    params={"api_key": api_key, "language": "en-US"}, timeout=30)
                    if r.status_code == 401:
                        raise TooManyFailures("TMDB rejected the API key (401)")
                    if r.status_code == 429:
                        time.sleep(float(r.headers.get("Retry-After", 5)))
                        continue
                    if r.status_code == 404:
                        with lock:
                            state["fails"] = 0
                        return row, {"_not_found": True}
                    r.raise_for_status()
                    with lock:
                        state["fails"] = 0
                    return row, r.json()
                except TooManyFailures:
                    raise
                except Exception:
                    time.sleep(1 + attempt)
            with lock:
                state["fails"] += 1
            return row, None

        t0, n = time.time(), 0
        with ThreadPoolExecutor(workers) as ex:
            for row, d in ex.map(fetch, todo.itertuples(index=False)):
                if d is None:
                    if state["fails"] >= max_consecutive_failures:
                        break
                    continue
                if d.get("_not_found"):
                    con.execute("INSERT OR REPLACE INTO tmdb_detail VALUES (?,?,?,?,?,?)", (row.title_id, row.tmdb_id, row.tmdb_media, None, "[]", "not_found"))
                else:
                    spoken = [s["iso_639_1"] for s in d.get("spoken_languages", []) if s.get("iso_639_1")]
                    con.execute("INSERT OR REPLACE INTO tmdb_detail VALUES (?,?,?,?,?,?)",
                                (row.title_id, row.tmdb_id, row.tmdb_media, d.get("original_language"), json.dumps(spoken), "matched"))
                n += 1
                if n % 200 == 0:
                    con.commit()
                    _progress("TMDB detail", n, len(todo), t0)
        con.commit()
        print()
        if state["fails"] >= max_consecutive_failures:
            raise TooManyFailures(f"TMDB detail: {state['fails']} consecutive failures after {n:,} lookups; cache kept, rerun to resume")
    con.close()


def fetch_wikidata_languages(ids, cache_db=CACHE_DB, batch=100, interval=1.0, max_consecutive_failures=3):
    """All original languages (P364) of items whose IMDb ID (P345) matches; language key = ISO 639-1 code, else QID."""
    con = _connect(cache_db)
    _tables(con)
    done = {r[0] for r in con.execute("SELECT imdb_id FROM wikidata_all")}
    todo = [i for i in sorted(set(ids)) if i not in done]
    fails = skipped = 0
    t0 = time.time()
    headers = {"User-Agent": "cinema-mobility-research/0.1 (student project)"}
    for k in range(0, len(todo), batch):
        part = todo[k:k + batch]
        q = ('SELECT ?imdb ?item ?l ?code WHERE { VALUES ?imdb { %s } ?item wdt:P345 ?imdb . '
             'OPTIONAL { ?item wdt:P364 ?l . OPTIONAL { ?l wdt:P218 ?code } } }' % " ".join(f'"{i}"' for i in part))
        try:
            r = requests.get("https://query.wikidata.org/sparql", params={"query": q, "format": "json"}, headers=headers, timeout=90)
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After", 30)))
                r = requests.get("https://query.wikidata.org/sparql", params={"query": q, "format": "json"}, headers=headers, timeout=90)
            r.raise_for_status()
            fails = 0
        except Exception:
            fails += 1
            skipped += 1
            if fails >= max_consecutive_failures:
                con.commit()
                raise TooManyFailures(f"Wikidata: {fails} consecutive failures; cache kept, rerun to resume")
            continue
        found: dict[str, dict] = {}
        for b in r.json()["results"]["bindings"]:
            d = found.setdefault(b["imdb"]["value"], {"qids": set(), "langs": set()})
            d["qids"].add(b["item"]["value"].rsplit("/", 1)[-1])
            if "l" in b:
                d["langs"].add(b["code"]["value"] if "code" in b else b["l"]["value"].rsplit("/", 1)[-1])
        for i in part:
            d = found.get(i)
            con.execute("INSERT OR REPLACE INTO wikidata_all VALUES (?,?,?,?)",
                        (i, json.dumps(sorted(d["qids"])) if d else "[]", json.dumps(sorted(d["langs"])) if d else "[]",
                         "not_found" if not d else "matched" if d["langs"] else "no_language"))
        con.commit()
        _progress("Wikidata", min(k + batch, len(todo)), len(todo), t0)
        time.sleep(interval)
    if todo:
        print()
    if skipped:
        print(f"WARNING: {skipped} Wikidata batch(es) failed and were skipped; rerun to retry them.")
    con.close()


def fetch_wikipedia_languages(ids, cache_db=CACHE_DB, net=None, max_consecutive_failures=5, log=print):
    """Film-infobox language(s) of the English Wikipedia article that links to each IMDb title (ID match only).

    Uses the prefill HTTP cache (so earlier lookups are free) and its polite rate limit with backoff. Only
    definite answers are stored: matched, or no_article. Errors are retried on the next run.
    """
    from . import prefill
    net = net or prefill.Net()
    con = _connect(cache_db)
    _tables(con)
    done = {r[0] for r in con.execute("SELECT imdb_id FROM wikipedia_id")}
    todo = [i for i in sorted(set(ids)) if i not in done]
    fails, t0 = 0, time.time()
    for n, t in enumerate(todo, 1):
        deadline = time.time() + 60
        u = net.get("wiki", f"wext:{t}", prefill.WIKI, {"action": "query", "list": "exturlusage", "euquery": f"www.imdb.com/title/{t}",
                                                        "eunamespace": 0, "eulimit": 5, "format": "json"}, deadline)
        pages = None
        if "_error" not in u:
            linked = [x["title"] for x in u.get("query", {}).get("exturlusage", [])]
            pages = prefill._infobox(net, linked, deadline) if linked else []
            if isinstance(pages, dict):
                pages = None
        if pages is None:
            fails += 1
            if fails >= max_consecutive_failures:
                con.commit()
                raise TooManyFailures(f"Wikipedia: {fails} consecutive failures after {n - 1} titles; cache kept, rerun to resume")
            continue
        fails = 0
        if pages:
            info = prefill._read_infobox(*pages[0])
            con.execute("INSERT OR REPLACE INTO wikipedia_id VALUES (?,?,?,?)", (t, "matched", info["page"], json.dumps(info["languages"])))
        else:
            con.execute("INSERT OR REPLACE INTO wikipedia_id VALUES (?,?,?,?)", (t, "no_article", None, "[]"))
        if n % 25 == 0:
            con.commit()
            _progress("Wikipedia", n, len(todo), t0)
    con.commit()
    if todo:
        print()
    con.close()


def fetch_rule_data(status: pd.DataFrame, labels: pd.DataFrame, title_ids=None, cache_db=CACHE_DB, tmdb_interval=0.05, wikipedia=False):
    """Fetch what the rules need for focal-labeled titles (optionally a subset)."""
    foc = _focal_titles(status, labels)
    if title_ids is not None:
        foc = foc[foc.title_id.isin(set(title_ids))]
    t1 = foc[foc.label_source.eq("T1") & foc.tmdb_id.notna()]
    print(f"{len(foc):,} focal-labeled titles: {len(t1):,} TMDB detail requests, {-(-len(foc) // 100)} Wikidata batches")
    fetch_tmdb_details(t1[["title_id", "tmdb_id", "tmdb_media"]], cache_db, interval=tmdb_interval)
    fetch_wikidata_languages(foc.title_id, cache_db)
    if wikipedia:  # only the titles rule (a) flags
        a = rules_table(status, labels, foc.title_id, cache_db)
        ids = a.title_id[a.rule_spoken]
        print(f"Wikipedia infobox lookups for the {len(ids):,} titles flagged by rule (a)")
        fetch_wikipedia_languages(ids, cache_db)


def _focal_titles(status, labels):
    # Titles relabelled by manual review (label_source "reviewed") have no TMDB/Wikidata evidence to apply rules to.
    foc = status[status.status.eq("kept_labeled") & status.label_source.isin(["T1", "T2"])][["title_id", "language", "label_source"]]
    return foc.merge(labels[["title_id", "tmdb_id", "tmdb_media", "tmdb_language"]], on="title_id", how="left")


# --------------------------------------------------------------------------- rules
def rules_table(status, labels, title_ids=None, cache_db=CACHE_DB) -> pd.DataFrame:
    """One row per focal-labeled title with the data each rule uses and one boolean per rule (from the cache only)."""
    import sqlite3
    foc = _focal_titles(status, labels)
    if title_ids is not None:
        foc = foc[foc.title_id.isin(set(title_ids))]
    con = sqlite3.connect(cache_db)
    td = pd.read_sql("SELECT imdb_id AS title_id, spoken_json, status AS tmdb_detail_status FROM tmdb_detail", con)
    wd = pd.read_sql("SELECT imdb_id AS title_id, langs_json, status AS wd_status FROM wikidata_all", con)
    wp = pd.read_sql("SELECT imdb_id AS title_id, status AS wiki_status, page AS wiki_page, langs_json AS wiki_json FROM wikipedia_id", con) \
        if con.execute("SELECT name FROM sqlite_master WHERE name='wikipedia_id'").fetchone() else \
        pd.DataFrame(columns=["title_id", "wiki_status", "wiki_page", "wiki_json"])
    con.close()
    t = foc.merge(td, on="title_id", how="left").merge(wd, on="title_id", how="left").merge(wp, on="title_id", how="left")
    miss_t = t[t.label_source.eq("T1") & t.tmdb_id.notna() & t.tmdb_detail_status.isna()]
    miss_w = t[t.wd_status.isna()]
    if len(miss_t) or len(miss_w):
        raise RuntimeError(f"rule data incomplete: {len(miss_t)} titles without TMDB detail, {len(miss_w)} without Wikidata; run rules-fetch")
    t["spoken"] = t.spoken_json.map(lambda s: json.loads(s) if isinstance(s, str) else [])
    t["wd_langs"] = t.langs_json.map(lambda s: json.loads(s) if isinstance(s, str) else [])
    t["n_spoken_focal"] = t.spoken.map(lambda s: len(set(s) & set(FOCAL)))
    t["rule_spoken"] = t.n_spoken_focal >= 2
    t["wiki_langs"] = t.wiki_json.map(lambda s: json.loads(s) if isinstance(s, str) else [])
    t["n_wiki_focal"] = t.wiki_langs.map(lambda ls: len(set(ls) & set(FOCAL)))
    # (a2) needs a fetched Wikipedia answer; flagged_titles refuses to use it for titles that were never looked up
    t["rule_spoken_wiki"] = t.rule_spoken & (t.wiki_status == "matched") & (t.n_wiki_focal >= 2)
    t["rule_wikidata"] = t.wd_langs.map(len) >= 2
    t["n_wd_focal"] = t.wd_langs.map(lambda ls: len(set(ls) & set(FOCAL)))
    t["rule_wikidata_focal"] = t.n_wd_focal >= 2
    t["rule_disagree"] = [bool(a and w and a not in w) for a, w in zip(t.tmdb_language.fillna(""), t.wd_langs)]
    return t[["title_id", "language", "label_source", "tmdb_language", "spoken", "n_spoken_focal", "wd_status", "wd_langs",
              "n_wd_focal", "wiki_status", "wiki_page", "wiki_langs", "n_wiki_focal",
              "rule_spoken", "rule_spoken_wiki", "rule_wikidata", "rule_wikidata_focal", "rule_disagree"]].reset_index(drop=True)


def flagged_titles(rules: pd.DataFrame, active) -> set:
    if not len(active):
        return set()
    if "spoken_wiki" in active:
        missing = rules[rules.rule_spoken & rules.wiki_status.isna()]
        if len(missing):
            raise RuntimeError(f"rule a2 needs Wikipedia lookups for {len(missing)} titles flagged by rule a; run rules-fetch --wikipedia")
    mask = np.zeros(len(rules), dtype=bool)
    for r in active:
        mask |= rules[f"rule_{r}"].to_numpy()
    return set(rules.title_id[mask])


def apply_rule_flags(credits: pd.DataFrame, rules: pd.DataFrame, active) -> tuple[pd.DataFrame, set]:
    """Flagged titles keep their credits as ties only: language null, label_source rule_ambiguous."""
    flagged = flagged_titles(rules, active)
    if not flagged:
        return credits, flagged
    out = credits.copy()
    m = out.title_id.isin(flagged)
    out.loc[m, "language"] = None
    out.loc[m, "label_source"] = SOURCE_LABEL
    return out, flagged


# --------------------------------------------------------------------------- evaluation
def control_evaluation(rules: pd.DataFrame, control: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Compare rule flags with the control file's ambiguity flags (pre-filled evidence, not a hand review)."""
    c = control.rename(columns={"tconst": "title_id"})[["title_id", "primaryTitle", "language", "is_ambiguous"]].copy()
    c["manual"] = c.is_ambiguous.eq("1")
    m = c.merge(rules.drop(columns=["language"]), on="title_id", how="left")
    for r in RULES:
        m[f"rule_{r}"] = (m[f"rule_{r}"] if f"rule_{r}" in m else pd.Series(False, index=m.index)).fillna(False).astype(bool)
    m["rule_any"] = m[[f"rule_{r}" for r in RULES]].any(axis=1)
    rows = []
    for name in [*(f"rule_{r}" for r in RULES), "rule_any"]:
        tp = int((m[name] & m.manual).sum())
        fl, pos = int(m[name].sum()), int(m.manual.sum())
        rows.append({"rule": name.replace("rule_", ""), "flagged": fl, "manual_flags": pos, "true_positive": tp,
                     "false_positive": fl - tp, "missed": pos - tp,
                     "precision": tp / fl if fl else np.nan, "recall": tp / pos if pos else np.nan})
    return pd.DataFrame(rows), m


def sensitivity(credits: pd.DataFrame, rules: pd.DataFrame, focal=FOCAL):
    """Every on/off combination of the three rules: titles/credits flagged, eligibility, and the pair screen."""
    summary, pairs_long = [], []
    for flags in product([False, True], repeat=len(RULES)):
        active = tuple(r for r, f in zip(RULES, flags) if f)
        mod, flagged = apply_rule_flags(credits, rules, active)
        q = qualifying_years(mod, focal)
        ps = select_pairs(mod, focal_languages=focal)
        name = "+".join(active) if active else "none"
        summary.append({"rules": name, "titles_flagged": len(flagged), "credits_flagged": int(credits.title_id.isin(flagged).sum()),
                        "eligible_actor_language_pairs": len(q), "eligible_actors": int(q.actor_id.nunique()),
                        "total_events": int(ps.events.sum()), "total_at_risk_actor_years": int(ps.at_risk_actor_years.sum()),
                        "min_pair_events": int(ps.events.min()), "pairs_meeting_default_thresholds": int(ps.selected.sum())})
        pairs_long.append(ps.assign(rules=name).drop(columns="selected"))
    return pd.DataFrame(summary), pd.concat(pairs_long, ignore_index=True)
