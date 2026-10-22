from __future__ import annotations
import hashlib
import shutil
from itertools import combinations
from pathlib import Path
import numpy as np
import pandas as pd

from .ingest import title_status, build_credits

REVIEW_COLS = ["tconst", "primaryTitle", "year", "language", "current_label_source", "akas_langs", "reason",
               "imdb_url", "is_dubbed", "is_ambiguous", "corrected_language", "notes", "reviewer"]
CODEBOOK = Path(__file__).resolve().parents[2] / "docs" / "review_codebook.md"
TRUE, FALSE = {"1", "true", "yes", "y", "t", "x"}, {"0", "false", "no", "n", "f"}


# --------------------------------------------------------------------------- packet
def make_packet(review_csv, outdir, reviewers=("A", "B", "C"), top=1500, overlap=100, control=200, seed=42):
    """Split the top-`top` titles (by impact) into equal files, add a shared blind overlap set and a control sample.

    * Top titles are dealt round-robin by rank, so each reviewer gets the same impact mix.
    * The overlap set is drawn at random from the titles *below* the top block and is added to every file.
    * The control sample is drawn at random from what is left, for a separate reviewer.
    * Rows in each file are shuffled and nothing marks which ones are shared; the key stays with the Data Lead.
    Refuses to write over anything that already exists.
    """
    r = pd.read_csv(review_csv).sort_values("rank").reset_index(drop=True)
    k = len(reviewers)
    if top % k:
        raise ValueError("top must divide evenly among reviewers")
    rest = r.iloc[top:]
    if len(rest) < overlap + control:
        raise ValueError(f"only {len(rest)} titles below the top block; need {overlap + control}")
    rng = np.random.default_rng(seed)
    pick = rng.permutation(len(rest))
    ov = rest.iloc[pick[:overlap]]
    ctl = rest.iloc[pick[overlap:overlap + control]]
    out = Path(outdir)
    targets = [out / f"review_{name}.csv" for name in reviewers] + [out / "review_control.csv", out / "packet_key.csv", out / "CODEBOOK.md"]
    clash = [str(p) for p in targets if p.exists()]
    if clash:
        raise FileExistsError(f"refusing to overwrite existing packet files: {clash}")
    out.mkdir(parents=True, exist_ok=True)

    def prep(df, name):
        d = df.copy()
        d["imdb_url"] = "https://www.imdb.com/title/" + d.tconst + "/"
        d["is_dubbed"] = d["is_ambiguous"] = d["corrected_language"] = d["notes"] = ""
        d["reviewer"] = name
        return d[REVIEW_COLS].sample(frac=1, random_state=int(rng.integers(1 << 31))).reset_index(drop=True)

    key = []
    top_block = r.iloc[:top]
    for i, name in enumerate(reviewers):
        own = top_block.iloc[i::k]
        prep(pd.concat([own, ov]), name).to_csv(out / f"review_{name}.csv", index=False)
        key += [{"tconst": t, "set": f"unique:{name}"} for t in own.tconst]
    prep(ctl, "control").to_csv(out / "review_control.csv", index=False)
    key += [{"tconst": t, "set": "overlap"} for t in ov.tconst] + [{"tconst": t, "set": "control"} for t in ctl.tconst]
    key += [{"tconst": t, "set": "unreviewed"} for t in rest.tconst if t not in set(ov.tconst) | set(ctl.tconst)]
    pd.DataFrame(key).to_csv(out / "packet_key.csv", index=False)
    if CODEBOOK.exists():
        shutil.copyfile(CODEBOOK, out / "CODEBOOK.md")
    return {"per_reviewer": len(top_block) // k + overlap, "unique_each": len(top_block) // k, "overlap": overlap,
            "control": control, "unreviewed": len(rest) - overlap - control}


# --------------------------------------------------------------------------- reading reviews
def _parse_flag(series, file, col):
    s = series.astype("string").str.strip().str.lower()
    bad = s.notna() & (s != "") & ~s.isin(TRUE | FALSE)
    if bad.any():
        raise ValueError(f"{file}: column {col} has values that are not 1/0/yes/no: {sorted(set(s[bad]))[:5]}")
    return s.map(lambda x: True if x in TRUE else False if x in FALSE else pd.NA).astype("boolean")


