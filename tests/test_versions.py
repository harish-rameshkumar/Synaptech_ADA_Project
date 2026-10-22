import pandas as pd
import pytest
from cinema_mobility import quality, versions


def test_wilson_matches_reported_control_interval():
    p, lo, hi = quality.wilson(0, 157)
    assert p == 0 and lo == 0 and hi == pytest.approx(0.0238, abs=0.0005)   # "0/157, 95% CI 0-2.4%"
    p, lo, hi = quality.wilson(1, 2000)
    assert hi == pytest.approx(0.0028, abs=0.0003)


def test_control_validation_rows_count_only_evidence_rows():
    c = pd.DataFrame({"language": ["ta", "ta", "", "", "te"], "corrected_language": ["", "", "kn", "", ""],
                      "notes": ["TMDB x", "TMDB y", "Wikipedia z", "no evidence found", "TMDB w"],
                      "is_ambiguous": ["0", "1", "0", "0", "0"], "is_dubbed": "0", "confidence": ["high", "low", "medium", "low", "high"]})
    rows = {r["key1"] + r["key2"]: r for r in quality.control_validation_rows(c)}
    assert rows["rows with evidence"]["n_titles"] == 4 and rows["rows with no evidence found"]["n_titles"] == 1
    assert rows["existing labels changed"]["n_titles"] == 0 and rows["unknown labels filled from Wikipedia"]["n_titles"] == 1
    assert rows["confidencehigh"]["n_titles"] == 2 and rows["ambiguous flags (pre-fill)"]["n_titles"] == 1


def test_data_versions_lists_each_file_with_rule_counts(tmp_path):
    base = pd.DataFrame({"title_id": ["a", "b", "c"], "actor_id": ["x", "y", "z"], "billing_order": 1, "year": [2000, 2001, 2002],
                         "language": ["ta", None, "en"], "label_source": ["T1", "unknown", "T1"]})
    final = base.copy()
    final.loc[0, ["language", "label_source"]] = [None, "rule_ambiguous"]
    base.to_parquet(tmp_path / "credits.parquet")
    final.to_parquet(tmp_path / "credits_final.parquet")
    v = versions.data_versions(tmp_path).set_index("version")
    assert list(v.index) == ["baseline", "final"]                   # files that do not exist are skipped
    assert v.loc["baseline", "rules"] == "none" and v.loc["final", "rules"] == "spoken+wikidata_focal+disagree"
    assert v.loc["baseline", "focal_language_credits"] == 1 and v.loc["final", "focal_language_credits"] == 0
    assert v.loc["final", "rule_flagged_credits"] == 1 and v.loc["final", "rule_flagged_titles"] == 1
    assert v.loc["final", "tie_only_credits"] == 2 and v.loc["final", "other_language_credits"] == 1
    assert v.loc["final", "use_for"] == "PRIMARY analysis" and len(v.loc["final", "sha256"]) == 64
