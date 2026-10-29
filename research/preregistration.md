# Preregistration

**Do Collaborator Moves Predict Cross-Language Career Entry? Temporal Network Signals in Indian Cinema**



This document fixes the analysis rules. Results are reported separately in `final_report.md`.

## 1. Research question and hypotheses

**Question.** Among actors established in a source language S, does a prior collaborator's entry into a destination language D improve prediction of the actor's own first entry into D, beyond career history and general access to D?

- **H1.** Adding X→D exposure to a career-only model improves out-of-time prediction of first entry into D.
- **H2.** Adding direction-matched S→D exposure to a model that already contains career history and X→D exposure improves out-of-time prediction further.
- **H0.** Neither exposure improves held-out prediction beyond the career-only model.

The study estimates predictive association. It makes no causal claim.

## 2. Data

- **Primary dataset:** `credits_final_v2.parquet`. **Sensitivity datasets:** `credits_final.parquet` and `credits.parquet`.
- **Study period:** 1980–2024. **Languages:** Hindi, Tamil, Telugu, Malayalam, Kannada (20 ordered pairs).
- **Sources:** IMDb non-commercial datasets (titles, people, principal credits), TMDB (original language), Wikidata (fallback language lookup).

## 3. Language labels

Each title receives one label, resolved in this order:

1. **Tier 1 (T1):** TMDB `/find` by IMDb ID; original language of the best match, movie matches preferred over TV.
2. **Tier 2 (T2):** Wikidata, looked up by IMDb ID (property P345).
3. **Unknown:** titles unresolved after T1 and T2.

IMDb `title.akas` is used only to shortlist titles for lookup and review, not as a label source. A language is never inferred from a title's cast. Each title carries a `label_source` value. Titles flagged by the automatic ambiguity rules are marked as ambiguous and are not treated as individually verified.

Dubbed releases should not count as language entries. Because the primary dataset has no dubbed-release flag, this exclusion is not applied; the omission is a stated limitation.

## 4. Unresolved and ambiguous titles

- Unknown and ambiguous titles remain in the collaboration network as ties (left join, never an inner join).
- They create no language events, give no source-language eligibility evidence, and are excluded from the labelled-credit denominator.

## 5. Eligibility and risk set

An actor is at risk for the S→D pathway in year *t* when, using only information from before *t*:

1. The actor has at least five credits in S across at least three distinct observed calendar years (not necessarily consecutive).
2. At least 70% of prior labelled credits are in S.
3. The actor has no prior credit in D.
4. S is the unique most recent prior resolved language. If the latest prior calendar year has several resolved languages, S is treated as ambiguous and the actor is not at risk that year.

**Source definition (primary, dynamic).** S is recomputed before every observation year. A risk spell ends when S changes or eligibility lapses, and a new spell may start if the actor re-qualifies without having entered D.

**First entry** is the first observed year with a credit labelled D and no earlier D credit. The actor leaves the risk set at first entry or at the end of the study period. Years absent from the data are not treated as zero-credit years and do not count toward the three qualifying years. History before 1980 is unobserved, and actors first seen at the period boundary are marked left-truncated.

## 6. Network and exposure measures

A **collaboration** is co-appearance on a title. Exposures are cumulative counts of co-appearance titles:

- **Career exposure:** all prior co-appearance titles, across languages and including unresolved titles.
- **X→D exposure:** co-appearance titles with collaborators whose first clean entry into D occurred in an earlier year, and whose latest unambiguous resolved language before that entry differed from the focal actor's current S.
- **S→D exposure:** the same, for collaborators whose language before entering D equalled the focal actor's current S.
- **Reverse D→S exposure** (check only): the same construction for collaborators who made the reverse transition.

**Timing rule.** Only titles strictly after the collaborator's entry year and strictly before the observation year *t* count. Transition-year ties are excluded because order within a calendar year is unobservable. Every predictor uses only information available before *t*.

