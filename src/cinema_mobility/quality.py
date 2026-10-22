from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from .filtering import LANGS, MIN_YEAR
from .pipeline import qualifying_years

COLS = ["section", "key1", "key2", "n_titles", "n_dropped", "n_credits", "n_actors", "n_ties", "share", "value", "note"]
CONCENTRATION_PP = 10   # flag a group whose unknown share exceeds the overall share by this many points
CONCENTRATION_MIN_N = 300


def stage_counts(basics_path, principals_path, cache="data/interim/stage_counts.json", chunksize=2_000_000) -> dict:
    """Title/credit counts at each IMDb filter stage, plus principals title IDs missing from basics."""
    if Path(cache).exists():
        return json.loads(Path(cache).read_text())
    b = pd.read_csv(basics_path, sep="\t", na_values="\\N", usecols=["tconst", "titleType", "startYear"], dtype=str)
    all_ids = pd.Index(b.tconst)  # hashed once and reused for every chunk
    n_all = len(b)
    b = b[b.titleType.isin(["movie", "tvMovie"])]
    n_type = len(b)
    b = b[pd.to_numeric(b.startYear, errors="coerce").notna()]
    n_year = len(b)
    kept = pd.Index(b[pd.to_numeric(b.startYear) >= MIN_YEAR].tconst)
    seen = cat = bill = on_titles = 0
    orphan_ids, orphan_rows = set(), 0
    for part in pd.read_csv(principals_path, sep="\t", na_values="\\N", chunksize=chunksize,
                            usecols=["tconst", "ordering", "category"], dtype={"tconst": str, "category": str}):
        seen += len(part)
        part = part[part.category.isin(["actor", "actress"])]
        cat += len(part)
        part = part[part.ordering.le(10)]
        bill += len(part)
        miss = part[all_ids.get_indexer(part.tconst) < 0]
        orphan_rows += len(miss)
        orphan_ids |= set(miss.tconst)
        on_titles += int((kept.get_indexer(part.tconst) >= 0).sum())
    out = {"basics_rows": n_all, "movie_tvmovie": n_type, "with_year": n_year, "year_ge_1980": len(kept),
           "principals_rows": seen, "actor_actress": cat, "top10": bill, "top10_on_kept_titles": int(on_titles),
           "orphan_title_ids_in_principals": len(orphan_ids), "orphan_credit_rows": int(orphan_rows)}
    Path(cache).parent.mkdir(parents=True, exist_ok=True)
    Path(cache).write_text(json.dumps(out, indent=2))
    return out


def pair_table(credits: pd.DataFrame) -> pd.DataFrame:
    """All actor pairs sharing a title (a < b), one row per (title, pair)."""
    c = credits[["title_id", "actor_id"]].drop_duplicates()
    p = c.merge(c, on="title_id", suffixes=("_a", "_b"))
    p = p[p.actor_id_a < p.actor_id_b]
    return p.rename(columns={"actor_id_a": "a", "actor_id_b": "b"})


def connectivity(credits: pd.DataFrame) -> dict:
    """Connected components of the actor graph (actors linked by sharing a title)."""
    c = credits[["title_id", "actor_id"]].drop_duplicates()
    ai, au = pd.factorize(c.actor_id)
    ti, tu = pd.factorize(c.title_id)
    n_a, n_t = len(au), len(tu)
    g = coo_matrix((np.ones(len(c)), (ai, ti + n_a)), shape=(n_a + n_t, n_a + n_t))
    n_comp, lab = connected_components(g, directed=False)
    sizes = np.bincount(lab[:n_a], minlength=n_comp)
    sizes = sizes[sizes > 0]
    return {"n_actors": n_a, "n_titles": n_t, "n_components": int(len(sizes)),
            "largest_component_actors": int(sizes.max()), "largest_share": float(sizes.max() / n_a),
            "singleton_actors": int((sizes == 1).sum())}


