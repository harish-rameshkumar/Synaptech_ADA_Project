# Modeling plan and run guide

This document describes the modeling implementation. It reports predictive associations, not causal effects.

## Before fitting models

The primary split is **train through 2016, validation 2017–2019, and test 2020–2024**. The existing final-v2 panel and results were verified on 2026-10-26 against these boundaries (see the completed-run inventory below). Recheck model-free coverage if the data or eligibility definition changes; do not choose boundaries from model scores.

```bash
python -m pip install -e ".[dev]"

cinema-mobility-model preflight \
  --credits data/processed/credits_final_v2.parquet \
  --outdir results/modeling/preflight
```

This writes `pair_screen.csv`, `events_by_year.csv`, and `label_coverage_by_year.csv`. It does not fit a model or build network-exposure features; it computes eligible risk rows and screening summaries. The `selected` flag applies the pre-model thresholds of 10 events and 100 at-risk actor-years; it does not filter the pooled analysis. The pooled 20-pair analysis remains primary. Under the verified dynamic screen, only `ml→ta` meets the registered ≥100-event secondary per-pair threshold.

## Primary analysis

After the split is documented, build the actor-year panel and analyze it. The two boundary years are required inputs: `train_end_year` is inclusive, `validation_end_year` is inclusive, and later years form the test set.

