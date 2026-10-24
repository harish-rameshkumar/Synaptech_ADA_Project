from __future__ import annotations

from bisect import bisect_left
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
try:
    import statsmodels.api as sm
    import statsmodels.formula.api as smf
except ImportError:  # metric/panel code remains usable before optional inference deps install
    sm = smf = None
from scipy.stats import chi2

CREDIT_REQUIRED = {"actor_id", "title_id", "year", "language", "billing_order"}
TRUE = {"1", "true", "yes", "y"}
FOCAL_LANGUAGES = {"hi", "ta", "te", "ml", "kn"}


def _bool(s: pd.Series) -> pd.Series:
    return s.fillna(False).map(lambda x: bool(x) if isinstance(x, (bool, np.bool_)) else str(x).strip().lower() in TRUE)


def load_credits(path: str | Path, strict: bool = False) -> pd.DataFrame:
    """Load all credits. Unknown/ambiguous/dubbed titles remain for graph ties.

    `strict` is applied when building language events, not here, so strict mode
    never removes collaboration edges.
    """
    if isinstance(path, pd.DataFrame):
        df = path.copy()
    else:
        path = Path(path)
        df = pd.read_parquet(path) if path.suffix.lower() in {".parquet", ".pq"} else pd.read_csv(path)
    missing = CREDIT_REQUIRED - set(df.columns)
    if missing:
        raise ValueError(f"Credits missing required columns: {sorted(missing)}")
    df = df.copy()
    df["year"] = pd.to_numeric(df["year"], errors="coerce")
    df["billing_order"] = pd.to_numeric(df["billing_order"], errors="coerce")
    # A valid actor/title/year row stays in the collaboration graph even if its
    # language is unresolved or excluded from the primary event definition.
    df = df.dropna(subset=["actor_id", "title_id", "year", "billing_order"])
    df["year"] = df["year"].astype(int)
    df["billing_order"] = df["billing_order"].astype(int)
    df["actor_id"] = df["actor_id"].astype(str)
    df["title_id"] = df["title_id"].astype(str)
    df["language"] = df["language"].fillna("unknown").astype(str).str.lower().str.strip()
    df.loc[df.language.isin(["", "nan", "none", "\\n"]), "language"] = "unknown"
    if "label_source" not in df:
        df["label_source"] = np.where(df.language.ne("unknown"), "manual", "unknown")
    df["label_source"] = df["label_source"].fillna("unknown").astype(str).str.upper()
    for c in ["is_dubbed", "is_ambiguous"]:
        if c not in df:
            df[c] = False
        df[c] = _bool(df[c])
    df = df[df["billing_order"].between(1, 10)]
    return df.drop_duplicates(["actor_id", "title_id"]).sort_values(["year", "title_id", "billing_order"])


def _event_credits(credits: pd.DataFrame, strict: bool = False) -> pd.DataFrame:
    # A dubbed release is not automatically discarded: the project label is
    # the title's original language. Exclude only unresolved/ambiguous labels.
    known = credits.language.ne("unknown") & ~credits.is_ambiguous
    if strict:
        known &= credits.label_source.eq("T1")
    return credits[known].copy()


def language_audit(credits: pd.DataFrame) -> pd.DataFrame:
    """Title-level match counts/rates by resolver tier, language and release year."""
    cols = ["title_id", "year", "language", "label_source"]
    titles = credits[cols].drop_duplicates("title_id").copy()
    titles["label_source"] = titles.label_source.replace({"": "UNKNOWN"}).fillna("UNKNOWN")
    titles.loc[titles.language.eq("unknown"), "label_source"] = "UNKNOWN"
    out = titles.groupby(["year", "language", "label_source"], dropna=False).size().rename("titles").reset_index()
    total = titles.groupby(["year", "language"], dropna=False).size().rename("all_titles").reset_index()
    out = out.merge(total, on=["year", "language"], how="left")
    out["match_rate"] = out.titles / out.all_titles
    return out.sort_values(["year", "language", "label_source"]).reset_index(drop=True)


def yearly_label_coverage(credits: pd.DataFrame) -> pd.DataFrame:
    """Pre-model yearly title-label coverage for choosing temporal boundaries."""
    titles = credits[["title_id", "year", "language", "label_source"]].drop_duplicates("title_id").copy()
    titles["language"] = titles.language.fillna("unknown").astype(str).str.lower()
    titles["label_source"] = titles.label_source.fillna("unknown").astype(str).str.upper()
    titles["is_rule_ambiguous"] = titles.label_source.eq("RULE_AMBIGUOUS")
    titles["is_unknown"] = titles.language.eq("unknown") & ~titles.is_rule_ambiguous
    titles["is_resolved"] = ~titles.language.eq("unknown") & ~titles.is_rule_ambiguous
    out = titles.groupby("year", observed=True).agg(
        candidate_titles=("title_id", "nunique"), unknown_titles=("is_unknown", "sum"),
        rule_ambiguous_titles=("is_rule_ambiguous", "sum"), resolved_titles=("is_resolved", "sum")
    ).reset_index().sort_values("year")
    out["unknown_share"] = out.unknown_titles / out.candidate_titles
    out["resolved_share"] = out.resolved_titles / out.candidate_titles
    return out


