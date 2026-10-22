import json
import sqlite3
import pandas as pd
import pytest
from cinema_mobility import rules


def _cache(tmp_path, tmdb, wd):
    db = str(tmp_path / "c.sqlite")
    con = sqlite3.connect(db)
    rules._tables(con)
    for t, (orig, spoken) in tmdb.items():
        con.execute("INSERT INTO tmdb_detail VALUES (?,?,?,?,?,?)", (t, "1", "movie", orig, json.dumps(spoken), "matched"))
    for t, langs in wd.items():
        con.execute("INSERT INTO wikidata_all VALUES (?,?,?,?)", (t, "[]", json.dumps(langs), "matched" if langs else "not_found"))
    con.commit()
    con.close()
    return db


def _inputs(ids):
    status = pd.DataFrame({"title_id": ids, "status": "kept_labeled", "language": "te", "label_source": "T1"})
    labels = pd.DataFrame({"title_id": ids, "tmdb_id": "1", "tmdb_media": "movie", "tmdb_language": "te"})
    return status, labels


def test_parse_rules():
    assert rules.parse_rules("all") == rules.RULES and rules.parse_rules("none") == ()
    assert rules.parse_rules("c,a") == ("spoken", "disagree")
    assert rules.parse_rules("a,b-focal,c") == rules.PRIMARY == rules.parse_rules("spoken,wikidata_focal,disagree")
    assert rules.parse_rules("bf") == ("wikidata_focal",)
    with pytest.raises(ValueError):
        rules.parse_rules("spoken,nope")


def test_each_rule_fires_on_its_own_condition(tmp_path):
    ids = ["clean", "spoken", "multi_wd", "disagree", "kn_only_one", "no_wd"]
    db = _cache(tmp_path,
                {"clean": ("te", ["te"]), "spoken": ("te", ["te", "hi", "en"]), "multi_wd": ("te", ["te"]),
                 "disagree": ("te", ["te"]), "kn_only_one": ("te", ["te", "en", "ur"]), "no_wd": ("te", ["te"])},
                {"clean": ["te"], "spoken": ["te"], "multi_wd": ["te", "ta"], "disagree": ["ta"], "kn_only_one": ["te"], "no_wd": []})
    t = rules.rules_table(*_inputs(ids), cache_db=db).set_index("title_id")
    assert t.rule_spoken.to_dict() == {"clean": False, "spoken": True, "multi_wd": False, "disagree": False, "kn_only_one": False, "no_wd": False}
    assert t.rule_wikidata.to_dict() == {"clean": False, "spoken": False, "multi_wd": True, "disagree": False, "kn_only_one": False, "no_wd": False}
    assert t.rule_wikidata_focal.to_dict() == {"clean": False, "spoken": False, "multi_wd": True, "disagree": False, "kn_only_one": False, "no_wd": False}
    # multi_wd contains te so it is not a disagreement; no Wikidata data means no disagreement
    assert t.rule_disagree.to_dict() == {"clean": False, "spoken": False, "multi_wd": False, "disagree": True, "kn_only_one": False, "no_wd": False}


def test_incomplete_cache_is_an_error(tmp_path):
    db = _cache(tmp_path, {}, {})
    with pytest.raises(RuntimeError, match="incomplete"):
        rules.rules_table(*_inputs(["x"]), cache_db=db)


def test_flagged_titles_stay_as_ties_with_no_language(tmp_path):
    db = _cache(tmp_path, {"a": ("te", ["te", "hi"]), "b": ("te", ["te"])}, {"a": ["te"], "b": ["te"]})
    tbl = rules.rules_table(*_inputs(["a", "b"]), cache_db=db)
    credits = pd.DataFrame({"title_id": ["a", "a", "b"], "actor_id": ["x", "y", "x"], "language": "te", "label_source": "T1", "year": 2000})
    out, flagged = rules.apply_rule_flags(credits, tbl, ("spoken",))
    assert flagged == {"a"} and len(out) == 3                         # credits kept, so ties survive
    assert out.loc[out.title_id == "a", "language"].isna().all()
    assert (out.loc[out.title_id == "a", "label_source"] == "rule_ambiguous").all()
    assert out.loc[out.title_id == "b", "language"].eq("te").all()
    same, none = rules.apply_rule_flags(credits, tbl, ())             # switch off = unchanged
    assert none == set() and same.equals(credits)
    assert credits.language.eq("te").all()                            # input not mutated


def test_control_evaluation_precision_recall():
    tbl = pd.DataFrame({"title_id": ["a", "b", "c", "d"], "language": "te", "rule_spoken": [True, True, False, False],
                        "rule_wikidata": False, "rule_wikidata_focal": False, "rule_disagree": [False, False, True, False]})
    ctl = pd.DataFrame({"tconst": ["a", "b", "c", "d"], "primaryTitle": "x", "language": "te", "is_ambiguous": ["1", "0", "1", "0"]})
    ev, _ = rules.control_evaluation(tbl, ctl)
    e = ev.set_index("rule")
    assert e.loc["spoken", "precision"] == 0.5 and e.loc["spoken", "recall"] == 0.5
    assert e.loc["disagree", "precision"] == 1.0 and e.loc["any", "recall"] == 1.0 and e.loc["any", "false_positive"] == 1


def test_reviewed_relabels_are_outside_the_rules(tmp_path):
    db = _cache(tmp_path, {"a": ("te", ["te"])}, {"a": ["te"]})
    status, labels = _inputs(["a", "r"])
    status.loc[status.title_id == "r", "label_source"] = "reviewed"
    assert list(rules.rules_table(status, labels, cache_db=db).title_id) == ["a"]


def test_wikidata_focal_ignores_non_focal_second_languages(tmp_path):
    ids = ["en_second", "two_focal", "three_noise"]
    db = _cache(tmp_path, {i: ("te", ["te"]) for i in ids},
                {"en_second": ["te", "en"], "two_focal": ["te", "ta"], "three_noise": ["te", "en", "ur"]})
    t = rules.rules_table(*_inputs(ids), cache_db=db).set_index("title_id")
    assert t.rule_wikidata.all()                                   # plain rule: any two languages
    assert t.rule_wikidata_focal.to_dict() == {"en_second": False, "two_focal": True, "three_noise": False}
