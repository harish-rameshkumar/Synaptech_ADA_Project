# Modeling plan and run guide

This document describes the modeling implementation. It reports predictive associations, not causal effects.

## Before fitting models

The working split is **train through 2016, validation 2017–2019, and test 2020–2024**. Regenerate the model-free screening tables after finalizing label filters and panel construction; verify event coverage in each period and record the confirmed split in the preregistration before running `analyze`.

```bash
python -m pip install -e ".[dev]"

cinema-mobility-model preflight \
  --credits data/processed/credits_final_v2.parquet \
  --outdir results/modeling/preflight
```

This writes `pair_screen.csv`, `events_by_year.csv`, and `label_coverage_by_year.csv`. It does not fit a model or build network-exposure features; it computes eligible risk rows and screening summaries. The `selected` flag applies the pre-model thresholds of 10 events and 100 at-risk actor-years; it does not filter the pooled analysis. The pooled 20-pair analysis remains primary. Under the current provisional screen, only `ml→ta` meets the registered ≥100-event secondary per-pair threshold.

## Primary analysis

After the split is documented, build the actor-year panel and analyze it. The two boundary years are required inputs: `train_end_year` is inclusive, `validation_end_year` is inclusive, and later years form the test set.

```bash
cinema-mobility-model build \
  --credits data/processed/credits_final_v2.parquet \
  --out results/modeling/actor_year_final_v2.parquet

cinema-mobility-model analyze \
  --actor-years results/modeling/actor_year_final_v2.parquet \
  --train-end-year 2016 \
  --validation-end-year 2019 \
  --bootstrap-reps 1000 \
  --outdir results/modeling/final_v2
```

The model hierarchy is:

1. Career history, duration at risk, calendar year, and pair fixed effects.
2. Model 1 plus cumulative prior X→D exposure.
3. Model 2 plus cumulative prior S→D exposure.

Career exposure is the cumulative number of all prior co-appearance titles. X→D and S→D exposures count only co-appearance titles strictly after the collaborator's first clean entry into D and strictly before the focal observation year. Ties in the collaborator's transition year are not counted because within-year ordering is unavailable. Collaborators are classified by whether their latest unambiguous resolved language before their D entry was different from or equal to the focal actor's current source S. The same post-transition tie rule applies to the reverse D→S feature. The lead-exposure placebo intentionally counts pre-transition ties to collaborators whose first S→D entry occurs in the following calendar year. The source S is recalculated from the focal actor's latest unambiguous resolved language before each observation year. If multiple clean languages occur in that latest prior calendar year, the source is treated as ambiguous for that observation; the code does not guess an ordering within that year.

Eligibility is based only on information before the observation year: at least five S credits across at least three distinct observed credit years, at least 70% of prior clean labelled credits in S, and no prior D credit. Unknown and rule-ambiguous credits are excluded from the primary denominator. The eligibility sensitivity counts unknown, non-rule-flagged credits in the denominator.

All supplied title rows contribute collaboration ties, including unknown, dubbed, and ambiguity-flagged rows. Current event construction excludes unknown, rule-ambiguous, `is_ambiguous`, and `is_dubbed` credits from language events and source eligibility; the dubbed flag's meaning must be confirmed with the Data Lead before finalizing counts. Calendar years absent from the input are not fabricated as actor-year observations or source-credit years. In a year present in the dataset, an actor with no recorded credit can still contribute a non-event risk row if eligible. History before the first observed study-period credit cannot be recovered; actors first appearing at the study-period boundary are marked left-truncated in the panel.

The legacy fixed-source sensitivity is available with `--source-mode fixed_legacy`. It qualifies an actor once per source language and keeps that source fixed through the first destination entry or study end. The primary mode is `dynamic`, which recalculates source at each year boundary and starts a new risk spell after interruptions or source changes.

## Outputs and inference

`analyze` writes:

- `model_comparison.csv`: validation and test metrics for all three models, plus paired actor-bootstrap intervals for test metric differences.
- `clustered_coefficients.csv`: full-risk-set GLM coefficients with actor-clustered standard errors and confidence intervals.
- `likelihood_ratio_tests.csv`: nested full-risk-set model tests as supplementary association analyses.
- `placebo_comparison.csv`: test metrics for true S→D exposure versus the next-year network lead placebo; this is a descriptive negative-control comparison, without a separate permutation p-value.
- `reverse_direction_comparison.csv`: Model 2 plus matched S→D exposure versus Model 2 plus the reverse D→S collaborator exposure, evaluated on the same temporal split with paired actor-bootstrap intervals.
- `split.json`: exact year boundaries and bootstrap settings.

