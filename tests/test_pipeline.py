import pandas as pd
from cinema_mobility.pipeline import load_credits, build_actor_year


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
