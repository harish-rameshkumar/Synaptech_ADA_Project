# Do Collaborator Moves Predict Cross-Language Career Entry?

A reproducible research pipeline for studying whether collaborators' prior cross-language career entries predict an actor's first clean entry into another Indian film language. The estimand is **predictive association**, not causal influence.

## What is implemented

- IMDb principal-cast ingestion for feature films and TV movies, restricted to top 10 billed actors.
- Tiered original-language labels in `label_source`: **T1** TMDB `/find/{imdb_id}` (movie match preferred over TV), **T2** Wikidata by IMDb ID (P345) original language (P364), else **unknown**. Every lookup is cached in SQLite so reruns resume (`TMDB_API_KEY` in `.env`). `title.akas` is *not* a labelling tier (its original-title rows carry no language); it is used only to shortlist candidate titles (region `IN` or an hi/ta/te/ml/kn aka) and to flag likely dubbed/ambiguous titles. Cast is never used to infer language. Titles with unknown language remain graph ties but are excluded from language events. The final-v2 review list is not a completed manual review log; see [data provenance](docs/data_provenance.md).
- Resolved original-language labels create language events; unknown and rule-ambiguous titles remain graph ties but do not create events.
- Actor-language risk sets using the one-pager's thresholds: at least five principal credits in the source language across at least three *distinct calendar years* (a count of distinct years, not a max-minus-min span, and the years need not be consecutive), at least 70% of observed prior credits in that source language, and no prior destination credit.
- Cumulative prior collaboration, X→D, and S→D exposure features; same-year transitions do not count as prior exposure.
- Three pooled discrete-time hazard models, pair fixed effects, actor-clustered coefficient inference, actor-bootstrap test-metric intervals, pre-model temporal coverage tables, timing permutation, and a network lead placebo.

## Pooled hazard models

The integrated modeling implementation is in `src/cinema_mobility/modeling/`. Run `cinema-mobility-model preflight` first to create event-by-year and label-coverage tables. Choose and record the train/validation/test boundaries from those tables before fitting models. The `analyze` command requires both boundary years and has no default split. Full commands and sensitivity steps are in [`docs/modeling.md`](docs/modeling.md).

## Data and language review

1. Obtain the IMDb non-commercial TSV datasets (`title.basics.tsv.gz`, `title.principals.tsv.gz`). Respect IMDb's non-commercial terms and use the required attribution in [`docs/data_provenance.md`](docs/data_provenance.md).
2. Prepare `data/raw/language_labels.csv` with columns:
   - `tconst`: IMDb title ID
   - `language`: reviewed ISO-639-1 language code (e.g. `hi`, `ta`, `te`, `ml`, `kn`, `bn`, `mr`, `pa`)
   - `is_dubbed`: true when the release is dubbed into that language rather than a new-language career credit
   - `is_ambiguous`: true for uncertain or genuinely multilingual language assignment
3. Use the T1 (TMDB) / T2 (Wikidata) labels as the initial label (`cinema-mobility resolve-labels [--end-year Y] [--sample N]`), then review dubbed and ambiguous cases manually. TMDB labels alone do not reliably identify every language present in a film. Keep decisions in the CSV so exclusions are auditable.

The optional TMDB resolver can be run from Python after IDs are available:

```python
from cinema_mobility.ingest import tmdb_enrich
# IDs are IMDb tconst strings, for example tt1234567
tmdb_enrich(imdb_title_ids, "data/raw/tmdb_matches.csv")
```

## Building the credits table and quality outputs

```bash
cinema-mobility resolve-labels [--end-year Y]      # T1 TMDB, T2 Wikidata; cached in data/raw/label_cache.sqlite, resumable
cinema-mobility build-credits [--end-year 2024] [--focal-languages hi,ta,te,ml,kn]       # data/processed/credits.parquet (left join)
cinema-mobility quality-report [--end-year 2024] [--final data/processed/credits_final_v2.parquet]      # --final defaults to credits_final_v2.parquet; results/data_quality.csv + data/raw/language_labels_review.csv
cinema-mobility make-sample                        # data/processed/credits_sample.parquet (~5,000 credits)
```