## 7. Models

All three are discrete-time hazard models with career history, duration at risk, calendar year and pair fixed effects. The primary analysis pools all 20 ordered pairs, with standard errors clustered by actor.

| Model | Terms |
|---|---|
| 1. Career only | Career history, duration, calendar year, pair fixed effects |
| 2. Career + X→D | Model 1 plus cumulative X→D exposure |
| 3. Career + X→D + S→D | Model 2 plus cumulative S→D exposure |

## 8. Split and pair selection

- **Chronological split:** train through 2016, validation 2017–2019, test 2020–2024, identical for all models. Boundaries are fixed from model-free coverage and event counts, never from model scores.
- **Pair screen:** a pair is flagged as selected when it has at least 10 first-entry events and at least 100 at-risk actor-years. The flag does not remove any pair from the pooled primary model.
- **Secondary per-pair analysis:** limited to pairs with at least 100 first-entry events. The threshold is not changed after any model result is seen.
- Pair selection is independent of model performance.

## 9. Evaluation and inference

- **Primary metric:** test-set AUPRC (events are rare). **Secondary metrics:** ROC-AUC and Brier score.
- **Uncertainty:** actor-level bootstrap, 1,000 repetitions, seed 2026. Sampled actors keep all of their test rows, and predictions are held fixed, so intervals are conditional on the fitted models.
- **Supplementary:** likelihood-ratio tests between nested models on the full risk set. They are association tests, not held-out evaluation.

## 10. Decision rules

- **H1 is supported** if the 95% bootstrap interval for the test AUPRC gain of Model 2 over Model 1 excludes zero.
- **H2 is supported** if the 95% bootstrap interval for the gain of Model 3 over Model 2 excludes zero. If the interval includes zero, the held-out evidence for H2 is reported as uncertain, whatever the likelihood-ratio test or permutation result shows.
- All results are interpreted as predictive association.

## 11. Sensitivity and diagnostic analyses

Each uses the same split and is reported separately from the primary result.

| Analysis | Rule |
|---|---|
| Tier 1 only | Use only T1 labels for language events and eligibility; all titles stay as ties |
| Unknown-credit denominator | Count unknown, non-ambiguous credits in the 70% denominator |
| Two-year exposure window | Count qualifying post-transition ties only in the two calendar years before *t* |
| Legacy fixed source | Fix S once per source language until first entry or study end |
| Alternative datasets | Repeat on `credits_final.parquet` and `credits.parquet` |
| Reverse direction | Replace S→D exposure with D→S exposure, same timing rule |
| Lead-exposure placebo | Count pre-transition ties to collaborators whose first S→D entry falls in year *t*+1 |
| Timing permutation | Keeps the collaboration graph fixed and jointly shuffles the two S→D exposure columns within actor/source/destination/risk-spell groups; 1,000 repetitions, seed 2026; statistic is the test AUPRC gain of Model 3 over Model 2; one-sided p = (1 + number of null gains ≥ observed gain) / (1,000 + 1) |
| Graph audit | Compare connected components and largest-component share for the all-title graph versus the clean resolved-title graph |

The reverse-direction and lead-placebo comparisons are descriptive and carry no separate p-value.

## 12. Known limitations stated in advance

- No comprehensive dubbed-release screening.
- Unknown and ambiguous labels remain after resolution; label coverage varies by year.
- Annual resolution: order of events within a calendar year is unobservable.
- Career histories before 1980 are unavailable, so a first observed entry may not be a first-ever entry.
- The timing permutation keeps the collaboration graph fixed and only shuffles S→D exposure timing within actor/source/destination/risk-spell groups; it is a supplementary predictive diagnostic, not a causal test.
- Bootstrap intervals do not include refitting uncertainty.

## 13. Attribution

IMDb data follow the non-commercial dataset terms. This product uses the TMDB API but is not endorsed or certified by TMDB. Wikidata content is CC0.