def read_review(path) -> pd.DataFrame:
    """Read one reviewed CSV (read-only) into tconst, reviewer, is_dubbed, is_ambiguous, corrected_language."""
    path = Path(path)
    d = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = {"tconst", "is_dubbed", "is_ambiguous"} - set(d.columns)
    if missing:
        raise ValueError(f"{path}: missing columns {sorted(missing)}")
    if d.tconst.duplicated().any():
        raise ValueError(f"{path}: duplicate tconst rows")
    reviewer = d["reviewer"].iloc[0] if "reviewer" in d and len(d) and d["reviewer"].iloc[0] else path.stem
    corr = d["corrected_language"].str.strip().str.lower() if "corrected_language" in d else pd.Series("", index=d.index)
    return pd.DataFrame({"tconst": d.tconst, "reviewer": reviewer,
                         "is_dubbed": _parse_flag(d.is_dubbed, path, "is_dubbed"),
                         "is_ambiguous": _parse_flag(d.is_ambiguous, path, "is_ambiguous"),
                         "corrected_language": corr.replace("", pd.NA)})


def consensus(votes: pd.DataFrame) -> pd.DataFrame:
    """One decision per title. A field is true when a strict majority of non-blank votes say so.

    A split vote (e.g. 1-1) is a conflict: the title is excluded as ambiguous and listed for adjudication.
    A title with only blank votes is not reviewed and is left alone.
    """
    rows = []
    for t, g in votes.groupby("tconst", sort=False):
        res = {"tconst": t, "n_reviewers": g.reviewer.nunique(), "conflict": False}
        reviewed = g[["is_dubbed", "is_ambiguous"]].notna().any(axis=1)
        res["reviewed"] = bool(reviewed.any())
        for col in ["is_dubbed", "is_ambiguous"]:
            v = g[col].dropna().astype(bool)
            yes, n = int(v.sum()), len(v)
            res[col] = bool(yes * 2 > n)
            res["conflict"] |= bool(n > 0 and yes * 2 == n)
        langs = g.corrected_language.dropna()
        top = langs.value_counts()
        res["corrected_language"] = top.index[0] if len(top) and (len(top) == 1 or top.iloc[0] > top.iloc[1]) else pd.NA
        res["exclude"] = res["reviewed"] and (res["is_dubbed"] or res["is_ambiguous"] or res["conflict"])
        rows.append(res)
    cols = ["tconst", "n_reviewers", "conflict", "reviewed", "is_dubbed", "is_ambiguous", "corrected_language", "exclude"]
    return pd.DataFrame(rows, columns=cols).astype({"conflict": bool, "reviewed": bool, "is_dubbed": bool, "is_ambiguous": bool, "exclude": bool})


# --------------------------------------------------------------------------- agreement
def fleiss_kappa(ratings: np.ndarray) -> float:
    """ratings: subjects x raters matrix of 0/1."""
    n_sub, n = ratings.shape
    counts = np.stack([(ratings == 0).sum(1), (ratings == 1).sum(1)], axis=1)
    p_i = ((counts ** 2).sum(1) - n) / (n * (n - 1))
    p_j = counts.sum(0) / (n_sub * n)
    pe = float((p_j ** 2).sum())
    return float("nan") if pe >= 1 else float((p_i.mean() - pe) / (1 - pe))


def cohen_kappa(a: np.ndarray, b: np.ndarray) -> float:
    po = float((a == b).mean())
    pe = float(sum(((a == v).mean() * (b == v).mean()) for v in (0, 1)))
    return float("nan") if pe >= 1 else (po - pe) / (1 - pe)


