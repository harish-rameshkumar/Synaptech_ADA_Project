import pandas as pd
from cinema_mobility.ingest import title_status, build_credits
from cinema_mobility.quality import connectivity, pair_table


def _inputs():
    titles = pd.DataFrame({"title_id": ["a", "b", "c", "d", "e"], "titleType": "movie", "year": [1990, 1991, 1992, 1993, 2030]})
    cands = pd.DataFrame({"title_id": ["a", "b", "c", "e"]})
    labels = pd.DataFrame({"title_id": ["a", "b", "c", "e"], "language": ["ta", "en", None, "hi"],
                           "label_source": ["T1", "T1", "unknown", "T2"]})
    return titles, cands, labels


def test_title_status_and_left_join():
    titles, cands, labels = _inputs()
    st = title_status(titles, cands, labels, end_year=2020).set_index("title_id").status
    assert st.to_dict() == {"a": "kept_labeled", "b": "dropped_other_language", "c": "kept_unknown",
                            "d": "dropped_not_indian_candidate", "e": "dropped_after_end_year"}
    raw = pd.DataFrame({"title_id": ["a", "c", "b"], "actor_id": ["x", "y", "z"], "billing_order": 1})
    out = build_credits(raw, title_status(titles, cands, labels, end_year=2020))
    assert set(out.title_id) == {"a", "c"}  # unknown title kept, other-language dropped
    assert out.set_index("title_id").loc["c", "label_source"] == "unknown" and out.language.isna().sum() == 1


def test_unknown_titles_connect_actors():
    credits = pd.DataFrame({"title_id": ["a", "a", "u", "u"], "actor_id": ["x", "y", "y", "z"],
                            "language": ["ta", "ta", None, None]})
    assert connectivity(credits)["n_components"] == 1
    assert connectivity(credits[credits.language.notna()])["n_actors"] == 2
    assert len(pair_table(credits)) == 2
