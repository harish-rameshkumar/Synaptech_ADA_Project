# Do Collaborator Moves Predict Cross-Language Career Entry?

A reproducible research pipeline for studying whether recent collaborator transitions predict an actor's first clean entry into another Indian film language. The estimand is **predictive association**, not causal influence.

## What is implemented

- IMDb principal-cast ingestion for feature films and TV movies, restricted to top 10 billed actors.
- TMDB `/find/{imdb_id}` language lookup with a local SQLite cache (`TMDB_API_KEY` required); metadata remains subject to manual review.
- Reviewed language labels can exclude dubbed releases and ambiguous bilingual/multilingual titles.
- Actor-language risk sets using the one-pager's thresholds: at least five principal credits in the source language across at least three *distinct calendar years* (a count of distinct years, not a max-minus-min span, and the years need not be consecutive), at least 70% of observed prior credits in that source language, and no prior destination credit.
- Time-varying collaboration ties and lagged 1-3 year exposure, with a stricter two-year-lag sensitivity option; a collaborator must have had an existing tie with the actor before their own first destination-language credit. Same-year moves do not count as exposure.
- Career-only vs career-plus-network discrete-time logistic models, chronological holdout, AUPRC / ROC-AUC / Brier score, and a lead-exposure placebo.

## Data and language review

1. Obtain the IMDb non-commercial TSV datasets (`title.basics.tsv.gz`, `title.principals.tsv.gz`). Respect IMDb's data license and terms.
2. Prepare `data/raw/language_labels.csv` with columns:
   - `tconst`: IMDb title ID
   - `language`: reviewed ISO-639-1 language code (e.g. `hi`, `ta`, `te`, `ml`, `kn`, `bn`, `mr`, `pa`)
   - `is_dubbed`: true when the release is dubbed into that language rather than a new-language career credit
   - `is_ambiguous`: true for uncertain or genuinely multilingual language assignment
3. Use TMDB original-language metadata as the initial label, then review ambiguous cases using Wikidata/film documentation. TMDB labels alone do not reliably identify every language present in a film. Keep decisions in the CSV so exclusions are auditable.

The optional TMDB resolver can be run from Python after IDs are available:

```python
from cinema_mobility.ingest import tmdb_enrich
# IDs are IMDb tconst strings, for example tt1234567
tmdb_enrich(imdb_title_ids, "data/raw/tmdb_matches.csv")
```

## Setup and run

```bash
cd cinema_mobility
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -e .

cinema-mobility ingest \
  --principals /path/to/title.principals.tsv.gz \
  --basics /path/to/title.basics.tsv.gz \
  --languages data/raw/language_labels.csv

cinema-mobility select-pairs --min-events 10 --min-risk-years 100
# Review results/pair_screen.csv before modeling and pre-register chosen pairs.
cinema-mobility build --source ta --destination hi
# Sensitivity: require at least a 2-year collaborator lead
cinema-mobility build --source ta --destination hi --min-exposure-lag 2 --out data/processed/actor_year_lag2.parquet
cinema-mobility analyze --split-year 2018
```

For the pooled all-pairs model, omit `--source` and `--destination`. Choose the chronological split after inspecting year/event coverage, and record it before fitting. Do not select pairs based on model results. If the thresholded pair list is too sparse, use the pre-specified pooled model and report support by pair.

## Outputs

- `data/processed/credits.parquet`: clean title-actor-language credits
- `results/pair_screen.csv`: pre-model pair event and at-risk actor-year support screen
- `data/processed/actor_year.parquet`: discrete-time actor-pair-year risk set and features
- `results/model_comparison.csv`: chronological baseline/network comparison
- `results/placebo_comparison.csv`: lead-exposure falsification comparison
- `results/split.json`: exact training/test time ranges

## Current limitations / next research steps

This is a first executable pipeline, not yet an empirical result. Validate the exact actor eligibility and career-history definitions against the course proposal, audit IMDb/TMDB ID matches, manually review language/dubbing labels, and document exclusions. The current collaboration feature construction is an interpretable first version; sensitivity analyses still needed include a stricter two-year lag, actor-level constrained timing permutation, and comparison of both S→D and D→S directions. The pair screen reports event counts, eligible actors and approximate at-risk actor-years; review those support levels and record thresholds before model fitting. Results must not be described causally.
