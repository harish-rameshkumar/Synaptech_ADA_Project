from __future__ import annotations

from itertools import combinations
from pathlib import Path
import json
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score, brier_score_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

CREDIT_REQUIRED = {"actor_id", "title_id", "year", "language", "billing_order"}


def load_credits(path: str | Path) -> pd.DataFrame:
    """Read a cleaned CSV/Parquet credit table; normalize and validate columns."""
    path = Path(path)
    df = pd.read_parquet(path) if path.suffix.lower() in {".parquet", ".pq"} else pd.read_csv(path)
    missing = CREDIT_REQUIRED - set(df.columns)
    if missing:
        raise ValueError(f"Credits missing required columns: {sorted(missing)}")
    df = df.copy()
    df["year"] = pd.to_numeric(df["year"], errors="coerce")
    df["billing_order"] = pd.to_numeric(df["billing_order"], errors="coerce")
    df = df.dropna(subset=["actor_id", "title_id", "year", "language", "billing_order"])
    df["year"] = df["year"].astype(int)
    df["billing_order"] = df["billing_order"].astype(int)
    df["actor_id"] = df["actor_id"].astype(str)
    df["title_id"] = df["title_id"].astype(str)
    df["language"] = df["language"].astype(str).str.lower().str.strip()
    def as_bool(series):
        return series.fillna(False).map(lambda x: x if isinstance(x, (bool, np.bool_)) else str(x).strip().lower() in {"1", "true", "yes", "y"})
    if "is_dubbed" in df:
        df = df[~as_bool(df["is_dubbed"])]
    if "is_ambiguous" in df:
        df = df[~as_bool(df["is_ambiguous"])]
    # Only principal cast; default top 10 billed actors on each title.
    df = df[df["billing_order"].between(1, 10)]
    return df.drop_duplicates(["actor_id", "title_id"]).sort_values(["year", "title_id", "billing_order"])


def select_pairs(credits: pd.DataFrame, min_events: int = 10, min_at_risk: int = 100) -> pd.DataFrame:
    """Pre-model pair selection based on clean first-entry events and eligible risk-years."""
    transitions = _eligible_transitions(credits)
    rows = []
    for (s, d), g in transitions.groupby(["source", "destination"]):
        rows.append({"source": s, "destination": d,
                     "events": int(g["event_year"].notna().sum()),
                     "eligible_actors": int(g["actor_id"].nunique()),
                     "at_risk_actor_years": int(sum((int(r.last_observed_year) if pd.isna(r.event_year) else int(r.event_year)) - int(r.qualifying_year) for r in g.itertuples()))})
    if not rows:
        return pd.DataFrame(columns=["source", "destination", "events", "eligible_actors", "selected"])
    out = pd.DataFrame(rows)
    out["selected"] = (out.events >= min_events) & (out.at_risk_actor_years >= min_at_risk)
    return out.sort_values(["selected", "events"], ascending=[False, False])


def _eligible_transitions(credits: pd.DataFrame) -> pd.DataFrame:
    """Find first year an actor qualifies in S and first clean D credit thereafter."""
    rows = []
    observation_end = int(credits.year.max())
    language_universe = sorted(credits.language.unique())
    for actor, g in credits.groupby("actor_id", sort=False):
        g = g.sort_values(["year", "title_id"])
        actor_languages = sorted(g.language.unique())
        for s in actor_languages:
            source = g[g.language.eq(s)]
            # First calendar year at which lifetime S credits meet both thresholds.
            qualifying_year = None
            for year in sorted(g.year.unique()):
                prior = g[g.year.le(year)]
                source_prior = prior[prior.language.eq(s)]
                distinct_years = int(source_prior.year.nunique())
                share = len(source_prior) / max(1, len(prior))
                if len(source_prior) >= 5 and distinct_years >= 3 and share >= .70:
                    qualifying_year = int(year)
                    break
            if qualifying_year is None:
                continue
            prior_dests = set(g.loc[g.year.le(qualifying_year), "language"])
            # Include destinations the actor has never entered: they provide the
            # non-event actor-years needed for a valid risk set.
            for d in language_universe:
                if d == s or d in prior_dests:
                    continue
                target = g[(g.language.eq(d)) & (g.year.gt(qualifying_year))]
                event_year = int(target.year.min()) if len(target) else np.nan
                # Actors with an event before source qualification are ineligible.
                rows.append({"actor_id": actor, "source": s, "destination": d,
                             "qualifying_year": qualifying_year, "event_year": event_year,
                             "first_observed_year": int(g.year.min()),
                             "last_observed_year": observation_end})
    cols = ["actor_id", "source", "destination", "qualifying_year", "event_year",
            "first_observed_year", "last_observed_year"]
    return pd.DataFrame(rows, columns=cols).drop_duplicates(["actor_id", "source", "destination"])