- `credits.parquet` keeps (a) titles labeled with a focal language, (b) titles resolved to **other** languages, kept with their label so the source-share denominator and other-language features are correct, and (c) candidate titles whose language is unknown (`language` null, `label_source = unknown`). Unknown titles are collaboration ties only: they never create language events and, by default, are excluded from the 70% share denominator (`qualifying_years(..., count_unknown=True)` and the report's `eligibility_sensitivity` section show the effect of counting them). Only the **focal languages** (`--focal-languages`, default hi,ta,te,ml,kn) can be sources or destinations in risk sets and the pair screen; other resolved languages never are. Titles never sent to TMDB (no IN-region or focal-language aka) and titles after `--end-year` (we use 2024) are dropped; a 2,000-title recall check found 0.05% target-language titles outside the candidate rule.
- `results/data_quality.csv` is a long table (`section`, `key1`, `key2`, counts, `share`, `note`) with titles dropped per stage, match rate per tier overall / by language / by year, unknown concentration flags, per-tier actor and tie contribution, merged/orphan ID evidence and graph connectivity.
- `language_labels_review.csv` is a 5,672-title review shortlist, not a completed review log; blank `is_dubbed` / `is_ambiguous` fields are expected. Final-v2 ambiguity is rule-based. Only two control-sample label corrections were applied; see [data provenance](docs/data_provenance.md).
- The sample is built from whole title casts around eligible actors so ties survive. It holds ids, years, billing and labels only. IMDb data remains subject to IMDb's non-commercial license, so share it only within the project team.

## Manual review workflow

```bash
cinema-mobility review-packet                     # data/review/packet/: review_A/B/C.csv, review_control.csv, packet_key.csv, CODEBOOK.md
# reviewers fill is_dubbed / is_ambiguous (1/0) per docs/review_codebook.md and return <name>_reviewed.csv
cinema-mobility review-agreement --reviewed A_reviewed.csv B_reviewed.csv C_reviewed.csv --control control_reviewed.csv
cinema-mobility apply-review --reviewed A_reviewed.csv B_reviewed.csv C_reviewed.csv [--control file] [--excluded-as ties|drop]
```

- The top 1,500 titles by impact are dealt round-robin into three files of 500. A shared overlap set of 100 titles (random, drawn from below the top block) is added to every file, unmarked, so `review-agreement` can report Fleiss' kappa (plus pairwise Cohen's) for `is_dubbed`, `is_ambiguous` and the combined exclude decision. 200 more random titles from the rest form the control file; the share of them a reviewer would exclude estimates the error rate left in the ~3,900 unreviewed titles (Wilson 95% interval).
- `apply-review` takes a strict majority per field across reviewers; a split vote is a conflict and is excluded as ambiguous. An optional `corrected_language` replaces the label (`label_source = reviewed`). Excluded titles stay in `credits.parquet` as collaboration ties with no language (`label_source = review_excluded`); `--excluded-as drop` removes their credits instead. It rebuilds `credits.parquet` from the original labels (so it can be rerun as reviews come in), keeps the pre-review table in `data/processed/credits_pre_review.parquet`, prints how many titles and credits changed, refuses to write to any reviewed file and verifies their checksums afterwards.

## Predefined ambiguity rules (sensitivity switches)

Three rules flag focal-language titles whose single language label may mislead. They use only title-level evidence (never the cast):

| rule (`--ambiguity-rules`) | fires when |
|---|---|
| `spoken` (a) | TMDB `spoken_languages` contains two or more of hi, ta, te, ml, kn |
| `wikidata` (b) | Wikidata lists two or more original languages (P364) |
| `spoken_wiki` (a2) | rule (a) **and** the English Wikipedia film infobox (article found via its IMDb link) also lists two or more focal languages; no IMDb-matched article means not flagged |
| `wikidata_focal` (b-focal, `bf`) | Wikidata lists two or more of hi, ta, te, ml, kn as original languages (en and other non-focal languages ignored) |
| `disagree` (c) | TMDB `original_language` is not among Wikidata's original languages (both present) |

```bash
cinema-mobility rules-fetch [--only FILE | --limit N] [--wikipedia]     # --wikipedia adds the infobox lookups for rule a2 (only titles flagged by rule a; ~1 request per 1.5 s, about 70 min for 448 titles)
# cached, resumable: ~16k TMDB detail calls + ~190 Wikidata batches for the full table
cinema-mobility rules-report --stage control               # precision/recall of the rules against the control file's ambiguity flags
cinema-mobility rules-report --stage full                  # titles/credits flagged per rule + all 8 on/off combinations -> results/ambiguity_rules_sensitivity.csv, results/ambiguity_rules_pairs.csv
cinema-mobility build-credits --ambiguity-rules none|all|spoken,spoken_wiki,wikidata,wikidata_focal,disagree      # also on apply-review
```