def yearly_event_counts(panel: pd.DataFrame) -> pd.DataFrame:
    """Pre-model annual event and at-risk counts; safe for split selection."""
    return (panel.groupby("year", observed=True)
            .agg(first_entry_events=("event", "sum"), at_risk_actor_years=("event", "size"),
                 at_risk_actors=("actor_id", "nunique"))
            .reset_index().sort_values("year"))


def _source_at_start_of_year(actor_events: pd.DataFrame, destination: str, year: int,
                             unknown_before: int = 0, count_unknown: bool = False) -> str | None:
    """Get an eligible, unambiguous most-recent source using only prior years.

    The source files provide calendar years rather than within-year title dates.
    If the actor has clean credits in multiple languages in their latest prior
    credit year, the source is ambiguous and no source is guessed.
    """
    prior = actor_events[actor_events.year.lt(year)]
    if prior.empty:
        return None
    most_recent_year = int(prior.year.max())
    most_recent_languages = prior.loc[prior.year.eq(most_recent_year), "language"].unique()
    if len(most_recent_languages) != 1:
        return None
    source = str(most_recent_languages[0])
    if source not in FOCAL_LANGUAGES or source == destination:
        return None
    source_rows = prior[prior.language.eq(source)]
    denominator = len(prior) + (unknown_before if count_unknown else 0)
    if len(source_rows) < 5 or source_rows.year.nunique() < 3:
        return None
    if denominator == 0 or len(source_rows) / denominator < .70:
        return None
    return source


def _latest_prior_language(actor_events: pd.DataFrame, year: int) -> str | None:
    """Unambiguous latest resolved language before a collaborator move year."""
    prior = actor_events[actor_events.year.lt(year)]
    if prior.empty:
        return None
    latest_year = int(prior.year.max())
    languages = prior.loc[prior.year.eq(latest_year), "language"].unique()
    return str(languages[0]) if len(languages) == 1 else None


def _unknown_counts_by_actor(credits: pd.DataFrame, count_unknown: bool) -> dict[str, dict[int, int]]:
    """Index unknown, non-rule-flagged credits for the denominator sensitivity."""
    if not count_unknown:
        return {}
    unknown = credits[credits.language.eq("unknown")].copy()
    if "is_ambiguous" in unknown:
        unknown = unknown[~unknown.is_ambiguous]
    if "label_source" in unknown:
        unknown = unknown[~unknown.label_source.eq("RULE_AMBIGUOUS")]
    return {str(actor): {int(y): int(n) for y, n in group.groupby("year").size().items()}
            for actor, group in unknown.groupby("actor_id", sort=False)}


