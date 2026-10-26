# Start here: modeling results

Paths below are relative to `results/modeling/` in the local project or the modeling research handoff ZIP. Generated results and source data are intentionally excluded from Git; obtain the handoff package to use the saved outputs.

Use these folders:

- **Primary findings:** `final_v2/` (949 events; 216 test events).
- **Primary screening:** `preflight_dynamic/`.
- **Secondary ml→ta analysis:** `ml_ta/` (140 total events).
- **Sensitivities:** `automated_sensitivities_20261025_000626/`. Its six named analysis folders contain strict Tier 1, unknown-counted denominator, two-year exposure, fixed-source, credits_final, and baseline results. Its `logs/` folder records completion and the permutation interruption.
- **Combined analysis summary:** `modeling_summary.csv` (eight analyses, 24 model rows, with dataset labels).
- **Completed timing permutation:** `timing_permutation_20261026/` (all 1,000 repetitions, replicate scores, and provenance checks).
- **Graph audit:** `graph_audit_20261026/graph_connectivity_code_audit.csv`.
- **Full explanations and provenance:** [`modeling.md`](modeling.md).

The actor_year*.parquet files are saved modeling panels; retain them for reproducibility.

## Cleanup completed (2026-10-26)

These removed copies were checked file by file against the retained versions:

- `strict_t1/`: six files duplicated in `automated_sensitivities_20261025_000626/strict_t1/`. Prefer the sensitivity-run copy beside its logs.
- `preflight_final/`: three files duplicated in `preflight_dynamic/`. Prefer the clearly named dynamic copy.

Both duplicate folders were deleted after a fresh byte-for-byte comparison. Disposable pytest and Python caches under src, tests, and scripts were also deleted. No unique data, panels, source code, sensitivity results, or provenance logs were removed.

The separately authorized timing-permutation run completed on 2026-10-26 with **all 1,000 repetitions**, seed 2026, dynamic source, and the unchanged 2016/2019 split. Observed test AUPRC gain was 0.003767234; 0 of 1,000 null gains were at least this large, giving the registered one-sided Monte Carlo p-value **0.000999** = (1 + 0) / 1001. Null mean gain was -0.005091300; the null 95th percentile was -0.001820029. This is a predictive timing diagnostic, not a causal test.

Modeling computations and checks are complete. All 58 repository tests pass. Carry forward the source-data and dubbing limitations in the modeling guide.
