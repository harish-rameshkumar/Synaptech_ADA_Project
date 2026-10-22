import pandas as pd
from cinema_mobility.pipeline import load_credits, build_actor_year, _eligible_transitions


def test_never_destination_actors_remain_in_risk_set(tmp_path):
    rows = []
    for i in range(8):
        for j, year in enumerate([2000, 2001, 2002, 2003, 2004]):
            rows.append({"actor_id": f"a{i}", "title_id": f"a{i}-{j}", "year": year,
                         "language": "ta", "billing_order": 1})
    for j, year in enumerate([2000, 2001, 2002, 2003, 2004]):
        rows.append({"actor_id": "b", "title_id": f"b-{j}", "year": year,
                     "language": "ta", "billing_order": 1})
    rows.append({"actor_id": "b", "title_id": "b-hi", "year": 2006,
                 "language": "hi", "billing_order": 1})
    path = tmp_path / "credits.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    panel = build_actor_year(load_credits(path), "ta", "hi")
    assert set(panel.actor_id) == {f"a{i}" for i in range(8)} | {"b"}
    assert panel.event.sum() == 1
    assert (panel.event == 0).any()


def test_string_false_flags_are_not_dropped(tmp_path):
    path = tmp_path / "credits.csv"
    pd.DataFrame([{"actor_id":"a", "title_id":"t", "year":2000, "language":"ta",
                   "billing_order":1, "is_dubbed":"False", "is_ambiguous":"False"}]).to_csv(path,index=False)
    assert len(load_credits(path)) == 1


def _credits(actor_years, extra=()):
    rows = [{"actor_id": "x", "title_id": f"x-{i}", "year": y, "language": "ta", "billing_order": 1}
            for i, y in enumerate(actor_years)]
    rows += [{"actor_id": "other", "title_id": "o-hi", "year": 2010, "language": "hi", "billing_order": 1}]
    rows += list(extra)
    return pd.DataFrame(rows)


def test_eligibility_needs_three_distinct_years_not_a_span():
    # Five credits spanning 2000-2003 but only two distinct calendar years.
    tr = _eligible_transitions(_credits([2000, 2000, 2000, 2003, 2003]))
    assert "x" not in set(tr.actor_id)


def test_eligibility_accepts_non_consecutive_years():
    tr = _eligible_transitions(_credits([2000, 2000, 2005, 2005, 2010]))
    row = tr[(tr.actor_id == "x") & (tr.destination == "hi")].iloc[0]
    assert row.qualifying_year == 2010


def test_eligibility_needs_five_credits():
    tr = _eligible_transitions(_credits([2000, 2001, 2002, 2003]))
    assert "x" not in set(tr.actor_id)


def test_unlabeled_credits_are_ties_only_not_eligibility(tmp_path):
    rows = [{"actor_id": "x", "title_id": f"x-{i}", "year": y, "language": "ta", "billing_order": 1}
            for i, y in enumerate([2000, 2001, 2002, 2003, 2004])]
    rows += [{"actor_id": "x", "title_id": f"u-{i}", "year": 2003 + i % 2, "language": None, "billing_order": 2}
             for i in range(5)]
    rows += [{"actor_id": "o", "title_id": "o-hi", "year": 2010, "language": "hi", "billing_order": 1}]
    path = tmp_path / "credits.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    credits = load_credits(path)
    assert credits.language.isna().sum() == 5  # kept by load_credits
    tr = _eligible_transitions(credits)
    # 5 labeled ta credits / 10 total would fail the 70% share if unlabeled credits counted.
    assert "x" in set(tr.actor_id)
    build_actor_year(credits, "ta", "hi")  # no crash with unlabeled rows


def test_focal_languages_limit_sources_and_destinations_but_other_languages_count_in_share():
    rows = [{"actor_id": "x", "title_id": f"x-{i}", "year": y, "language": "ta", "billing_order": 1}
            for i, y in enumerate([2000, 2001, 2002, 2003, 2004])]
    rows += [{"actor_id": "x", "title_id": "x-en", "year": 2004, "language": "en", "billing_order": 1},
             {"actor_id": "x", "title_id": "x-bn", "year": 2008, "language": "bn", "billing_order": 1},
             {"actor_id": "o", "title_id": "o-hi", "year": 2010, "language": "hi", "billing_order": 1}]
    tr = _eligible_transitions(pd.DataFrame(rows), ("ta", "hi"))
    assert set(tr.destination) == {"hi"} and set(tr.source) == {"ta"}  # bn/en never sources or destinations
    assert tr.event_year.isna().all()
    # 5 ta of 6 prior credits (the English one counts in the denominator) = 83%: still eligible...
    assert "x" in set(tr.actor_id)
    rows += [{"actor_id": "x", "title_id": f"x-en{i}", "year": 2004, "language": "en", "billing_order": 1} for i in range(3)]
    # ...but 5 ta of 9 prior credits is below 70%.
    assert "x" not in set(_eligible_transitions(pd.DataFrame(rows), ("ta", "hi")).actor_id)
