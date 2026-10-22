from __future__ import annotations
import os, sqlite3, time
from pathlib import Path
import pandas as pd
import requests


def from_imdb(principals_path, basics_path, languages_path, output_path, max_billing=10):
    """Join IMDb principal acting credits to title years and reviewed language labels.

    languages.csv must contain tconst, language, and optionally is_dubbed,
    is_ambiguous. The language field should be a reviewed ISO-639-1 code.
    """
    basics = pd.read_csv(basics_path, sep="\t", na_values="\\N", usecols=["tconst", "titleType", "startYear"])
    basics = basics[basics.titleType.isin(["movie", "tvMovie"])].dropna(subset=["startYear"])
    basics["year"] = pd.to_numeric(basics.pop("startYear"), errors="coerce")
    chunks = []
    for part in pd.read_csv(principals_path, sep="\t", na_values="\\N", chunksize=1_000_000,
                            usecols=["tconst", "ordering", "nconst", "category"]):
        part = part[part.category.isin(["actor", "actress"]) & part.ordering.le(max_billing)]
        chunks.append(part.rename(columns={"tconst":"title_id", "nconst":"actor_id", "ordering":"billing_order"})[["title_id", "actor_id", "billing_order"]])
    principal = pd.concat(chunks, ignore_index=True)
    lang = pd.read_csv(languages_path)
    if not {"tconst", "language"}.issubset(lang.columns):
        raise ValueError("language labels need tconst and language columns")
    lang = lang.rename(columns={"tconst":"title_id"})
    titles = basics.rename(columns={"tconst":"title_id"})
    # Left join: titles without a reviewed label stay as credits (ties) with a null language.
    out = principal.merge(titles[["title_id", "year"]], on="title_id").merge(lang, on="title_id", how="left")
    if "label_source" not in out:
        out["label_source"] = out["language"].notna().map({True: "reviewed", False: "unknown"})
    out["label_source"] = out["label_source"].fillna("unknown")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(output_path, index=False)
    return out


def tmdb_enrich(imdb_ids, output_csv, api_key=None, cache_db="data/raw/tmdb_cache.sqlite", delay=.25):
    """Resolve IMDb title IDs via TMDB /find and cache language matches in SQLite."""
    api_key = api_key or os.getenv("TMDB_API_KEY")
    if not api_key:
        raise RuntimeError("Set TMDB_API_KEY before running TMDB enrichment")
    Path(cache_db).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(cache_db)
    con.execute("CREATE TABLE IF NOT EXISTS tmdb (imdb_id TEXT PRIMARY KEY, tmdb_id TEXT, language TEXT, status TEXT)")
    session = requests.Session()
    for imdb_id in sorted(set(map(str, imdb_ids))):
        if con.execute("SELECT 1 FROM tmdb WHERE imdb_id=?", (imdb_id,)).fetchone():
            continue
        url = f"https://api.themoviedb.org/3/find/{imdb_id}"
        resp = session.get(url, params={"api_key": api_key, "external_source":"imdb_id"}, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        items = payload.get("movie_results", []) or payload.get("tv_results", [])
        first = items[0] if items else {}
        con.execute("INSERT OR REPLACE INTO tmdb VALUES (?, ?, ?, ?)",
                    (imdb_id, first.get("id"), first.get("original_language"), "matched" if first else "not_found"))
        con.commit()
        time.sleep(delay)
    rows = con.execute("SELECT imdb_id, tmdb_id, language, status FROM tmdb ORDER BY imdb_id").fetchall()
    pd.DataFrame(rows, columns=["tconst", "tmdb_id", "language", "status"]).to_csv(output_csv, index=False)
    con.close()


TARGET_LANGS = ("hi", "ta", "te", "ml", "kn")


def title_status(titles, candidates, labels, end_year=None, langs=TARGET_LANGS) -> pd.DataFrame:
    """Per-title fate: kept_labeled (focal language), kept_other_language (resolved, non-focal), kept_unknown
    (unresolved, ties only) or the reason it is dropped."""
    t = titles.merge(labels[["title_id", "language", "label_source"]], on="title_id", how="left")
    t["is_candidate"] = t.title_id.isin(candidates.title_id)
    t["status"] = "kept_unknown"
    t.loc[t.language.notna(), "status"] = "kept_other_language"
    t.loc[t.language.isin(langs), "status"] = "kept_labeled"
    t.loc[~t.is_candidate, "status"] = "dropped_not_indian_candidate"
    if end_year:
        t.loc[t.year > end_year, "status"] = "dropped_after_end_year"
    t["label_source"] = t.label_source.fillna("unlabeled")
    t.loc[t.status.eq("kept_unknown"), "language"] = None
    return t


KEPT = ["kept_labeled", "kept_other_language", "kept_unknown"]


def build_credits(credits_raw, status, output_path=None) -> pd.DataFrame:
    """Left join credits to labeled titles. Unresolved candidate titles are kept with language=NaN (ties only);
    titles resolved to other languages are kept with their label so source-language share is correct.
    Titles never sent to the resolver, and titles after --end-year, are dropped."""
    keep = status[status.status.isin(KEPT)]
    out = credits_raw.merge(keep[["title_id", "year", "titleType", "language", "label_source"]], on="title_id", how="inner")
    out["label_source"] = out.label_source.where(out.language.notna(), "unknown")
    out = out.rename(columns={"titleType": "title_type"}).sort_values(["year", "title_id", "billing_order"]).reset_index(drop=True)
    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        out.to_parquet(output_path, index=False)
    return out
