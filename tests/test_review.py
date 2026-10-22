import numpy as np
import pandas as pd
import pytest
from cinema_mobility import review


def _shortlist(tmp_path, n=100):
    df = pd.DataFrame({"rank": range(1, n + 1), "tconst": [f"tt{i:07d}" for i in range(1, n + 1)],
                       "primaryTitle": "x", "year": 2000, "language": "te", "current_label_source": "T1", "akas_langs": "hi",
                       "reason": "r", "n_actors": 10, "n_ties": 45, "n_ties_lost_if_dropped": 45,
                       "is_dubbed": "", "is_ambiguous": ""})
    p = tmp_path / "review.csv"
    df.to_csv(p, index=False)
    return p


def test_packet_split_overlap_control(tmp_path):
    out = tmp_path / "packet"
    info = review.make_packet(_shortlist(tmp_path), out, ("A", "B", "C"), top=30, overlap=6, control=8)
    files = {n: pd.read_csv(out / f"review_{n}.csv", dtype=str) for n in "ABC"}
    ctl = pd.read_csv(out / "review_control.csv", dtype=str)
    key = pd.read_csv(out / "packet_key.csv")
    assert all(len(f) == 16 for f in files.values()) and len(ctl) == 8 and info["unreviewed"] == 100 - 30 - 6 - 8
    uniq = {n: set(key[key.set == f"unique:{n}"].tconst) for n in "ABC"}
    assert all(len(u) == 10 for u in uniq.values()) and not (uniq["A"] & uniq["B"]) and not (uniq["A"] & uniq["C"])
    assert set().union(*uniq.values()) == {f"tt{i:07d}" for i in range(1, 31)}  # exactly the top block
    overlap = set(key[key.set == "overlap"].tconst)
    assert all(overlap <= set(f.tconst) for f in files.values())
    assert not (overlap & set(ctl.tconst)) and not (set(ctl.tconst) & set().union(*uniq.values()))
    assert "overlap" not in "".join(files["A"].columns) and files["A"].is_dubbed.isna().all()  # blind, blank
    with pytest.raises(FileExistsError):
        review.make_packet(_shortlist(tmp_path), out, ("A", "B", "C"), top=30, overlap=6, control=8)


def test_fleiss_kappa_hand_computed():
    m = np.array([[1, 1, 1], [0, 0, 0], [1, 1, 0], [0, 0, 1]])
    assert review.fleiss_kappa(m) == pytest.approx(1 / 3)
    assert review.fleiss_kappa(np.array([[1, 1, 1], [0, 0, 0], [1, 1, 1], [0, 0, 0]])) == pytest.approx(1.0)
    assert review.cohen_kappa(np.array([1, 1, 0, 0]), np.array([1, 0, 0, 0])) == pytest.approx(0.5)


def test_consensus_majority_conflict_and_blank():
    v = lambda t, r, d, a, c=pd.NA: {"tconst": t, "reviewer": r, "is_dubbed": d, "is_ambiguous": a, "corrected_language": c}
    votes = pd.DataFrame([v("t1", "A", True, False), v("t1", "B", True, False), v("t1", "C", False, False),
                          v("t2", "A", True, False), v("t2", "B", False, False),   # 1-1 split
                          v("t3", "A", pd.NA, pd.NA),                              # skipped
                          v("t4", "A", False, False, "ml")]).astype({"is_dubbed": "boolean", "is_ambiguous": "boolean"})
    d = review.consensus(votes).set_index("tconst")
    assert d.loc["t1", "is_dubbed"] and d.loc["t1", "exclude"] and not d.loc["t1", "conflict"]
    assert d.loc["t2", "conflict"] and d.loc["t2", "exclude"]
    assert not d.loc["t3", "reviewed"] and not d.loc["t3", "exclude"]
    assert d.loc["t4", "corrected_language"] == "ml" and not d.loc["t4", "exclude"]


def test_read_review_rejects_bad_values_and_never_writes(tmp_path):
    p = tmp_path / "r.csv"
    p.write_text("tconst,is_dubbed,is_ambiguous,reviewer\ntt1,maybe,0,A\n")
    before = review.sha256(p)
    with pytest.raises(ValueError, match="not 1/0"):
        review.read_review(p)
    assert review.sha256(p) == before


def test_apply_review_ties_vs_drop_and_changes():
    titles = pd.DataFrame({"title_id": ["a", "b", "c"], "titleType": "movie", "year": 2000})
    cands = pd.DataFrame({"title_id": ["a", "b", "c"]})
    labels = pd.DataFrame({"title_id": ["a", "b", "c"], "language": ["te", "hi", "ta"], "label_source": ["T1", "T1", "T2"]})
    raw = pd.DataFrame({"title_id": ["a", "a", "b", "c"], "actor_id": ["x", "y", "x", "z"], "billing_order": 1})
    dec = pd.DataFrame({"tconst": ["a", "b", "c"], "reviewed": True, "is_dubbed": [True, False, False],
                        "is_ambiguous": False, "conflict": False, "corrected_language": [pd.NA, "ml", pd.NA],
                        "exclude": [True, False, False]})
    base, *_ = review.apply_review(pd.DataFrame(columns=dec.columns), titles, cands, labels, raw)
    new, *_ = review.apply_review(dec, titles, cands, labels, raw)
    o = new.drop_duplicates("title_id").set_index("title_id")
    assert pd.isna(o.loc["a", "language"]) and o.loc["a", "label_source"] == "review_excluded"  # dubbed -> ties only
    assert o.loc["b", "language"] == "ml" and o.loc["b", "label_source"] == "reviewed"          # corrected
    assert o.loc["c", "language"] == "ta"
    ch = review.credit_changes(base, new)
    assert ch["titles_changed"] == 2 and ch["credits_changed"] == 3 and ch["credits_removed"] == 0
    dropped, *_ = review.apply_review(dec, titles, cands, labels, raw, excluded_as="drop")
    assert "a" not in set(dropped.title_id) and review.credit_changes(base, dropped)["credits_removed"] == 2
