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
    assert st.to_dict() == {"a": "kept_labeled", "b": "kept_other_language", "c": "kept_unknown",
                            "d": "dropped_not_indian_candidate", "e": "dropped_after_end_year"}
    raw = pd.DataFrame({"title_id": ["a", "c", "b"], "actor_id": ["x", "y", "z"], "billing_order": 1})
    out = build_credits(raw, title_status(titles, cands, labels, end_year=2020))
    assert set(out.title_id) == {"a", "b", "c"}  # unknown and other-language titles kept, non-candidate dropped
    o = out.set_index("title_id")
    assert o.loc["c", "label_source"] == "unknown" and out.language.isna().sum() == 1
    assert o.loc["b", "language"] == "en"  # other-language label preserved


def test_unknown_titles_connect_actors():
    credits = pd.DataFrame({"title_id": ["a", "a", "u", "u"], "actor_id": ["x", "y", "y", "z"],
                            "language": ["ta", "ta", None, None]})
    assert connectivity(credits)["n_components"] == 1
    assert connectivity(credits[credits.language.notna()])["n_actors"] == 2
    assert len(pair_table(credits)) == 2


def test_sensitivity_unknown_in_denominator_can_only_remove_eligibility():
    from cinema_mobility.quality import eligibility_sensitivity
    rows = []
    for actor, n_unknown in [("keep", 0), ("lose", 6), ("late", 2)]:
        for i, y in enumerate([2000, 2001, 2002, 2003, 2004]):
            rows.append({"actor_id": actor, "title_id": f"{actor}-{i}", "year": y, "language": "ta", "billing_order": 1})
        for j in range(n_unknown):
            rows.append({"actor_id": actor, "title_id": f"{actor}-u{j}", "year": 2000, "language": None, "billing_order": 2})
    rows.append({"actor_id": "late", "title_id": "late-6", "year": 2005, "language": "ta", "billing_order": 1})
    z = eligibility_sensitivity(pd.DataFrame(rows), ("ta", "hi"))
    assert z["eligible_excluded"] == 3 and z["pairs_gained_when_counted"] == 0
    assert z["pairs_lost_when_counted"] == 1  # 5 ta / 11 prior = 45% < 70%
    assert z["actors_lost"] == 1
