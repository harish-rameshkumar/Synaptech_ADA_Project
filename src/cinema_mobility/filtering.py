from __future__ import annotations
from pathlib import Path
import pandas as pd

LANGS = ("hi", "ta", "te", "ml", "kn")
MIN_YEAR = 1980


def filter_titles(basics_path, min_year=MIN_YEAR) -> pd.DataFrame:
    """Feature films and TV movies from min_year onwards, with counts after each filter."""
    b = pd.read_csv(basics_path, sep="\t", na_values="\\N", usecols=["tconst", "titleType", "startYear"],
                    dtype={"tconst": str, "titleType": str})
    print(f"basics rows:                      {len(b):>12,}")
    b = b[b.titleType.isin(["movie", "tvMovie"])]
    print(f"  movie + tvMovie:                {len(b):>12,}")
    b["year"] = pd.to_numeric(b.startYear, errors="coerce")
    b = b.dropna(subset=["year"])
    print(f"  with a start year:              {len(b):>12,}")
    b = b[b.year >= min_year]
    print(f"  year >= {min_year}:                   {len(b):>12,}")
    b["year"] = b.year.astype(int)
    return b[["tconst", "titleType", "year"]].rename(columns={"tconst": "title_id"}).reset_index(drop=True)


def filter_credits(principals_path, title_ids, max_billing=10, chunksize=2_000_000) -> pd.DataFrame:
    """Stream title.principals; keep actor/actress credits billed <= max_billing on the given titles."""
    ids = set(title_ids)
    seen = after_cat = after_bill = after_title = 0
    parts = []
    for part in pd.read_csv(principals_path, sep="\t", na_values="\\N", chunksize=chunksize,
                            usecols=["tconst", "ordering", "nconst", "category"],
                            dtype={"tconst": str, "nconst": str, "category": str}):
        seen += len(part)
        part = part[part.category.isin(["actor", "actress"])]
        after_cat += len(part)
        part = part[part.ordering.le(max_billing)]
        after_bill += len(part)
        part = part[part.tconst.isin(ids)]
        after_title += len(part)
        parts.append(part.rename(columns={"tconst": "title_id", "nconst": "actor_id",
                                          "ordering": "billing_order"})[["title_id", "actor_id", "billing_order"]])
        print(f"\r  principals rows read: {seen:>12,}", end="", flush=True)
    print()
    print(f"principals rows:                  {seen:>12,}")
    print(f"  actor/actress only:             {after_cat:>12,}")
    print(f"  billed in top {max_billing}:                {after_bill:>12,}")
    print(f"  on movie/tvMovie >= {MIN_YEAR} titles:  {after_title:>12,}")
    return pd.concat(parts, ignore_index=True).drop_duplicates(["title_id", "actor_id"])


def indian_candidates(akas_path, title_ids, langs=LANGS, chunksize=3_000_000) -> pd.DataFrame:
    """Titles with any aka in region IN or language in langs. Returns title_id + evidence flags."""
    ids = set(title_ids)
    parts = []
    for part in pd.read_csv(akas_path, sep="\t", na_values="\\N", chunksize=chunksize,
                            usecols=["titleId", "region", "language"], dtype=str):
        part = part[part.titleId.isin(ids)]
        hit = part[part.region.eq("IN") | part.language.isin(langs)]
        if len(hit):
            parts.append(pd.DataFrame({"title_id": hit.titleId, "region_in": hit.region.eq("IN"),
                                       "lang_hit": hit.language.isin(langs)}))
    out = pd.concat(parts).groupby("title_id", as_index=False).max()
    return out


def akas_summary(akas_path, title_ids, langs=LANGS, chunksize=3_000_000) -> pd.DataFrame:
    """One row per title: akas evidence used for candidate selection (T2) and the review shortlist."""
    ids = set(title_ids)
    parts = []
    for part in pd.read_csv(akas_path, sep="\t", na_values="\\N", chunksize=chunksize,
                            usecols=["titleId", "region", "language", "isOriginalTitle"], dtype=str):
        part = part[part.titleId.isin(ids)]
        parts.append(part)
    a = pd.concat(parts, ignore_index=True)
    a["is_orig"] = a.isOriginalTitle.eq("1")
    g = a.groupby("titleId")
    s = pd.DataFrame({
        "region_in": g.region.apply(lambda x: x.eq("IN").any()),
        "lang_hit": g.language.apply(lambda x: x.isin(langs).any()),
        "n_akas": g.size(),
        "akas_langs": g.language.apply(lambda x: ",".join(sorted(set(x.dropna()) & set(langs)))),
    })
    o = a[a.is_orig].drop_duplicates("titleId").set_index("titleId")
    s["orig_lang"] = o.language.reindex(s.index)
    s["orig_region"] = o.region.reindex(s.index)
    s["has_orig_row"] = s.index.isin(o.index)
    return s.reset_index().rename(columns={"titleId": "title_id"})
