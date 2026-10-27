"""Match rate per tier by release year, decade, original language and akas evidence (read-only).

Long format, one row per group x tier. `share_of_group` = titles / `group_titles`, where the group is every candidate
title in that cut. Unknown titles have no language, so they appear only in the year, decade and akas-evidence cuts.
Rule-flagged titles are placed in a language group by the label they had before the rule fired (ambiguity_rules).
Refuses to overwrite an existing --out.
"""
import argparse
from pathlib import Path
import numpy as np
import pandas as pd

FOCAL = ["hi", "ta", "te", "ml", "kn"]
TIERS = ["T1", "T2", "reviewed", "rule_ambiguous", "unknown"]


def decade(y):
    return "2020-24" if y >= 2020 else f"{y // 10 * 10}s"


def match_rates(credits, rules, akas):
    t = credits.drop_duplicates("title_id")[["title_id", "year", "language", "label_source"]]
    t = t.merge(rules[["title_id", "language"]].rename(columns={"language": "pre_lang"}), on="title_id", how="left")
    lang = t.language.fillna(t.pre_lang.where(t.label_source == "rule_ambiguous"))
    t["lgrp"] = lang.map(lambda x: np.nan if pd.isna(x) else (x if x in FOCAL else "other"))
    t = t.merge(akas[["title_id", "region_in", "lang_hit"]], on="title_id", how="left")
    t["akas"] = np.where(t.lang_hit, "focal_language_alt_title", np.where(t.region_in, "IN_region_only", "neither"))
    t["decade"], t["year_s"], t["all"] = t.year.map(decade), t.year.astype(int).astype(str), "all candidate titles"
    rows = []

    def emit(dim, df, group, per, tiers=TIERS):
        for (g, p), d in df.groupby([group, per]):
            vc = d.label_source.value_counts()
            rows.extend((dim, g, p, tr, int(vc.get(tr, 0)), int(vc.get(tr, 0)) / len(d), len(d)) for tr in tiers)

    emit("year", t, "all", "year_s")
    emit("decade", t, "all", "decade")
    emit("language_x_decade", t[t.lgrp.notna()], "lgrp", "decade", TIERS[:4])
    emit("akas_evidence_x_decade", t, "akas", "decade")
    out = pd.DataFrame(rows, columns=["dimension", "group", "year_or_decade", "tier", "titles", "share_of_group", "group_titles"])
    out["share_of_group"] = out.share_of_group.round(6)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--credits", default="data/processed/credits_final_v2.parquet")
    ap.add_argument("--rules", default="data/interim/ambiguity_rules.parquet")
    ap.add_argument("--akas", default="data/interim/akas_summary.parquet")
    ap.add_argument("--out", default="results/match_rates_final_v2.csv")
    a = ap.parse_args()
    if Path(a.out).exists():
        raise SystemExit(f"{a.out} exists; not overwriting")
    out = match_rates(pd.read_parquet(a.credits), pd.read_parquet(a.rules), pd.read_parquet(a.akas))
    out.to_csv(a.out, index=False)
    print(f"Wrote {len(out)} rows to {a.out}")