Flagged titles **stay in `credits.parquet` as collaboration ties with no language** (`label_source = rule_ambiguous`), so they never create language events and, by default, drop out of the 70% share denominator. The default is `none`: nothing changes until a rule set is chosen, and `rules-report --stage full` reports every combination for the sensitivity analysis. For the frozen final-v2 dataset, the 200-title control sample was pre-filled with AI assistance, 29 rows received a blinded second-model cross-check, and all six disagreements were human-adjudicated. These steps did not constitute a full review of the 5,672-title shortlist. The TA's decision on AI-assisted labeling with rule-based exclusion is pending.

Single corrections from a pre-fill can be applied through the normal review path: put them in a CSV with `tconst, is_dubbed, is_ambiguous, corrected_language` and run `apply-review --reviewed that.csv` (this is how the two control corrections, Danny Jigar to `gu` and Kanulu Therichinaa Kanulu Moosinaa to `te`, were applied).

## Data versions (pick by name)

`data/processed/data_versions.csv` (`cinema-mobility data-versions`) lists each table with its rules, row counts and sha256.

| version | file | rules | use |
|---|---|---|---|
| `final` | `credits_final.parquet` | `spoken` + `wikidata_focal` + `disagree`, plus the 2 control corrections | sensitivity: broader ambiguity rule |
| `final_v2` | `credits_final_v2.parquet` | `spoken_wiki` (a2) + `wikidata_focal` + `disagree`, plus the 2 corrections | **primary** |
| `baseline` | `credits.parquet` | none (includes the 2 control corrections) | sensitivity: no rules |
| `pre_review` | `credits_pre_review.parquet` | none, no corrections | audit |
| `sample_final_v2` | `credits_final_v2_sample.parquet` | as `final_v2` | development (~5,000 credits) |
| `sample_final` | `credits_final_sample.parquet` | as `final` | development (~5,000 credits) |
| `sample_baseline` | `credits_sample.parquet` | none, older | older development sample |

All versions have the same credits, titles and actors (they differ only in the `language` / `label_source` of flagged titles), so results are directly comparable. `credits_final` was built with
`apply-review --reviewed data/review/corrections_control.csv --credits data/processed/credits_final.parquet --baseline data/processed/credits_pre_review.parquet --ambiguity-rules spoken,wikidata_focal,disagree`; `credits.parquet` is never modified by it. Flagged titles carry `label_source = rule_ambiguous` and `language` null; run any pipeline command on either file with `--credits`.

`docs/data_quality_final_v2.csv` reports final-v2-only figures, including match rates by tier, year and language. It supersedes the older `data_quality.csv`. The corrected six-page data section is [`docs/data_section.pdf`](docs/data_section.pdf), and title-ID limitations are in [`docs/id_checks.md`](docs/id_checks.md).

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

cinema-mobility-model preflight --credits data/processed/credits_final_v2.parquet \
  --outdir results/modeling/preflight
# Lock the train and validation end years from these model-free tables first.
cinema-mobility-model build --credits data/processed/credits_final_v2.parquet \
  --out results/modeling/actor_year_final_v2.parquet
cinema-mobility-model analyze \
  --actor-years results/modeling/actor_year_final_v2.parquet \
  --train-end-year <locked-year> --validation-end-year <locked-year> \
  --bootstrap-reps 1000 --outdir results/modeling/final_v2
```

For the pooled all-pairs model, omit `--source` and `--destination`. Do not choose a split or pairs from model-performance results. The fixed primary plan pools all 20 ordered pairs; the five secondary pairs are preselected by their event-count threshold.

## Outputs

- `data/processed/credits.parquet`: clean title-actor-language credits
- `results/modeling/preflight/pair_screen.csv`: pre-model pair screen
- `results/modeling/preflight/events_by_year.csv`: first-entry events and at-risk counts by year
- `results/modeling/preflight/label_coverage_by_year.csv`: unknown and resolved label coverage by year
- `results/modeling/final_v2/model_comparison.csv`: three-model validation/test comparison and paired intervals
- `results/modeling/final_v2/placebo_comparison.csv`: network lead placebo
- `results/modeling/final_v2/split.json`: exact training/validation/test time ranges

## Current limitations / next research steps

This is a first executable pipeline, not yet an empirical result. Validate the exact actor eligibility and career-history definitions against the course proposal, audit IMDb/TMDB ID matches, manually review language/dubbing labels, and document exclusions. The current collaboration feature construction is an interpretable first version; sensitivity analyses still needed include a stricter two-year lag, actor-level constrained timing permutation, and comparison of both S→D and D→S directions. The pair screen reports event counts, eligible actors and approximate at-risk actor-years; review those support levels and record thresholds before model fitting. Results must not be described causally.