```bash
cinema-mobility-model build \
  --credits data/processed/credits_final_v2.parquet \
  --out results/modeling/actor_year_final_v2_dynamic.parquet

cinema-mobility-model analyze \
  --actor-years results/modeling/actor_year_final_v2_dynamic.parquet \
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

All supplied title rows contribute collaboration ties, including unknown and rule-ambiguous rows. Unknown and rule-ambiguous credits do not create language events or source eligibility. The implementation also honors `is_ambiguous` and `is_dubbed` flags when supplied, but **credits_final_v2.parquet has no `is_dubbed` field**: the loader fills the absent flag with false. The Data Lead confirmed that no comprehensive dubbed-title screening was performed. Dubbing is therefore a data limitation, not an applied final-v2 exclusion. No AI-assisted labeling is authorized for this modeling work; the existing data-labeling history remains documented in the data-provenance materials. Calendar years absent from the input are not fabricated as actor-year observations or source-credit years. In a year present in the dataset, an actor with no recorded credit can still contribute a non-event risk row if eligible. History before the first observed study-period credit cannot be recovered; actors first appearing at the study-period boundary are marked left-truncated in the panel.

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

# Completed separately: all 1,000 repetitions, seed 2026.
# Original CLI command retained for reference; use the checkpointed runner below for practical runtime.
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
- Final-v2 has no `is_dubbed` field and no comprehensive dubbed-title screening. Do not claim that dubbed titles were excluded; optional flag support in code is not evidence of screening.
- Do not describe any model score or coefficient as a causal effect.

## Verified output inventory (2026-10-26)

The inventory below applies to **every one of the six files** in each listed analysis directory: `model_comparison.csv`, `clustered_coefficients.csv`, `likelihood_ratio_tests.csv`, `placebo_comparison.csv`, `reverse_direction_comparison.csv`, and `split.json`. After duplicate cleanup, all eight retained directories contain the complete set (48 files). Coefficient and likelihood-ratio row counts match their saved panels; placebo and reverse-direction test-event counts match model comparison. Every split records boundaries 2016/2019, test end 2024, actor-cluster bootstrap seed 2026 and 1,000 requested repetitions. `split.json` alone does not record the input dataset.

Paths below are relative to `results/modeling/`. AUPRC values are test scores in career / +X→D / +X→D+S→D order. Panel splits are train / validation / test, each shown as rows/events.

- `automated_sensitivities_20261025_000626/dataset_baseline/`: **credits.parquet**, dynamic, alternate dataset; panel `automated_sensitivities_20261025_000626/actor_year_dataset_baseline.parquet`. Total 203,672 rows / 1,044 events; splits 130,511/676, 24,424/130, 48,737/238. AUPRC 0.021342 / 0.026616 / 0.030467.
- `automated_sensitivities_20261025_000626/dataset_final/`: **credits_final.parquet**, dynamic, alternate dataset; panel `automated_sensitivities_20261025_000626/actor_year_dataset_final.parquet`. Total 205,457 rows / 886 events; splits 131,822/578, 24,651/108, 48,984/200. AUPRC 0.024691 / 0.033411 / 0.037735.
- `automated_sensitivities_20261025_000626/fixed_source/`: **credits_final_v2.parquet**, fixed_legacy source; panel `automated_sensitivities_20261025_000626/actor_year_fixed_source.parquet`. Total 251,868 rows / 1,488 events; splits 163,063/1,026, 29,770/172, 59,035/290. AUPRC 0.021741 / 0.031154 / 0.035997.
- `automated_sensitivities_20261025_000626/strict_t1/`: **credits_final_v2.parquet**, dynamic, strict Tier 1 labels; panel `actor_year_strict_t1.parquet`. Total 183,467 rows / 866 events; splits 115,762/534, 22,361/111, 45,344/221. AUPRC 0.026261 / 0.033557 / 0.036059.
- `automated_sensitivities_20261025_000626/two_year_exposure/`: **credits_final_v2.parquet**, dynamic, two-year exposure window; panel `automated_sensitivities_20261025_000626/actor_year_two_year_exposure.parquet`. Total 205,664 rows / 949 events; splits 131,661/611, 24,712/122, 49,291/216. AUPRC 0.022608 / 0.030270 / 0.033313.
- `automated_sensitivities_20261025_000626/unknown_counted/`: **credits_final_v2.parquet**, dynamic, unknown-counted denominator; panel `automated_sensitivities_20261025_000626/actor_year_unknown_counted.parquet`. Total 147,962 rows / 785 events; splits 92,828/493, 18,148/101, 36,986/191. AUPRC 0.024149 / 0.030874 / 0.035597.
- `final_v2/`: **credits_final_v2.parquet**, primary dynamic, cumulative exposures; panel `actor_year_final_v2_dynamic.parquet`. Total 205,664 rows / 949 events; splits 131,661/611, 24,712/122, 49,291/216. AUPRC 0.022608 / 0.031414 / 0.035181.
- `ml_ta/`: **credits_final_v2.parquet**, secondary dynamic ml→ta; panel `actor_year_ml_ta.parquet`. Total 9,895 rows / 140 events; splits 6,351/77, 1,171/24, 2,373/39. AUPRC 0.050643 / 0.081756 / 0.080952.

The primary panel matches **all** historical counts: 205,664 rows, 949 events; train 131,661/611, validation 24,712/122, test 49,291/216, with 20 pairs. `preflight_dynamic` agrees, and only ml→ta exceeds 100 events (140). The older directory named `preflight_final` was byte-for-byte identical to `preflight_dynamic` and was removed during cleanup on 2026-10-26. Use `preflight_dynamic` for the primary screening tables.

Primary test ROC-AUC is .800521 / .809886 / .812295; Brier is .004328 / .004314 / .004311. Incremental S→D AUPRC is .003767, with paired actor-bootstrap 95% interval [-.003205, .010998], which includes zero. These are predictive associations. Full-risk-set coefficients and likelihood-ratio tests must not be presented as held-out predictive scores; likelihood-ratio tests are supplementary and do not use clustered test statistics.

### Provenance evidence and limitations

The sensitivity script explicitly maps dataset_final to credits_final, dataset_baseline to credits, and the other sensitivities to final_v2. Its status log records exit code 0 for all six analyses, from strict_t1 through dataset_baseline (completed 2026-10-25 01:21:39). Build logs and saved panels agree with the inventory. The two strict_t1 result sets were byte-for-byte identical across all six files. The redundant top-level `strict_t1/` was removed on 2026-10-26; the sensitivity-run copy beside its logs is retained. Available PowerShell history confirms the final_v2 dynamic preflight command but does not retain the original primary build/analyze commands. Primary attribution is supported by the supplied run history, saved dynamic panel, preflight, and exact historical metrics; existing outputs have no embedded source-dataset hash. No models were refitted for the initial provenance verification; the separately authorized completion below fits the registered permutation replicates.

The reported uploaded CSV counts (205,457 rows and 200 test events) match the repository dataset_final sensitivity: 886 total events, split rows 131,822 / 24,651 / 48,984 and events 578 / 108 / 200. Use that directory only as the credits_final sensitivity. The uploaded copies were not supplied in this session, so their byte identity cannot be verified from counts alone; generic filenames and split.json are insufficient to certify a copied file.

### Historical interruption and completed follow-up

The original sensitivity-script status log ends at START timing_permutation_1000, with no EXIT: that attempt was stopped for runtime. This historical log remains unchanged. The owner subsequently explicitly authorized completion.

The separately authorized timing-permutation run completed on 2026-10-26 with **all 1,000 repetitions**, seed 2026, dynamic source, and the unchanged 2016/2019 split. Observed test AUPRC gain was 0.003767234; 0 of 1,000 null gains were at least this large, giving the registered one-sided Monte Carlo p-value **0.000999** = (1 + 0) / 1001. Null mean gain was -0.005091300; the null 95th percentile was -0.001820029. This is a predictive timing diagnostic, not a causal test.

The original script placed graph connectivity and modeling_summary.csv after permutation; neither ran in that script. Both have now been completed separately. `results/modeling/modeling_summary.csv` consolidates all eight unique completed analyses (24 model rows) with explicit analysis-directory and dataset columns; it was derived from the saved results without refitting them.

Permutation outputs are in `results/modeling/timing_permutation_20261026/`: `timing_permutation.csv`, all 1,000 `timing_permutation_replicates.csv` records, and `validation.json` containing checks, hashes, package versions and settings. The saved primary panel was reused after verifying all historical primary AUPRC scores to absolute tolerance 1e-12. The checkpointed runner produces exactly the same shuffled exposure values as the existing implementation for seed 2027, and its first-replicate AUPRC matches within 1e-12. Model 2 is invariant to S→D shuffling and is cached; each replicate refits Model 3 on train+validation and scores test, matching the existing statistic. Validation-only fits do not enter that statistic and are omitted. Four local processes run independent seeds; results are ordered by repetition. No registered repetitions, grouping, split, features, model settings, or p-value formula were changed.

The unchanged original shuffle took about 181 seconds for one repetition because it repeatedly selected and assigned small DataFrame blocks. The completion runner caches group indices and assigns identical permutations through arrays, avoiding that bottleneck. To inspect or resume this run safely:

```bash
python scripts/run_timing_permutation_checkpointed.py
```

The runner preserves an already completed run after checking its panel hash and repetition count; interrupted runs checkpoint each repetition. Source credits, primary results, and existing sensitivity results were not overwritten.

### Separate graph audit

The graph audit completed separately on 2026-10-26 using the existing CLI on `data/processed/credits_final_v2.parquet`; the new output is `results/modeling/graph_audit_20261026/graph_connectivity_code_audit.csv`. Existing results were not overwritten. Reproduction command:

```bash
cinema-mobility-model graph-audit --credits data/processed/credits_final_v2.parquet \
  --out results/modeling/graph_audit_20261026/graph_connectivity_code_audit.csv