def build_actor_year(credits: pd.DataFrame, source: str | None = None,
                     destination: str | None = None, min_exposure_lag: int = 1) -> pd.DataFrame:
    """Build actor-pair-year risk set and lagged collaborator exposure features."""
    all_transitions = _eligible_transitions(credits)
    transitions = all_transitions
    if source:
        transitions = transitions[transitions.source.eq(source.lower())]
    if destination:
        transitions = transitions[transitions.destination.eq(destination.lower())]
    if transitions.empty:
        raise ValueError("No eligible actor-language-pair risk sets; inspect credits or relax pre-model data-quality thresholds.")
    # Historical collaborator ties, indexed by target actor and collaborator, with repeated-credit strength.
    title_cast = credits.groupby("title_id").agg(year=("year", "min"), actors=("actor_id", lambda x: sorted(set(x)))).reset_index()
    edge_rows = []
    for row in title_cast.itertuples(index=False):
        for a, b in combinations(row.actors, 2):
            edge_rows.extend([(a, b, int(row.year)), (b, a, int(row.year))])
    edges = pd.DataFrame(edge_rows, columns=["actor_id", "collaborator_id", "tie_year"])
    ties = edges.groupby(["actor_id", "collaborator_id"]).tie_year.apply(list).to_dict()
    # First D credit supports the separate older-access control. Recent exposure
    # requires the collaborator's own qualifying S->D transition.
    dest_year = credits.groupby(["actor_id", "language"]).year.min().to_dict()
    qualified_move = {(r.actor_id, r.source, r.destination): r.event_year
                      for r in all_transitions.itertuples(index=False) if pd.notna(r.event_year)}
    actor_history = credits.groupby(["actor_id", "year"]).agg(
        annual_credits=("title_id", "nunique"), annual_languages=("language", "nunique")).reset_index()
    history = {(a, int(y)): r for (a, y), r in credits.groupby(["actor_id", "year"])}
    all_years = sorted(credits.year.unique())
    data = []
    for tr in transitions.itertuples(index=False):
        start = max(int(tr.qualifying_year) + 1, int(tr.first_observed_year))
        end = int(tr.event_year) if pd.notna(tr.event_year) else int(tr.last_observed_year)
        for year in all_years:
            year = int(year)
            if not start <= year <= end:
                continue
            # The actor's own event year is included as the positive outcome row.
            recent, old, weighted, repeat = 0, 0, 0.0, 0.0
            for (a, lang), first_d_year in dest_year.items():
                if lang != tr.destination or a == tr.actor_id:
                    continue
                tie_years = ties.get((tr.actor_id, a), [])
                # Existing destination access: an earlier D-experienced collaborator.
                if first_d_year < year and any(ty < int(first_d_year) for ty in tie_years):
                    old += 1
                move_year = qualified_move.get((a, tr.source, tr.destination))
                if move_year is None or move_year >= year:
                    continue
                prior_ties = [ty for ty in tie_years if ty < int(move_year)]
                if not prior_ties:
                    continue  # collaborator must predate their qualified S->D move
                lag = year - int(move_year)
                if min_exposure_lag <= lag <= 3:
                    recent += 1
                    repeat += min(len(prior_ties), 5)
                    weighted += (1 / lag) * min(len(prior_ties), 5)
            own = history.get((tr.actor_id, year - 1))
            event = int(pd.notna(tr.event_year) and year == int(tr.event_year))
            past = credits[(credits.actor_id.eq(tr.actor_id)) & (credits.year.lt(year))]
            s_count = int(past.language.eq(tr.source).sum())
            other_moves = int(past.loc[~past.language.isin([tr.source, tr.destination]), "language"].nunique())
            data.append({"actor_id": tr.actor_id, "source": tr.source, "destination": tr.destination,
                         "year": year, "event": event,
                         "career_age": year - int(tr.first_observed_year), "source_credits": s_count,
                         "other_languages": other_moves, "recent_exposed_collaborators": recent,
                         "recent_tie_strength": repeat, "recent_exposure_weighted": weighted,
                         "older_destination_access": old,
                         "annual_source_credits": int(own.language.eq(tr.source).sum()) if own is not None else 0})
    out = pd.DataFrame(data)
    return out.sort_values(["year", "actor_id", "source", "destination"]).reset_index(drop=True)