def _eligible_transitions(event_credits: pd.DataFrame, observation_end: int | None = None,
                          all_credits: pd.DataFrame | None = None,
                          count_unknown: bool = False) -> pd.DataFrame:
    """Build actor-destination risk rows with source re-evaluated before each year."""
    columns = ["actor_id", "source", "destination", "year", "event", "qualifying_year",
               "first_observed_year", "observation_end"]
    if event_credits.empty:
        return pd.DataFrame(columns=columns)
    observation_end = int(observation_end if observation_end is not None else event_credits.year.max())
    year_source = all_credits if all_credits is not None else event_credits
    observed_years = {int(y) for y in year_source.year.unique() if int(y) <= observation_end}
    first_year = min(observed_years) if observed_years else observation_end
    # Evaluate eligibility at every calendar-year boundary, but emit actor-year
    # observations only in years represented in the source data. An absent year
    # contributes no credit year and is not silently added to at-risk time.
    years = range(first_year, observation_end + 1)
    focal = sorted(FOCAL_LANGUAGES.intersection(event_credits.language.unique()))
    unknown_by_actor_year = _unknown_counts_by_actor(all_credits, count_unknown) if all_credits is not None else {}

    output = []
    all_first_observed = (all_credits.groupby("actor_id").year.min().to_dict()
                          if all_credits is not None else
                          event_credits.groupby("actor_id").year.min().to_dict())
    for actor, actor_events in event_credits.groupby("actor_id", sort=False):
        first_observed = int(all_first_observed.get(actor, actor_events.year.min()))
        # Compact each actor's history once. The old implementation repeatedly
        # filtered a DataFrame for every actor × destination × year; on the full
        # credits file that dominated both preflight and panel construction.
        year_language_counts: dict[int, dict[str, int]] = {}
        for (credit_year, language), count in actor_events.groupby(["year", "language"], sort=False).size().items():
            year_language_counts.setdefault(int(credit_year), {})[str(language)] = int(count)
        unknown_year_counts = unknown_by_actor_year.get(str(actor), {})
        for destination in focal:
            active_source = None
            qualifying_year = None
            prior_counts: dict[str, int] = {}
            prior_years: dict[str, set[int]] = {}
            prior_labelled_total = 0
            prior_unknown_total = 0
            latest_languages: set[str] = set()
            for year in years:
                year = int(year)
                if year <= first_observed:
                    current = year_language_counts.get(year, {})
                    for language, count in current.items():
                        prior_counts[language] = prior_counts.get(language, 0) + count
                        prior_years.setdefault(language, set()).add(year)
                        prior_labelled_total += count
                    if current:
                        latest_languages = set(current)
                    prior_unknown_total += int(unknown_year_counts.get(year, 0))
                    continue
                # The actor is no longer at risk in D after a prior clean D credit.
                if prior_counts.get(destination, 0) > 0:
                    break
                source = next(iter(latest_languages)) if len(latest_languages) == 1 else None
                if source in FOCAL_LANGUAGES and source != destination:
                    denominator = prior_labelled_total + (prior_unknown_total if count_unknown else 0)
                    eligible = (prior_counts.get(source, 0) >= 5 and
                                len(prior_years.get(source, set())) >= 3 and
                                denominator > 0 and prior_counts[source] / denominator >= .70)
                else:
                    eligible = False
                if eligible:
                    # A risk spell belongs to the currently eligible source.
                    # Reset duration if the source changes; if eligibility is
                    # interrupted, the next eligible observation starts a new
                    # spell instead of inheriting an earlier qualifying year.
                    if source != active_source:
                        active_source = source
                        qualifying_year = year
                    if year in observed_years:
                        current_destination = int(year_language_counts.get(year, {}).get(destination, 0) > 0)
                        output.append({"actor_id": str(actor), "source": source,
                                       "destination": destination, "year": year, "event": current_destination,
                                       "qualifying_year": qualifying_year,
                                       "first_observed_year": first_observed,
                                       "observation_end": observation_end})
                        if current_destination:
                            break
                else:
                    active_source = None
                    qualifying_year = None
                current = year_language_counts.get(year, {})
                for language, count in current.items():
                    prior_counts[language] = prior_counts.get(language, 0) + count
                    prior_years.setdefault(language, set()).add(year)
                    prior_labelled_total += count
                if current:
                    latest_languages = set(current)
                prior_unknown_total += int(unknown_year_counts.get(year, 0))
    return pd.DataFrame(output, columns=columns)


def select_pairs(credits: pd.DataFrame, min_events: int = 10, min_at_risk: int = 100,
                 strict: bool = False, count_unknown: bool = False) -> pd.DataFrame:
    """Pre-model pair screen without building network-exposure features."""
    events = _event_credits(credits, strict)
    transitions = _eligible_transitions(events, int(credits.year.max()), credits, count_unknown)
    return _pair_counts(transitions, min_events, min_at_risk)


def _pair_counts(transitions: pd.DataFrame, min_events: int, min_at_risk: int) -> pd.DataFrame:
    columns = ["source", "destination", "events", "eligible_actors", "at_risk_actor_years", "selected"]
    if transitions.empty:
        return pd.DataFrame(columns=columns)
    out = (transitions.groupby(["source", "destination"], observed=True)
           .agg(events=("event", "sum"), eligible_actors=("actor_id", "nunique"),
                at_risk_actor_years=("event", "size")).reset_index())
    out["selected"] = (out.events >= min_events) & (out.at_risk_actor_years >= min_at_risk)
    return out.sort_values(["selected", "events"], ascending=[False, False])


