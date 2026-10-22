import pandas as pd
from cinema_mobility import resolver


def _fake(monkeypatch, tmdb, wd):
    monkeypatch.setattr(resolver, "tmdb_lookup", lambda ids, **kw: pd.DataFrame(
        [{"title_id": i, "tmdb_id": None, "tmdb_media": "movie", "tmdb_language": tmdb.get(i), "tmdb_status": "matched"} for i in ids]))
    monkeypatch.setattr(resolver, "wikidata_lookup", lambda ids, **kw: pd.DataFrame(
        [{"title_id": i, "wd_qid": None, "wd_language": wd.get(i), "wd_status": "matched"} for i in ids]))


def test_tiers_are_tmdb_then_wikidata_then_unknown(monkeypatch):
    _fake(monkeypatch, {"t1": "ta", "t2": None, "t3": None}, {"t1": "hi", "t2": "ml", "t3": None})
    out = resolver.resolve_labels(["t1", "t2", "t3"]).set_index("title_id")
    assert out.loc["t1", ["language", "label_source"]].tolist() == ["ta", "T1"]  # TMDB wins over Wikidata
    assert out.loc["t2", ["language", "label_source"]].tolist() == ["ml", "T2"]
    assert out.loc["t3", "label_source"] == "unknown" and out.loc["t3", "language"] is None


def test_akas_is_not_a_labelling_tier():
    assert not hasattr(resolver, "akas_original_language")