Bootstrap resampling samples actors, retaining all of each sampled actor's test rows. Predictions are held fixed during metric resampling, so these intervals describe test-set metric uncertainty conditional on the fitted models. The coefficient table uses a separate unpenalized GLM for actor-clustered inference.

## Sensitivity analyses

Use the same locked split for all comparisons:

```bash
# Strict Tier 1 labels; all title rows remain available as graph ties.
cinema-mobility-model build --credits data/processed/credits_final_v2.parquet \
  --strict --out results/modeling/actor_year_strict_t1.parquet

# Count unknown, non-rule-flagged credits in the 70% denominator.
cinema-mobility-model build --credits data/processed/credits_final_v2.parquet \
  --count-unknown-in-share --out results/modeling/actor_year_unknown_counted.parquet

# Count qualifying post-transition co-appearance ties only in the two calendar years before t.
cinema-mobility-model build --credits data/processed/credits_final_v2.parquet \
  --exposure-lookback-years 2 --out results/modeling/actor_year_2y_window.parquet

# Legacy fixed-source definition, as a sensitivity analysis.
cinema-mobility-model build --credits data/processed/credits_final_v2.parquet \
  --source-mode fixed_legacy --out results/modeling/actor_year_fixed_source.parquet

# The panel now includes reverse D→S collaborator exposure for the focal S→D outcome.
# The pooled reverse-direction comparison is written automatically by `analyze`.
cinema-mobility-model build --credits data/processed/credits_final_v2.parquet \
  --source ta --destination ml --out results/modeling/actor_year_ta_ml.parquet

# Alternative data versions.
cinema-mobility-model build --credits data/processed/credits_final.parquet \
  --out results/modeling/actor_year_final.parquet
cinema-mobility-model build --credits data/processed/credits.parquet \
  --out results/modeling/actor_year_baseline.parquet

# Pooled timing permutation; use the predeclared 1,000 repetitions for reporting.
cinema-mobility-model permutation --credits data/processed/credits_final_v2.parquet \
  --train-end-year 2016 --validation-end-year 2019 \
  --reps 1000 --seed 2026 --out results/modeling/timing_permutation.csv

# Graph connectivity before and after restricting to clean resolved event titles.
cinema-mobility-model graph-audit --credits data/processed/credits_final_v2.parquet \
  --out results/modeling/graph_connectivity.csv
```

For each sensitivity panel, run `analyze` with the same locked boundaries. The reverse-direction comparison keeps the focal S→D outcome and substitutes prior collaborator D→S exposure for matched S→D exposure. The timing permutation shuffles paired S→D exposure columns within each actor/source/destination/risk-spell group; it uses the test AUPRC gain from Model 2 to Model 3 and a one-sided Monte Carlo p-value `(1 + null gains >= observed gain) / (reps + 1)`, with 1,000 repetitions and seed 2026 by default. The lead placebo compares held-out metrics for true S→D exposure with the next-year network lead; it has no separate p-value in the current implementation. Interpret both checks as predictive diagnostics, not causal tests.

`build` supports `--strict`, `--count-unknown-in-share`, `--exposure-lookback-years`, and `--source-mode`; use one build and one `analyze` run per planned sensitivity. `analyze` fits the three models, actor-clustered full-risk-set GLMs, paired actor-bootstrap metrics, the lead placebo, and the reverse-direction comparison. Timing permutation and graph audit are separate commands.

## Known design limits to report

- The dataset begins in 1980; pre-1980 credits and destination entries cannot be checked.
- Source language cannot be ordered within a calendar year. Rows with conflicting latest-year clean labels are omitted until a later unambiguous source year.
- Actors remain under observation through the study end while their destination is unentered; absence of an actor credit in a year does not add a source credit or qualifying year.
- The event pipeline currently excludes `is_dubbed` rows from language events and eligibility while retaining their graph ties, following the current preregistration instruction; confirm that the data field has this intended meaning with the Data Lead.
- Do not describe any model score or coefficient as a causal effect.
