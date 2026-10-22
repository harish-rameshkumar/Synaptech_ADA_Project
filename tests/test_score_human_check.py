import importlib.util
from pathlib import Path
import pandas as pd

spec = importlib.util.spec_from_file_location("score_human_check", Path(__file__).resolve().parents[1] / "scripts" / "score_human_check.py")
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)


def _files(tmp_path, answers):
    key = pd.DataFrame({"tconst": ["t1", "t2", "t3"], "primaryTitle": ["A", "B", "C"], "group": ["g", "g", "h"],
                        "ai_language": ["te", "ta", "hi"], "current_label": ["te", "ta", "hi"], "ai_ambiguous": ["0", "1", "0"],
                        "ai_dubbed": ["0", "0", "0"], "ai_confidence": "high", "ai_notes": "n"})
    sheet = pd.DataFrame({"tconst": ["t1", "t2", "t3"], "primaryTitle": ["A", "B", "C"], "year": "2000", "imdb_url": "u",
                          "my_language": answers[0], "my_bilingual": answers[1], "my_dubbed": answers[2], "sources_checked": "", "my_notes": ""})
    sheet.to_csv(tmp_path / "s.csv", index=False)
    key.to_csv(tmp_path / "k.csv", index=False)
    return tmp_path / "s.csv", tmp_path / "k.csv"


def test_scoring_matches_and_disagreements(tmp_path, capsys):
    s, k = _files(tmp_path, (["Telugu", "te", "hi"], ["no", "no", "no"], ["no", "no", "no"]))
    r = sc.score(sc.load(s, k))
    assert (r["language"]["match"], r["language"]["answered"]) == (2, 3)               # names are normalised; B: te vs ta
    assert (r["bilingual vs ambiguous"]["match"], r["bilingual vs ambiguous"]["answered"]) == (2, 3)
    assert r["language and bilingual both"]["match"] == 2
    assert sc.main(["--sheet", str(s), "--key", str(k)]) == 0
    out = capsys.readouterr().out
    assert "language: me='te' AI='ta'" in out and "bilingual: me=no AI ambiguous=yes" in out


def test_unanswered_rows_are_excluded_not_counted_wrong(tmp_path):
    s, k = _files(tmp_path, (["te", "", ""], ["no", "", ""], ["", "", ""]))
    r = sc.score(sc.load(s, k))
    assert r["language"]["answered"] == 1 and r["language"]["match"] == 1


def test_wilson_interval():
    p, lo, hi = sc.wilson(29, 29)
    assert p == 1 and 0.85 < lo < 0.9 and hi == 1.0
