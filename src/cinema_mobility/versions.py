"""Named data versions, so teammates can pick a table by name (data/processed/data_versions.csv)."""
from __future__ import annotations
from pathlib import Path
import pandas as pd

from .ingest import TARGET_LANGS
from .review import sha256

# name, file, rules, manual corrections applied, use, description
REGISTRY = [
    ("pre_review", "credits_pre_review.parquet", "none", False, "audit",
     "Left-joined labels, no manual corrections, no ambiguity rules (state before any review)."),
    ("baseline", "credits.parquet", "none", True, "sensitivity: no rules",
     "No-rules baseline. Includes the 2 control corrections (Danny Jigar gu, Kanulu Therichinaa Kanulu Moosinaa te)."),
    ("final", "credits_final.parquet", "spoken+wikidata_focal+disagree", True, "PRIMARY analysis",
     "Baseline plus rules (a) TMDB spoken>=2 focal, (b-focal) Wikidata>=2 focal, (c) TMDB/Wikidata disagree: flagged titles are ties only."),
    ("final_v2", "credits_final_v2.parquet", "spoken_wiki+wikidata_focal+disagree", True, "candidate primary (refined rule a2)",
     "Like final, but rule (a) is refined to (a2): TMDB spoken>=2 focal AND the IMDb-matched Wikipedia infobox also lists >=2 focal languages."),
    ("sample_final_v2", "credits_final_v2_sample.parquet", "spoken_wiki+wikidata_focal+disagree", True, "development sample of final_v2",
     "~5,000 credits from whole title casts around eligible actors; ids, years, billing and labels only."),
    ("sample_final", "credits_final_sample.parquet", "spoken+wikidata_focal+disagree", True, "development sample of final",
     "~5,000 credits from whole title casts around eligible actors; ids, years, billing and labels only."),
    ("sample_baseline", "credits_sample.parquet", "none", False, "older development sample",
     "Sample built from the baseline before the 2 control corrections; kept so existing work still runs."),
]


def data_versions(directory="data/processed", focal=TARGET_LANGS) -> pd.DataFrame:
    rows = []
    for name, file, rules, corrected, use, desc in REGISTRY:
        p = Path(directory) / file
        if not p.exists():
            continue
        c = pd.read_parquet(p)
        lang = c.language
        rule_flagged = c.label_source.eq("rule_ambiguous")
        rows.append({
            "version": name, "file": file, "use_for": use, "rules": rules, "manual_corrections_applied": corrected,
            "end_year": int(c.year.max()), "focal_languages": ",".join(focal),
            "credits": len(c), "titles": c.title_id.nunique(), "actors": c.actor_id.nunique(),
            "focal_language_credits": int(lang.isin(focal).sum()),
            "other_language_credits": int((lang.notna() & ~lang.isin(focal)).sum()),
            "tie_only_credits": int(lang.isna().sum()),
            "rule_flagged_credits": int(rule_flagged.sum()), "rule_flagged_titles": c.title_id[rule_flagged].nunique(),
            "sha256": sha256(p), "description": desc})
    return pd.DataFrame(rows)