def preflight_tables(credits: pd.DataFrame, min_events: int = 10, min_at_risk: int = 100,
                     strict: bool = False, count_unknown: bool = False
                     ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Build screening tables only; skip expensive network-exposure construction."""
    events = _event_credits(credits, strict)
    transitions = _eligible_transitions(events, int(credits.year.max()), credits, count_unknown)
    return (_pair_counts(transitions, min_events, min_at_risk),
            yearly_event_counts(transitions), yearly_label_coverage(credits))


def permute_timing_features(panel: pd.DataFrame, seed: int) -> pd.DataFrame:
    """Shuffle S→D exposure vectors within actor-pair spells, keeping graph fixed."""
    permuted = panel.copy()
    columns = ["sd_collaborations", "sd_collaborators"]
    missing = set(columns) - set(permuted.columns)
    if missing:
        raise ValueError(f"Panel lacks S→D timing features: {sorted(missing)}")
    rng = np.random.default_rng(seed)
    for _, idx in permuted.groupby(["actor_id", "source", "destination"], sort=False).groups.items():
        order = rng.permutation(len(idx))
        values = permuted.loc[idx, columns].to_numpy(copy=True)
        permuted.loc[idx, columns] = values[order]
    return permuted


def build_actor_year(credits: pd.DataFrame, source: str | None = None,
                     destination: str | None = None, exposure_lookback_years: int | None = None,
                     strict: bool = False, timing_permutation_seed: int | None = None,
                     count_unknown: bool = False) -> pd.DataFrame:
    """Build annual risk rows and strictly prior cumulative network exposures.

    Main-model exposure is cumulative over all strictly prior qualifying ties.
    `exposure_lookback_years=2` implements the pre-registered two-most-recent-
    years sensitivity.
    """
    if exposure_lookback_years is not None and exposure_lookback_years < 1:
        raise ValueError("exposure_lookback_years must be at least 1")
    events = _event_credits(credits, strict)
    if events.empty:
        raise ValueError("No resolved language credits remain under the selected label mode.")
    transitions = _eligible_transitions(events, int(credits.year.max()), credits, count_unknown)
    if source:
        transitions = transitions[transitions.source.eq(source.lower())]
    if destination:
        transitions = transitions[transitions.destination.eq(destination.lower())]
    if transitions.empty:
        raise ValueError("No eligible actor-language-pair risk rows under the selected criteria.")

    # All supplied title rows form ties. Label and ambiguity status only decide
    # whether a row can contribute language evidence.
    target_actors = set(transitions.actor_id.astype(str))
    title_cast = credits.groupby("title_id", sort=False).agg(
        year=("year", "min"), actors=("actor_id", lambda x: tuple(pd.unique(x.astype(str))))
    )
    ties: dict[str, dict[str, list[int]]] = {}
    for title_year, actors in title_cast.itertuples(index=False, name=None):
        relevant = target_actors.intersection(actors)
        for actor in relevant:
            neighbors = ties.setdefault(actor, {})
            for collaborator in actors:
                if collaborator != actor:
                    neighbors.setdefault(collaborator, []).append(int(title_year))
    for neighbors in ties.values():
        for years in neighbors.values():
            years.sort()

    origins: dict[tuple[str, str], tuple[str, int]] = {}
    event_histories = {str(actor): group for actor, group in events.groupby("actor_id", sort=False)}
    first_d = events.groupby(["actor_id", "language"], sort=False).year.min()
    for (actor, lang), move_year in first_d.items():
        if lang not in FOCAL_LANGUAGES:
            continue
        history = event_histories[str(actor)]
        origin = _latest_prior_language(history, int(move_year))
        if origin is not None:
            origins[(str(actor), str(lang))] = (origin, int(move_year))

    # Prior clean career snapshots, keyed by actor and year, avoid repeatedly
    # filtering the full credit table for each risk row.
    history_cache: dict[tuple[str, int], tuple[dict[str, int], set[str], int]] = {}
    min_year, max_year = int(credits.year.min()), int(credits.year.max())
    annual_counts = events.groupby(["actor_id", "year", "language"], sort=False).size().to_dict()
    for actor, group in events[events.actor_id.isin(target_actors)].groupby("actor_id", sort=False):
        per_year = {int(y): g.groupby("language").size().to_dict()
                    for y, g in group.groupby("year", sort=False)}
        counts: dict[str, int] = {}
        for y in range(min_year, max_year + 1):
            history_cache[(str(actor), y)] = (counts.copy(), set(counts),
                                               int(per_year.get(y - 1, {}).get("unknown", 0)))
            for lang, count in per_year.get(y, {}).items():
                counts[lang] = counts.get(lang, 0) + int(count)

    rows = []
    for tr in transitions.itertuples(index=False):
        actor = str(tr.actor_id)
        neighbors = ties.get(actor, {})
        year = int(tr.year)
        career_collabs = 0
        xd_collabs = xd_actors = 0
        sd_collabs = sd_actors = 0
        lead_sd_collabs = lead_sd_actors = 0
        reverse_sd_collabs = reverse_sd_actors = 0
        for collaborator, tie_years in neighbors.items():
            n_prior_ties = bisect_left(tie_years, year)
            career_collabs += n_prior_ties
            # Reverse-direction control: collaborator's prior D→S transition
            # for a focal S→D pathway. The focal collaboration must still
            # precede the observation year.
            reverse_move = origins.get((collaborator, tr.source))
            if reverse_move is not None:
                reverse_origin, reverse_year = reverse_move
                if reverse_origin == tr.destination and reverse_year < year:
                    reverse_sd_collabs += n_prior_ties
                    reverse_sd_actors += int(n_prior_ties > 0)
            move = origins.get((collaborator, tr.destination))
            if move is None:
                continue
            origin, move_year = move
            lag = year - move_year
            if move_year < year and (exposure_lookback_years is None or lag <= exposure_lookback_years):
                if origin == tr.source:
                    sd_collabs += n_prior_ties
                    sd_actors += int(n_prior_ties > 0)
                else:
                    xd_collabs += n_prior_ties
                    xd_actors += int(n_prior_ties > 0)
            # Genuine network lead: a collaborator's first D entry occurs at
            # t+1. This does not use at-risk-panel shift(), avoiding terminal
            # event leakage caused by panel rows ending at the outcome year.
            if origin == tr.source and move_year == year + 1 and n_prior_ties:
                lead_sd_collabs += n_prior_ties
                lead_sd_actors += 1
        prior_counts, prior_languages, _ = history_cache.get((actor, year), ({}, set(), 0))
        annual_source = int(annual_counts.get((actor, year - 1, tr.source), 0))
        rows.append({"actor_id": actor, "source": tr.source, "destination": tr.destination,
                     "pair": f"{tr.source}->{tr.destination}", "year": year,
                     "event": int(tr.event), "qualifying_year": int(tr.qualifying_year),
                     "risk_duration": int(year - int(tr.qualifying_year) + 1),
                     "career_age": year - int(tr.first_observed_year),
                     "career_exposure": career_collabs,
                     "source_credits": int(prior_counts.get(tr.source, 0)),
                     "other_languages": len(prior_languages - {tr.source, tr.destination}),
                     "annual_source_credits": annual_source,
                     "xd_collaborations": xd_collabs, "xd_collaborators": xd_actors,
                     "sd_collaborations": sd_collabs, "sd_collaborators": sd_actors,
                     "lead_sd_collaborations": lead_sd_collabs,
                     "lead_sd_collaborators": lead_sd_actors,
                     "reverse_sd_collaborations": reverse_sd_collabs,
                     "reverse_sd_collaborators": reverse_sd_actors,
                     "left_truncated_at_study_start": int(tr.first_observed_year == min_year)})
    panel = pd.DataFrame(rows).drop_duplicates(["actor_id", "source", "destination", "year"])
    if timing_permutation_seed is not None and not panel.empty:
        panel = permute_timing_features(panel, timing_permutation_seed)
    return panel.sort_values(["year", "actor_id", "source", "destination"]).reset_index(drop=True)


CAREER = ["risk_duration", "year", "career_age", "source_credits", "other_languages",
          "annual_source_credits", "career_exposure"]
XD = ["xd_collaborations", "xd_collaborators"]
SD = ["sd_collaborations", "sd_collaborators"]
REVERSE_SD = ["reverse_sd_collaborations", "reverse_sd_collaborators"]
CATEGORICAL = ["pair"]


def _make_model(numeric, categorical=CATEGORICAL):
    prep = ColumnTransformer([
        ("num", Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), numeric),
        ("cat", OneHotEncoder(handle_unknown="ignore"), categorical),
    ])
    return Pipeline([("prep", prep), ("model", LogisticRegression(max_iter=2000, C=.5))])


def _metrics(y: np.ndarray, p: np.ndarray) -> dict:
    return {"AUPRC": average_precision_score(y, p), "ROC_AUC": roc_auc_score(y, p),
            "Brier": brier_score_loss(y, p)}


def _cluster_bootstrap(test: pd.DataFrame, predictions: dict[str, np.ndarray],
                       reps: int = 1000, seed: int = 2026) -> dict:
    rng = np.random.default_rng(seed)
    actors = test.actor_id.astype(str).unique()
    grouped = {a: np.flatnonzero(test.actor_id.astype(str).to_numpy() == a) for a in actors}
    metric_names = ["AUPRC", "ROC_AUC", "Brier"]
    model_values = {name: {metric: [] for metric in metric_names} for name in predictions}
    comparisons = [("career_plus_XD", "career_only", "XD_minus_career"),
                   ("career_plus_XD_SD", "career_plus_XD", "SD_increment")]
    delta_values = {label: {metric: [] for metric in metric_names} for _, _, label in comparisons}
    y = test.event.to_numpy()
    for _ in range(reps):
        sample = rng.choice(actors, size=len(actors), replace=True)
        ix = np.concatenate([grouped[a] for a in sample])
        if np.unique(y[ix]).size < 2:
            continue
        metrics = {name: _metrics(y[ix], pred[ix]) for name, pred in predictions.items()}
        for name in predictions:
            for metric in metric_names:
                model_values[name][metric].append(metrics[name][metric])
        for added, base, label in comparisons:
            for metric in metric_names:
                delta_values[label][metric].append(metrics[added][metric] - metrics[base][metric])
    out = {}
    for name, metric_data in model_values.items():
        for metric, values in metric_data.items():
            out[f"{name}_{metric}_CI_low"] = float(np.quantile(values, .025)) if values else np.nan
            out[f"{name}_{metric}_CI_high"] = float(np.quantile(values, .975)) if values else np.nan
    for label, metric_data in delta_values.items():
        for metric, values in metric_data.items():
            out[f"delta_{label}_{metric}_CI_low"] = float(np.quantile(values, .025)) if values else np.nan
            out[f"delta_{label}_{metric}_CI_high"] = float(np.quantile(values, .975)) if values else np.nan
    return out


def _model_specs():
    return [("career_only", CAREER), ("career_plus_XD", CAREER + XD),
            ("career_plus_XD_SD", CAREER + XD + SD)]


def evaluate(actor_year: pd.DataFrame, train_end_year: int, validation_end_year: int,
             bootstrap_reps: int = 1000, seed: int = 2026) -> tuple[pd.DataFrame, dict]:
    """Evaluate with declared train, validation, and untouched test periods.

    Models are fit on train for validation metrics, then refit on train+validation
    for the final test metrics. Split years are required so this code cannot
    silently choose them from model results or a sample quantile.
    """
    if validation_end_year <= train_end_year:
        raise ValueError("validation_end_year must be later than train_end_year")
    train = actor_year[actor_year.year <= train_end_year]
    validation = actor_year[(actor_year.year > train_end_year) & (actor_year.year <= validation_end_year)]
    test = actor_year[actor_year.year > validation_end_year].copy().reset_index(drop=True)
    for label, frame in [("train", train), ("validation", validation), ("test", test)]:
        if frame.empty or frame.event.nunique() < 2:
            raise ValueError(f"{label} period needs both event classes; revise predeclared boundaries using event counts only.")

    validation_predictions: dict[str, np.ndarray] = {}
    test_predictions: dict[str, np.ndarray] = {}
    result_rows = []
    for label, features in _model_specs():
        xcols = features + CATEGORICAL
        model = _make_model(features)
        model.fit(train[xcols], train.event)
        validation_pred = model.predict_proba(validation[xcols])[:, 1]
        validation_predictions[label] = validation_pred
        model.fit(pd.concat([train, validation])[xcols], pd.concat([train, validation]).event)
        test_pred = model.predict_proba(test[xcols])[:, 1]
        test_predictions[label] = test_pred
        result_rows.append({"model": label,
                            **{f"validation_{k}": v for k, v in _metrics(validation.event.to_numpy(), validation_pred).items()},
                            **{f"test_{k}": v for k, v in _metrics(test.event.to_numpy(), test_pred).items()},
                            "train_rows": len(train), "validation_rows": len(validation), "test_rows": len(test),
                            "validation_events": int(validation.event.sum()), "test_events": int(test.event.sum()),
                            "train_end_year": train_end_year, "validation_end_year": validation_end_year})
    if bootstrap_reps:
        ci = _cluster_bootstrap(test, test_predictions, bootstrap_reps, seed)
        for row in result_rows:
            name = row["model"]
            for metric in ["AUPRC", "ROC_AUC", "Brier"]:
                row[f"test_{metric}_CI_low"] = ci[f"{name}_{metric}_CI_low"]
                row[f"test_{metric}_CI_high"] = ci[f"{name}_{metric}_CI_high"]
            if name == "career_plus_XD":
                delta = "XD_minus_career"
            elif name == "career_plus_XD_SD":
                delta = "SD_increment"
            else:
                delta = None
            if delta:
                for metric in ["AUPRC", "ROC_AUC", "Brier"]:
                    row[f"delta_{delta}_{metric}_CI_low"] = ci[f"delta_{delta}_{metric}_CI_low"]
                    row[f"delta_{delta}_{metric}_CI_high"] = ci[f"delta_{delta}_{metric}_CI_high"]
    score_map = {row["model"]: row for row in result_rows}
    for added, base, delta in [("career_plus_XD", "career_only", "XD_minus_career"),
                               ("career_plus_XD_SD", "career_plus_XD", "SD_increment")]:
        for metric in ["AUPRC", "ROC_AUC", "Brier"]:
            score_map[added][f"delta_{delta}_{metric}"] = (
                score_map[added][f"test_{metric}"] - score_map[base][f"test_{metric}"])
    metadata = {"train_start": int(train.year.min()), "train_end": train_end_year,
                "validation_start": int(validation.year.min()), "validation_end": validation_end_year,
                "test_start": int(test.year.min()), "test_end": int(test.year.max()),
                "bootstrap_unit": "actor cluster", "bootstrap_seed": seed,
                "bootstrap_reps_requested": bootstrap_reps}
    return pd.DataFrame(result_rows), metadata


def clustered_coefficients(actor_year: pd.DataFrame) -> pd.DataFrame:
    """Fit pooled models on the full risk set and report actor-clustered SEs.

    Pair fixed effects absorb baseline differences across the 20 directional
    language pairs. These coefficients are descriptive associations.
    """
    if sm is None or smf is None:
        raise ImportError("Install the project dependencies (`pip install -e '.[dev]'`) for clustered inference.")
    out = []
    for label, features in _model_specs():
        cols = ["event", "actor_id", "pair"] + features
        dat = actor_year[cols].dropna().copy()
        formula = "event ~ " + " + ".join(features) + " + C(pair)"
        fit = smf.glm(formula=formula, data=dat, family=sm.families.Binomial()).fit(
            cov_type="cluster", cov_kwds={"groups": dat.actor_id.astype(str)})
        ci = fit.conf_int()
        for term in fit.params.index:
            out.append({"model": label, "term": term, "coefficient": float(fit.params[term]),
                        "clustered_se": float(fit.bse[term]), "z": float(fit.tvalues[term]),
                        "p_value": float(fit.pvalues[term]), "ci_low": float(ci.loc[term, 0]),
                        "ci_high": float(ci.loc[term, 1]), "clusters": int(dat.actor_id.nunique()),
                        "rows": int(len(dat))})
    return pd.DataFrame(out)


def likelihood_ratio_tests(actor_year: pd.DataFrame) -> pd.DataFrame:
    """Full-risk-set nested GLM likelihood-ratio tests as supplementary inference."""
    if sm is None or smf is None:
        raise ImportError("Install the project dependencies (`pip install -e '.[dev]'`) for likelihood-ratio tests.")
    fitted = []
    for label, features in _model_specs():
        cols = ["event", "pair"] + features
        dat = actor_year[cols].dropna().copy()
        formula = "event ~ " + " + ".join(features) + " + C(pair)"
        fit = smf.glm(formula=formula, data=dat, family=sm.families.Binomial()).fit()
        fitted.append((label, fit))
    rows = []
    for (base_name, base), (full_name, full) in zip(fitted, fitted[1:]):
        statistic = max(0.0, 2 * (full.llf - base.llf))
        df_diff = int(round(full.df_model - base.df_model))
        rows.append({"comparison": f"{full_name}_vs_{base_name}", "LR_statistic": statistic,
                     "df": df_diff, "p_value": float(chi2.sf(statistic, df_diff)) if df_diff > 0 else np.nan,
                     "rows": int(full.nobs),
                     "note": "Full risk set; supplementary association test, not held-out prediction."})
    return pd.DataFrame(rows)


def run_placebo(actor_year: pd.DataFrame, train_end_year: int,
                validation_end_year: int) -> pd.DataFrame:
    """Compare true exposure with independently constructed network lead exposure."""
    train = actor_year[actor_year.year <= train_end_year]
    validation = actor_year[(actor_year.year > train_end_year) & (actor_year.year <= validation_end_year)]
    test = actor_year[actor_year.year > validation_end_year]
    if any(frame.empty or frame.event.nunique() < 2 for frame in (train, validation, test)):
        raise ValueError("Placebo requires event and non-event observations in train, validation, and test periods.")
    lead_features = ["lead_sd_collaborations", "lead_sd_collaborators"]
    if not set(lead_features).issubset(actor_year.columns):
        raise ValueError("Panel lacks true network lead features; rebuild the panel with the current feature builder.")
    fit_period = pd.concat([train, validation])
    specs = [("true_SD_exposure", CAREER + XD + SD),
             ("true_network_lead_placebo", CAREER + XD + lead_features)]
    out = []
    for label, features in specs:
        model = _make_model(features)
        model.fit(fit_period[features + CATEGORICAL], fit_period.event)
        pred = model.predict_proba(test[features + CATEGORICAL])[:, 1]
        out.append({"test": label, **_metrics(test.event.to_numpy(), pred),
                    "test_events": int(test.event.sum()), "train_end_year": train_end_year,
                    "validation_end_year": validation_end_year})
    return pd.DataFrame(out)


def run_reverse_direction_check(actor_year: pd.DataFrame, train_end_year: int,
                                validation_end_year: int, bootstrap_reps: int = 1000,
                                seed: int = 2026) -> pd.DataFrame:
    """Compare matched S→D exposure with reverse D→S collaborator exposure.

    The focal outcome remains first entry into D from the current source S.
    The reverse-control model substitutes collaborators' prior D→S exposure
    for the matched S→D exposure. All models use the same temporal split.
    """
    required = set(REVERSE_SD + SD + XD + CAREER + CATEGORICAL + ["event", "year", "actor_id"])
    missing = required - set(actor_year.columns)
    if missing:
        raise ValueError(f"Panel lacks reverse-direction check columns: {sorted(missing)}")
    train = actor_year[actor_year.year <= train_end_year]
    validation = actor_year[(actor_year.year > train_end_year) & (actor_year.year <= validation_end_year)]
    test = actor_year[actor_year.year > validation_end_year].copy().reset_index(drop=True)
    for label, frame in [("train", train), ("validation", validation), ("test", test)]:
        if frame.empty or frame.event.nunique() < 2:
            raise ValueError(f"{label} period needs both event classes for the reverse-direction check.")
    fit_period = pd.concat([train, validation])
    specs = [
        ("career_plus_XD", CAREER + XD),
        ("career_plus_XD_SD", CAREER + XD + SD),
        ("career_plus_XD_reverse_DS", CAREER + XD + REVERSE_SD),
    ]
    predictions: dict[str, np.ndarray] = {}
    rows = []
    for label, features in specs:
        model = _make_model(features)
        columns = features + CATEGORICAL
        model.fit(fit_period[columns], fit_period.event)
        pred = model.predict_proba(test[columns])[:, 1]
        predictions[label] = pred
        rows.append({"model": label, **{f"test_{k}": v for k, v in
                                        _metrics(test.event.to_numpy(), pred).items()},
                     "test_events": int(test.event.sum()),
                     "train_end_year": train_end_year,
                     "validation_end_year": validation_end_year})

    # Paired actor bootstrap for the incremental predictive scores against M2.
    rng = np.random.default_rng(seed)
    actor_ids = test.actor_id.astype(str).to_numpy()
    actors = np.unique(actor_ids)
    grouped = {a: np.flatnonzero(actor_ids == a) for a in actors}
    deltas = {name: {metric: [] for metric in ["AUPRC", "ROC_AUC", "Brier"]}
              for name in ["career_plus_XD_SD", "career_plus_XD_reverse_DS"]}
    y = test.event.to_numpy()
    for _ in range(bootstrap_reps):
        sample = rng.choice(actors, size=len(actors), replace=True)
        ix = np.concatenate([grouped[a] for a in sample])
        if np.unique(y[ix]).size < 2:
            continue
        base = _metrics(y[ix], predictions["career_plus_XD"][ix])
        for name in deltas:
            added = _metrics(y[ix], predictions[name][ix])
            for metric in deltas[name]:
                deltas[name][metric].append(added[metric] - base[metric])
    for row in rows:
        if row["model"] == "career_plus_XD":
            continue
        for metric, values in deltas[row["model"]].items():
            row[f"delta_vs_XD_{metric}"] = row[f"test_{metric}"] - next(
                base_row[f"test_{metric}"] for base_row in rows
                if base_row["model"] == "career_plus_XD")
            row[f"delta_vs_XD_{metric}_CI_low"] = float(np.quantile(values, .025)) if values else np.nan
            row[f"delta_vs_XD_{metric}_CI_high"] = float(np.quantile(values, .975)) if values else np.nan
    return pd.DataFrame(rows)


def timing_permutation_test(credits: pd.DataFrame, source: str | None,
                            destination: str | None,
                            train_end_year: int, validation_end_year: int,
                            reps: int = 1000, seed: int = 2026,
                            strict: bool = False) -> pd.DataFrame:
    """Test incremental S→D predictive information after permuting exposure timing.

    Each replicate permutes the vector of S→D exposure features within each
    actor-pathway spell. This preserves the actor's exposure totals and fixed
    network while breaking their alignment with event years. The statistic is
    the change in test AUPRC from Model 2 to Model 3.
    """
    if (source is None) != (destination is None):
        raise ValueError("Provide both source and destination for a pair-specific permutation, or neither for pooled.")
    panel = build_actor_year(credits, source, destination, strict=strict)
    observed, _ = evaluate(panel, train_end_year, validation_end_year, bootstrap_reps=0)
    base = float(observed.loc[observed.model.eq("career_plus_XD"), "test_AUPRC"].iloc[0])
    full = float(observed.loc[observed.model.eq("career_plus_XD_SD"), "test_AUPRC"].iloc[0])
    observed_gain = full - base
    null = []
    for i in range(reps):
        permuted = permute_timing_features(panel, seed + i + 1)
        scores, _ = evaluate(permuted, train_end_year, validation_end_year, bootstrap_reps=0)
        b = float(scores.loc[scores.model.eq("career_plus_XD"), "test_AUPRC"].iloc[0])
        f = float(scores.loc[scores.model.eq("career_plus_XD_SD"), "test_AUPRC"].iloc[0])
        null.append(f - b)
    p = (1 + sum(x >= observed_gain for x in null)) / (reps + 1)
    return pd.DataFrame([{"source": source or "all", "destination": destination or "all",
                          "observed_AUPRC_gain": observed_gain, "permutation_p_value": p,
                          "permutations": reps, "null_mean_gain": float(np.mean(null)),
                          "null_95pct": float(np.quantile(null, .95)), "seed": seed}])


def write_json(data: dict, path: str | Path) -> None:
    Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")


def graph_connectivity_audit(credits: pd.DataFrame) -> pd.DataFrame:
    """Compare all-title graph connectivity with the resolved-language-only graph."""
    all_actors = set(credits.actor_id.astype(str))
    def stats(frame: pd.DataFrame, label: str) -> dict:
        # Keep the same node universe in both graphs. Actors with no remaining
        # resolved-title ties become isolates instead of disappearing.
        actors = all_actors
        parent = {a:a for a in actors}
        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        def union(a,b):
            ra, rb = find(a), find(b)
            if ra != rb: parent[rb] = ra
        for _, group in frame.groupby("title_id"):
            members = sorted(set(group.actor_id.astype(str)))
            if len(members) > 1:
                for actor in members[1:]: union(members[0], actor)
        sizes = pd.Series([find(a) for a in actors]).value_counts() if actors else pd.Series(dtype=int)
        return {"graph":label, "actors":len(actors), "actors_with_edges":int(frame.actor_id.nunique()), "components":len(sizes),
                "largest_component_actors":int(sizes.max()) if len(sizes) else 0,
                "largest_component_share":float(sizes.max()/len(actors)) if len(sizes) else 0.0}
    resolved = _event_credits(credits, strict=False)
    return pd.DataFrame([stats(credits, "all_titles_including_unknown"),
                         stats(resolved, "resolved_clean_titles_only")])