def _make_model(numeric, categorical):
    prep = ColumnTransformer([
        ("num", Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), numeric),
        ("cat", OneHotEncoder(handle_unknown="ignore"), categorical),
    ])
    return Pipeline([("prep", prep), ("model", LogisticRegression(max_iter=2000, C=.5))])


def evaluate(actor_year: pd.DataFrame, split_year: int | None = None) -> tuple[pd.DataFrame, dict]:
    """Chronological test comparison: career-only vs career + network exposures."""
    if split_year is None:
        split_year = int(actor_year.year.quantile(.8))
    train, test = actor_year[actor_year.year < split_year], actor_year[actor_year.year >= split_year]
    if train.event.nunique() < 2 or test.event.nunique() < 2:
        raise ValueError(f"Split year {split_year} leaves one outcome class in train or test; choose a later/earlier split based on event coverage.")
    career = ["career_age", "source_credits", "other_languages", "annual_source_credits", "older_destination_access"]
    network = career + ["recent_exposed_collaborators", "recent_tie_strength", "recent_exposure_weighted"]
    cat = ["source", "destination"]
    results = []
    for label, features in [("career_only", career), ("career_plus_network", network)]:
        model = _make_model(features, cat)
        model.fit(train[features + cat], train.event)
        pred = model.predict_proba(test[features + cat])[:, 1]
        results.append({"model": label, "split_year": split_year,
                        "train_rows": len(train), "test_rows": len(test),
                        "test_events": int(test.event.sum()),
                        "AUPRC": average_precision_score(test.event, pred),
                        "ROC_AUC": roc_auc_score(test.event, pred),
                        "Brier": brier_score_loss(test.event, pred)})
    return pd.DataFrame(results), {"train_start": int(train.year.min()), "train_end": int(train.year.max()),
                                    "test_start": int(test.year.min()), "test_end": int(test.year.max())}


def run_placebo(actor_year: pd.DataFrame, split_year: int | None = None) -> pd.DataFrame:
    """Lead exposure placebo: future exposure should not improve the actor's current event prediction."""
    df = actor_year.sort_values(["actor_id", "source", "destination", "year"]).copy()
    for c in ["recent_exposed_collaborators", "recent_tie_strength", "recent_exposure_weighted"]:
        df["lead_" + c] = df.groupby(["actor_id", "source", "destination"])[c].shift(-1).fillna(0)
    if split_year is None:
        split_year = int(df.year.quantile(.8))
    train, test = df[df.year < split_year], df[df.year >= split_year]
    num = ["career_age", "source_credits", "other_languages", "annual_source_credits", "older_destination_access"]
    for label, features in [("network", num + ["recent_exposed_collaborators", "recent_tie_strength", "recent_exposure_weighted"]),
                            ("lead_placebo", num + ["lead_recent_exposed_collaborators", "lead_recent_tie_strength", "lead_recent_exposure_weighted"])]:
        model = _make_model(features, ["source", "destination"])
        model.fit(train[features + ["source", "destination"]], train.event)
        pred = model.predict_proba(test[features + ["source", "destination"]])[:, 1]
        yield_result = {"test": label, "AUPRC": average_precision_score(test.event, pred),
                        "test_events": int(test.event.sum()), "split_year": split_year}
        if label == "network": results = [yield_result]
        else: results.append(yield_result)
    return pd.DataFrame(results)


def write_json(data: dict, path: str | Path) -> None:
    Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")