```

Both graphs retain the same 572,755 actors. The all-title graph has 15,863 components; its largest contains 498,316 actors (87.0033%). The clean-resolved graph has 161,653 components; its largest contains 379,231 actors (66.2117%). Removed title ties leave isolates in the node universe. The output column `actors_with_edges` actually counts actors appearing in retained title rows, including singleton casts; do not interpret it as a verified positive-degree count. This is a static connectivity diagnostic, not a causal or temporal-network test.

### Research reporting handoff

Use `final_v2/` for the primary pooled findings and `ml_ta/` for the ≥100-event secondary analysis; use the named sensitivity directories only with their dataset/settings labels. Reverse-direction and next-year lead comparisons are available in each completed folder. Report the incremental S→D uncertainty alongside the completed timing diagnostic, upstream labeling provenance, and absence of comprehensive dubbing screening. The permutation and actor-bootstrap intervals answer different predictive questions; neither establishes causality. No AI-assisted labeling was undertaken here. The pre-existing README/data-provenance account of upstream AI-assisted control-sample work is not erased or represented as a newly authorized labeling step.

Verification: the full repository test suite passed 58/58 (including all 15 focused modeling tests) on 2026-10-26. The initial test attempt had 41 passes and 17 setup errors caused by an inaccessible default temporary folder; rerunning with an explicitly writable temporary folder passed all tests. The core modeling implementation and credit tables were not changed; a separate checkpointed execution script was added for the authorized permutation completion. `git diff --check` passed after final updates.