def agreement_report(reviews: list[pd.DataFrame]) -> pd.DataFrame:
    """Kappa on titles rated by every reviewer. Fields: is_dubbed, is_ambiguous, and exclude (either)."""
    long = pd.concat(reviews)
    names = sorted(long.reviewer.unique())
    long["exclude"] = (long.is_dubbed.fillna(False).astype(bool) | long.is_ambiguous.fillna(False).astype(bool)).where(
        long.is_dubbed.notna() | long.is_ambiguous.notna(), pd.NA)
    rows = []
    for col in ["is_dubbed", "is_ambiguous", "exclude"]:
        wide = long.pivot_table(index="tconst", columns="reviewer", values=col, aggfunc="first")
        wide = wide.dropna().astype(int)
        if len(wide) < 2 or len(names) < 2:
            rows.append({"field": col, "n_titles": len(wide), "raters": len(names), "fleiss_kappa": np.nan, "pct_agree": np.nan})
            continue
        m = wide.to_numpy()
        row = {"field": col, "n_titles": len(wide), "raters": len(names),
               "fleiss_kappa": fleiss_kappa(m) if len(names) > 2 else cohen_kappa(m[:, 0], m[:, 1]),
               "pct_agree": float((m == m[:, [0]]).all(1).mean()), "positive_rate": float(m.mean())}
        for a, b in combinations(range(len(names)), 2):
            row[f"cohen_{names[a]}_{names[b]}"] = cohen_kappa(m[:, a], m[:, b])
        rows.append(row)
    return pd.DataFrame(rows)


def control_error_rate(control: pd.DataFrame, n_unreviewed: int) -> dict:
    """Share of randomly sampled unreviewed titles that a reviewer would exclude, with a Wilson 95% interval."""
    c = control[control.reviewed]
    n, x = len(c), int(c.exclude.sum())
    if n == 0:
        return {}
    p, z = x / n, 1.96
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return {"n_control_reviewed": n, "n_flagged": x, "error_rate": p, "ci_low": centre - half, "ci_high": centre + half,
            "n_unreviewed_titles": n_unreviewed, "expected_flagged_unreviewed": p * n_unreviewed,
            "expected_low": (centre - half) * n_unreviewed, "expected_high": (centre + half) * n_unreviewed}


# --------------------------------------------------------------------------- apply
def apply_review(decisions, titles, candidates, labels, raw, end_year=None, focal=("hi", "ta", "te", "ml", "kn"),
                 excluded_as="ties"):
    """Rebuild the credits table with reviewed decisions applied. Pure: reads frames, returns frames.

    excluded_as="ties": titles marked dubbed/ambiguous (or in unresolved conflict) keep their credits as
    collaboration ties only (language null, label_source review_excluded), like unknown titles.
    excluded_as="drop": their credits are removed from the table entirely.
    `corrected_language` replaces the label (label_source "reviewed") unless the title is excluded.
    """
    lab = labels.copy()
    d = decisions[decisions.reviewed.astype(bool)].copy()
    d["exclude"] = d.exclude.astype(bool)
    corr = d[d.corrected_language.notna() & ~d.exclude].set_index("tconst").corrected_language
    m = lab.title_id.isin(corr.index)
    lab.loc[m, "language"] = lab.loc[m, "title_id"].map(corr)
    lab.loc[m, "label_source"] = "reviewed"
    status = title_status(titles, candidates, lab, end_year, focal)
    flagged = set(d[d.exclude].tconst)
    kept = status.status.str.startswith("kept")
    hit = status.title_id.isin(flagged) & kept
    if excluded_as == "drop":
        status.loc[hit, "status"] = "dropped_reviewed_exclusion"
    else:
        status.loc[hit, ["status", "language"]] = ["kept_unknown", None]
    new = build_credits(raw, status)
    if excluded_as != "drop":
        new.loc[new.title_id.isin(flagged), "label_source"] = "review_excluded"
    return new, status, lab


def credit_changes(baseline: pd.DataFrame, new: pd.DataFrame) -> dict:
    """Titles and credits whose language differs from the baseline table (NaN-safe), or that were removed."""
    b = baseline[["title_id", "actor_id", "language"]].merge(
        new[["title_id", "actor_id", "language", "label_source"]], on=["title_id", "actor_id"], how="left",
        suffixes=("_old", "_new"), indicator=True)
    removed = b._merge == "left_only"
    differs = ~removed & (b.language_old.fillna("∅") != b.language_new.fillna("∅"))
    changed = b[removed | differs]
    return {"credits_before": len(baseline), "credits_after": len(new),
            "credits_changed": len(changed), "credits_removed": int(removed.sum()),
            "credits_language_changed": int(differs.sum()),
            "titles_changed": int(changed.title_id.nunique()),
            "titles_to_ties_only": int(changed[~changed._merge.eq("left_only") & changed.language_new.isna()].title_id.nunique()),
            "titles_relabelled": int(changed[changed.language_new.notna()].title_id.nunique()),
            "actors_affected": int(changed.actor_id.nunique())}


def sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