def _actor_components(credits: pd.DataFrame) -> pd.Series:
    c = credits[["title_id", "actor_id"]].drop_duplicates()
    ai, au = pd.factorize(c.actor_id)
    ti, tu = pd.factorize(c.title_id)
    n_a = len(au)
    g = coo_matrix((np.ones(len(c)), (ai, ti + n_a)), shape=(n_a + len(tu), n_a + len(tu)))
    _, lab = connected_components(g, directed=False)
    return pd.Series(lab[:n_a], index=au)


def target_connectivity(credits: pd.DataFrame, focal=LANGS) -> dict:
    """Connectivity of the actors who have a focal-language credit, with and without unknown-title ties.

    Resolved other-language titles stay in both graphs; only unknown titles are toggled."""
    targets = set(credits.loc[credits.language.isin(focal), "actor_id"])
    out = {}
    for name, c in [("kept", credits), ("dropped", credits[credits.language.notna()])]:
        comp = _actor_components(c).reindex(sorted(targets))
        sizes = comp.value_counts()
        out[name] = {"n_target_actors": len(targets), "n_components": int(len(sizes)),
                     "largest_share": float(sizes.iloc[0] / len(targets)),
                     "singleton": int((sizes == 1).sum())}
    return out


def eligibility_sensitivity(credits: pd.DataFrame, focal=LANGS) -> dict:
    """70%-share rule with unknown credits excluded from the denominator (default) vs counted in it."""
    ex = qualifying_years(credits, focal, count_unknown=False)
    inc = qualifying_years(credits, focal, count_unknown=True)
    m = ex.merge(inc, on=["actor_id", "source"], how="outer", suffixes=("_excl", "_incl"), indicator=True)
    only_ex, only_inc = m[m._merge == "left_only"], m[m._merge == "right_only"]
    both = m[m._merge == "both"]
    delayed = both[both.qualifying_year_incl > both.qualifying_year_excl]
    # Of the actor-language pairs lost, how many unknown credits sat in their history?
    return {"eligible_excluded": len(ex), "eligible_included": len(inc),
            "actors_excluded": int(ex.actor_id.nunique()), "actors_included": int(inc.actor_id.nunique()),
            "pairs_lost_when_counted": len(only_ex), "pairs_gained_when_counted": len(only_inc),
            "actors_lost": int(set(ex.actor_id).difference(inc.actor_id).__len__()),
            "pairs_qualifying_later": len(delayed), "pairs_unchanged": len(both) - len(delayed),
            "median_delay_years": float((delayed.qualifying_year_incl - delayed.qualifying_year_excl).median()) if len(delayed) else 0.0,
            "pct_pairs_changed": float((len(only_ex) + len(only_inc) + len(delayed)) / max(1, len(ex)))}


def review_shortlist(credits, status, labels, summary, basics_titles, pairs) -> pd.DataFrame:
    """Kept titles that look dubbed or ambiguous, ranked by the actors and ties they affect."""
    k = status[status.status.isin(["kept_labeled", "kept_unknown"])][["title_id", "year", "language", "label_source", "status"]]
    k = k.merge(summary[["title_id", "akas_langs", "lang_hit"]], on="title_id", how="left")
    k = k.merge(labels[["title_id", "wd_status"]], on="title_id", how="left")
    n_akas = k.akas_langs.fillna("").map(lambda s: len([x for x in s.split(",") if x]))
    reasons = pd.DataFrame({
        "multi_language_akas": n_akas >= 2,
        "label_not_in_akas_languages": k.language.notna() & (n_akas >= 1)
                                       & ~k.apply(lambda r: r.language in (r.akas_langs or "").split(","), axis=1),
        "unknown_with_target_language_akas": k.language.isna() & (n_akas >= 1),
        "wikidata_multiple_languages": k.wd_status.eq("multiple_languages"),
    })
    k["reason"] = reasons.apply(lambda r: ";".join(r.index[r.values]), axis=1)
    k = k[k.reason != ""]
    n_act = credits.groupby("title_id").actor_id.nunique().rename("n_actors")
    uniq = pairs.merge(pairs.groupby(["a", "b"]).size().rename("n").reset_index(), on=["a", "b"])
    n_unique = uniq[uniq.n == 1].groupby("title_id").size().rename("n_ties_lost_if_dropped")
    k = k.join(n_act, on="title_id").join(n_unique, on="title_id")
    k["n_ties"] = k.n_actors * (k.n_actors - 1) // 2
    k["n_ties_lost_if_dropped"] = k.n_ties_lost_if_dropped.fillna(0).astype(int)
    k = k.merge(basics_titles, on="title_id", how="left")
    k = k.sort_values(["n_ties_lost_if_dropped", "n_actors", "n_ties"], ascending=False).reset_index(drop=True)
    k.insert(0, "rank", k.index + 1)
    k["is_dubbed"], k["is_ambiguous"] = "", ""
    k = k.rename(columns={"title_id": "tconst", "label_source": "current_label_source", "language": "language"})
    return k[["rank", "tconst", "primaryTitle", "year", "language", "current_label_source", "akas_langs", "reason",
              "n_actors", "n_ties", "n_ties_lost_if_dropped", "is_dubbed", "is_ambiguous"]]


def make_sample(credits: pd.DataFrame, target=5000, per_lang=None, seed=42, langs=LANGS) -> pd.DataFrame:
    """~target credits, built from whole title casts around randomly chosen eligible actors so ties survive.

    Only ids, years, billing and language labels are kept (no titles, names or API payloads).
    """
    rng = np.random.default_rng(seed)
    lab = credits[credits.language.notna()]
    elig = (lab.groupby(["actor_id", "language"]).agg(n=("title_id", "size"), y=("year", "nunique")).reset_index()
            .query("n >= 5 and y >= 3"))
    cast = credits.groupby("title_id").actor_id.apply(list)
    chosen, total = [], 0
    per_lang = per_lang or target // len(langs)
    for lang in langs:
        pool = elig[elig.language == lang].actor_id.to_numpy()
        rng.shuffle(pool)
        lang_total = 0
        for a in pool:
            t = credits.loc[credits.actor_id == a, "title_id"].unique()
            new = [x for x in t if x not in set(chosen)]
            add = int(cast.reindex(new).map(len).sum())
            if lang_total + add > per_lang * 1.2 and lang_total > 0:
                continue
            chosen += new
            lang_total += add
            if lang_total >= per_lang:
                break
        total += lang_total
    out = credits[credits.title_id.isin(set(chosen))]
    return out.sort_values(["year", "title_id", "billing_order"]).reset_index(drop=True)


def data_quality(status, labels, summary, credits, raw_stages, pairs, scenarios, extra=None, sensitivity=None, extra_rows=None) -> pd.DataFrame:
    rows = []

    def add(section, key1="", key2="", **kw):
        rows.append({"section": section, "key1": key1, "key2": key2, **kw})

    # --- stages: titles dropped and why
    s = raw_stages
    n_cr = s["top10_on_kept_titles"]
    stages = [("basics rows", s["basics_rows"], None, ""),
              ("movie + tvMovie", s["movie_tvmovie"], s["basics_rows"] - s["movie_tvmovie"], "other title types (episodes, shorts, series...)"),
              ("has start year", s["with_year"], s["movie_tvmovie"] - s["with_year"], "missing startYear"),
              ("year >= 1980", s["year_ge_1980"], s["with_year"] - s["year_ge_1980"], "before 1980"),
              ("has top-10 actor credit", len(status), s["year_ge_1980"] - len(status), "no actor/actress credit billed in top 10")]
    # Drop order: end-year cut first (all titles), then the candidate rule on what remains.
    left = len(status)
    after = status.status.eq("dropped_after_end_year")
    if after.any():
        left -= int(after.sum())
        stages.append(("after --end-year", left, int(after.sum()), "title year after --end-year"))
    notc = int((~status.is_candidate & ~after).sum())
    left -= notc
    stages.append(("Indian candidate (IN-region or focal-language aka)", left, notc,
                   "never sent to TMDB; recall check found 1/2000 focal-language titles outside the rule"))
    for name, n, dropped, note in stages:
        add("stage", name, n_titles=n, n_dropped=dropped, note=note)
    add("stage", "kept: labeled focal language", n_titles=int((status.status == "kept_labeled").sum()), note="language events and eligibility")
    add("stage", "kept: resolved to a non-focal language", n_titles=int((status.status == "kept_other_language").sum()),
        note="kept with its label: counts in the source-share denominator and other-language features, never a source or destination")
    add("stage", "kept: unknown (ties only)", n_titles=int((status.status == "kept_unknown").sum()),
        note="in graph as ties; excluded from language events and eligibility")
    add("orphan_ids", "principals title IDs missing from basics (possible merged/removed IDs)",
        n_titles=s["orphan_title_ids_in_principals"], n_credits=s["orphan_credit_rows"],
        note="IMDb dumps use current IDs; no redirect table is shipped, so these cannot be remapped")

    # --- tiers among the candidates sent to the resolver
    cand = labels[labels.title_id.isin(status.loc[~status.status.eq("dropped_after_end_year"), "title_id"])].copy()
    n = len(cand)
    for tier in ["T1", "T2", "unknown"]:
        m = cand.label_source == tier
        add("tier_overall", tier, n_titles=int(m.sum()), share=m.mean(), note="share of candidate titles")
    dup = labels[labels.label_source == "T1"].dropna(subset=["tmdb_id"]).groupby("tmdb_id").title_id.nunique()
    add("merged_ids", "T1 titles sharing a TMDB id with another IMDb id (likely merged/duplicate)",
        n_titles=int(dup[dup > 1].sum()), value=int((dup > 1).sum()), note="value = number of shared TMDB ids")

    # --- match rate per tier by language (label language) and unknown by akas evidence
    lab = cand[cand.language.notna()].copy()
    lab["lang_group"] = lab.language.where(lab.language.isin(LANGS), "other")
    for lang, g in lab.groupby("lang_group"):
        for tier in ["T1", "T2"]:
            add("tier_by_language", lang, tier, n_titles=int((g.label_source == tier).sum()),
                share=(g.label_source == tier).mean(), note="share of titles labeled with this language")
    ev = cand.merge(summary[["title_id", "akas_langs"]], on="title_id", how="left")
    ev["ev"] = ev.akas_langs.fillna("").map(lambda x: x if x == "" or "," not in x else "multiple")
    ev["ev"] = ev.ev.replace("", "none (IN region only)")
    overall_unk = (cand.label_source == "unknown").mean()
    for k, g in ev.groupby("ev"):
        u = (g.label_source == "unknown").mean()
        flag = "CONCENTRATED" if (u - overall_unk) * 100 >= CONCENTRATION_PP and len(g) >= CONCENTRATION_MIN_N else ""
        add("unknown_by_akas_language", k, n_titles=len(g), share=u, value=int((g.label_source == "unknown").sum()), note=flag)

    # --- by year
    cy = cand.merge(status[["title_id", "year"]], on="title_id")
    for y, g in cy.groupby("year"):
        for tier in ["T1", "T2", "unknown"]:
            add("tier_by_year", int(y), tier, n_titles=int((g.label_source == tier).sum()), share=(g.label_source == tier).mean(),
                note="share of that year's candidates")
    cy["period"] = (cy.year // 5 * 5).astype(str) + "-" + (cy.year // 5 * 5 + 4).astype(str)
    for per, g in cy.groupby("period"):
        u = (g.label_source == "unknown").mean()
        flag = "CONCENTRATED" if (u - overall_unk) * 100 >= CONCENTRATION_PP and len(g) >= CONCENTRATION_MIN_N else ""
        add("unknown_by_period", per, n_titles=len(g), share=u, value=int((g.label_source == "unknown").sum()), note=flag)

    # --- contribution of each tier to the final credits
    tp = pairs.merge(credits[["title_id", "label_source"]].drop_duplicates("title_id"), on="title_id")
    pair_tiers = tp.groupby(["a", "b"]).label_source.agg(lambda x: tuple(sorted(set(x))))
    actor_tiers = credits.groupby("actor_id").label_source.agg(lambda x: tuple(sorted(set(x))))
    order = ["T1", "T2", "reviewed", "unknown", "review_excluded", "rule_ambiguous"]
    for tier in [t for t in order if t in set(credits.label_source)]:
        c = credits[credits.label_source == tier]
        add("tier_contribution", tier, n_titles=c.title_id.nunique(), n_credits=len(c), n_actors=c.actor_id.nunique(),
            n_ties=int(tp[tp.label_source == tier][["a", "b"]].drop_duplicates().shape[0]),
            note="n_ties = distinct actor pairs with a shared title of this tier")
        add("tier_exclusive", tier, n_actors=int((actor_tiers == (tier,)).sum()), n_ties=int((pair_tiers == (tier,)).sum()),
            note="actors / ties reachable ONLY through titles of this tier")

    # --- graph connectivity
    for name, r in scenarios.items():
        add("connectivity", name, n_titles=r["n_titles"], n_actors=r["n_actors"], share=r["largest_share"],
            value=r["n_components"], note=f"value = components; largest has {r['largest_component_actors']:,} actors; {r['singleton_actors']:,} singleton actors")
    for name, r in (extra or {}).items():
        add("connectivity_target_actors", name, n_actors=r["n_target_actors"], share=r["largest_share"],
            value=r["n_components"], note=f"value = components among target-language actors; {r['singleton']:,} singletons")
    if sensitivity:
        z = sensitivity
        add("eligibility_sensitivity", "70% share, unknown EXCLUDED from denominator (default)", n_actors=z["actors_excluded"], value=z["eligible_excluded"],
            note="value = eligible actor-source-language pairs")
        add("eligibility_sensitivity", "70% share, unknown COUNTED in denominator", n_actors=z["actors_included"], value=z["eligible_included"],
            note="value = eligible actor-source-language pairs")
        add("eligibility_sensitivity", "pairs eligible only when unknown excluded", value=z["pairs_lost_when_counted"], note="lose eligibility if unknown credits count")
        add("eligibility_sensitivity", "actors losing every source language", n_actors=z["actors_lost"])
        add("eligibility_sensitivity", "pairs qualifying later when unknown counted", value=z["pairs_qualifying_later"],
            note=f"median delay {z['median_delay_years']:.0f} yr")
        add("eligibility_sensitivity", "pairs eligible only when unknown counted", value=z["pairs_gained_when_counted"], note="should be 0: extra credits cannot raise the share")
        add("eligibility_sensitivity", "share of default pairs whose status or year changes", share=z["pct_pairs_changed"])
    rows.extend(extra_rows or [])
    return pd.DataFrame(rows).reindex(columns=COLS)


def wilson(x: int, n: int, z: float = 1.96):
    p = x / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return p, max(0.0, centre - half), centre + half


def _row(section, key1="", key2="", **kw):
    return {"section": section, "key1": key1, "key2": key2, **kw}


def control_validation_rows(control: pd.DataFrame) -> list[dict]:
    """Validation of the evidence pre-fill on the random control sample (rows that had evidence only)."""
    noev = control.notes.str.contains("no evidence found")
    ev = ~noev
    n = int(ev.sum())
    changed = int(((control.corrected_language != "") & (control.language != "") & ev).sum())
    filled = int(((control.corrected_language != "") & (control.language == "")).sum())
    p, lo, hi = wilson(changed, n)
    rows = [_row("control_validation", "control rows", n_titles=len(control)),
            _row("control_validation", "rows with evidence", n_titles=n),
            _row("control_validation", "rows with no evidence found", n_titles=int(noev.sum()),
                 note=f"{int((noev & (control.language == '')).sum())} of them had no label to begin with"),
            _row("control_validation", "existing labels changed", n_titles=changed, share=p,
                 note=f"{changed}/{n}; 95% Wilson CI {lo:.1%}-{hi:.1%}"),
            _row("control_validation", "unknown labels filled from Wikipedia", n_titles=filled),
            _row("control_validation", "ambiguous flags (pre-fill)", n_titles=int((control.is_ambiguous == "1").sum())),
            _row("control_validation", "dubbed flags (pre-fill)", n_titles=int((control.is_dubbed == "1").sum()))]
    rows += [_row("control_validation", "confidence", lvl, n_titles=int((control.confidence == lvl).sum())) for lvl in ["high", "medium", "low"]]
    return rows


def recall_check_rows(cache="data/raw/recall_cache.sqlite", focal=LANGS) -> list[dict]:
    """Share of random non-candidate titles that TMDB says are in a focal language (candidate-rule recall check)."""
    import sqlite3
    if not Path(cache).exists():
        return []
    d = pd.read_sql("SELECT imdb_id, language FROM tmdb", sqlite3.connect(cache))
    x = int(d.language.isin(focal).sum())
    p, lo, hi = wilson(x, len(d))
    return [_row("recall_check", "non-candidate titles looked up on TMDB", n_titles=len(d)),
            _row("recall_check", "focal-language titles outside the candidate rule", n_titles=x, share=p,
                 note=f"{x}/{len(d)}; 95% Wilson CI {lo:.2%}-{hi:.2%}")]


def rule_rows(rules_tbl: pd.DataFrame, credits_baseline: pd.DataFrame, control_eval: pd.DataFrame | None, sens: pd.DataFrame | None,
              primary) -> list[dict]:
    rows = []
    per_title = credits_baseline.groupby("title_id").size()
    n = len(rules_tbl)
    for r in [*(c[5:] for c in rules_tbl.columns if c.startswith("rule_"))]:
        f = rules_tbl.title_id[rules_tbl[f"rule_{r}"]]
        rows.append(_row("rule_flags", r, n_titles=len(f), n_credits=int(per_title.reindex(f).sum()), share=len(f) / n,
                         note="share of focal-labeled titles" + (" (in the primary set)" if r in primary else "")))
    mask = np.zeros(n, dtype=bool)
    for r in primary:
        mask |= rules_tbl[f"rule_{r}"].to_numpy()
    f = rules_tbl.title_id[mask]
    rows.append(_row("rule_flags", "primary set: " + "+".join(primary), n_titles=len(f), n_credits=int(per_title.reindex(f).sum()),
                     share=len(f) / n, note="titles kept as ties only in credits_final"))
    if control_eval is not None:
        for _, r in control_eval.iterrows():
            rows.append(_row("rule_control_validation", r["rule"], "precision", n_titles=int(r["flagged"]), value=r["precision"],
                             note=f"flagged {int(r['flagged'])}, true positives {int(r['true_positive'])} of {int(r['manual_flags'])} pre-fill flags"))
            rows.append(_row("rule_control_validation", r["rule"], "recall", n_titles=int(r["flagged"]), value=r["recall"]))
    if sens is not None:
        none_events = int(sens.loc[sens.rules == "none", "total_events"].iloc[0])
        for _, r in sens.iterrows():
            rows.append(_row("rule_effect", r["rules"], n_titles=int(r["titles_flagged"]), n_credits=int(r["credits_flagged"]),
                             n_actors=int(r["eligible_actors"]), value=int(r["total_events"]), share=r["total_events"] / none_events - 1,
                             note=f"eligible actor-language pairs {int(r['eligible_actor_language_pairs'])}; at-risk actor-years {int(r['total_at_risk_actor_years'])}; "
                                  f"min pair events {int(r['min_pair_events'])}; pairs meeting default thresholds {int(r['pairs_meeting_default_thresholds'])}"))
    return rows


def version_rows(versions: pd.DataFrame) -> list[dict]:
    return [_row("versions", r.version, n_titles=r.titles, n_credits=r.credits, n_actors=r.actors,
                 note=f"{r.file}; rules={r.rules}; focal credits {r.focal_language_credits:,}; ties-only credits {r.tie_only_credits:,}")
            for r in versions.itertuples()]
