# Language-Pair Selection Rules

## 1. Purpose

This document defines the language-pair selection plan for the study *Do Collaborator Moves Predict Cross-Language Career Entry? Temporal Network Signals in Indian Cinema.*

Pair selection is based on observed transition counts, eligibility, career-history coverage, and data quality. It is independent of model-performance results.

The document distinguishes pair-level screening from inclusion in the pooled primary analysis. A pair that does not meet the screening thresholds is not automatically excluded from the pooled analysis.

## 2. Screening Criteria

For each candidate ordered source-to-destination (S→D) language pair, the screening records:

- Number of observed first-entry S→D transition events.
- Number of eligible actors or actor-language observations.
- Number of at-risk actor-year observations.
- Language-label coverage and relevant data-quality limitations.
- Availability of sufficient career history for temporal analysis.
- Pair-level screening status under the specified thresholds.

Pair-specific counts are taken from the screening output and relevant data-quality reports. Missing pair-specific counts are not inferred from aggregate totals.

The default screening thresholds are at least 10 observed transition events and at least 100 at-risk actor-years. These thresholds determine the pair-level screening flag; they do not determine inclusion in the pooled primary analysis.

## 3. Confirmed Analysis Plan

Pair selection is based on transition counts, eligibility, career-history coverage, and data quality, not model-performance results.

### 3.1 Primary analysis

The primary analysis includes all 20 ordered language pairs in one pooled model, with pair fixed effects and standard errors clustered by actor.

The primary dataset is `credits_final_v2.parquet`, using the dynamic-source eligibility definition. The study period is 1980–2024.

The completed primary actor-year panel contains:

- **At-risk actor-year rows:** 205,664
- **Observed first-entry events:** 949

The chronological data split is:

- **Training:** through 2016, with 611 events.
- **Validation:** 2017–2019, with 122 events.
- **Testing:** 2020–2024, with 216 events.

All 20 ordered pairs remain in the pooled primary analysis, regardless of their individual screening status.

### 3.2 Screening results

Under the default screening thresholds of at least 10 transition events and at least 100 at-risk actor-years, the current screening output marks 19 pairs as selected and `te→ml` as unselected.

This screening status does not determine inclusion in the pooled primary analysis. The unselected pair remains part of the pooled model.

The screening result is reported separately from model fitting and predictive-performance evaluation.

### 3.3 Secondary per-pair analyses

Secondary per-pair analyses use the threshold of at least 100 observed transition events.

In the current screening output, only Malayalam→Tamil (`ml→ta`) meets this threshold, with 140 events.

Secondary-analysis selection is based on verified transition counts and the same threshold, independently of model-performance results. The threshold is not changed in response to validation or test performance.

## 4. Data Version and Sensitivity Analyses

### 4.1 Dataset versions

- **Primary dataset:** `credits_final_v2.parquet`
- **Dataset sensitivity analyses:** `credits_final.parquet` and `credits.parquet`

The alternative datasets are used for sensitivity analyses and do not replace the primary dataset in the main analysis.

### 4.2 Eligibility definition

The primary eligibility rule requires:

1. At least five source-language credits before the observation year.
2. Those credits span at least three distinct calendar years; the years need not be consecutive.
3. At least 70% of prior labelled credits are in the source language.
4. No prior observed credit in the destination language before the observation year.

Unknown and rule-ambiguous credits are excluded from the primary labelled-credit denominator.

Source language is reassessed before each observation year. A risk spell ends if the actor's source language changes or eligibility is interrupted. Eligibility may restart if the actor later qualifies for the same pathway, provided the actor has not previously entered the destination language.

The primary analysis uses dynamic-source eligibility. The legacy fixed-source definition may be evaluated as a sensitivity analysis.

### 4.3 Unknown-language denominator sensitivity

The relevant sensitivity analysis repeats the eligibility calculation with unknown-language credits included in the source-language-share denominator. This tests whether the eligibility and transition results are sensitive to the treatment of unknown credits.

This sensitivity analysis does not change the primary denominator rule.

### 4.4 Other sensitivity and diagnostic analyses

The broader analysis plan and the completed analyses also include alternative dataset versions, Tier 1-only labels, a two-year exposure window, the legacy fixed-source definition, reverse-direction comparisons, a lead-placebo comparison, and a timing-permutation diagnostic.

Each analysis is reported as it was actually implemented. A sensitivity check is described as completed only if it was run and its results are available.

### 4.5 Dubbed-release limitation

The intended rule is to exclude confirmed dubbed-release credits from language-entry events and source-language eligibility calculations while retaining them as collaboration ties.

However, the primary dataset contains no dedicated, comprehensive dubbed-release flag, and this exclusion was not applied comprehensively in the completed primary analysis. Potential misclassification of dubbed IMDb entries therefore remains a data limitation.

Unknown and rule-ambiguous credits are handled separately under the primary language-label exclusion rules. Their exclusion from language-event classification does not automatically remove the associated collaboration ties.

The completed primary analysis reports 949 first-entry events using the dynamic-source eligibility definition. These counts describe the completed analysis under that definition and its documented data limitations.

## 5. Pair-Selection Procedure

Pair screening uses transition counts, eligibility, career-history coverage, and data quality.

The screening output provides the basis for reporting counts for all 20 ordered language pairs, alongside relevant data-quality findings, including unknown language labels, rule-ambiguous titles, and the absence of a comprehensive dubbed-release flag.

All 20 ordered pairs are retained in the pooled primary analysis, with pair fixed effects and standard errors clustered by actor. Secondary per-pair analyses are limited to pairs with at least 100 observed transition events. Under the current screening results, only `ml→ta` qualifies, with 140 events.

Pair-specific event counts, eligible-actor counts, at-risk actor-years, screening flags, and relevant data-quality limitations are reported using the available screening output. Missing pair-specific counts are not reconstructed from aggregate totals.

Where the documented eligibility or exposure-timing rules differ from the completed implementation, the difference is described as a methodological deviation or limitation. Claims about implementation are restricted to what the code and analysis outputs support.

Pair selection remains independent of model-performance results.

## 6. Missing and Partially Observed Career Histories

Missing calendar years are not treated as years with zero credits and do not count toward the three distinct qualifying credit years.

Career histories before 1980 are unobserved or incompletely observed, limiting the assessment of prior experience and first-entry events.

These restrictions affect the interpretation of eligibility and first-entry events. Results describe transitions observed within the available data and do not establish that an actor had no relevant career activity before the observation period.

## 7. Leakage Prevention

Language-pair selection is based on transition counts, eligibility, career-history coverage, and data quality, not model accuracy or other model-performance results.

The pooled primary analysis includes all 20 ordered pairs, while secondary per-pair analyses use the threshold of at least 100 observed transition events.

Predictors for each actor-year must use information available before the focal observation year. Collaborator exposures (X→D, S→D, reverse D→S) count only co-appearance titles strictly after the collaborator's first clean entry into the destination language and strictly before the focal observation year; ties in the transition year are excluded because within-year order is unobservable. The full definitions and deviations are in `preregistration.md`.

This separation prevents model-performance results from influencing which language pairs are selected for analysis. It also distinguishes pair-selection rules from temporal restrictions applied when constructing the predictor variables.

## 8. Documentation and Reproducibility

The analysis record includes the pair-screening output, transition counts, eligible-actor counts, at-risk actor-years, selection flags, dataset versions, and relevant data-quality caveats. Earlier screening outputs are retained to support comparison and reproducibility.

The primary analysis uses a pooled model covering all 20 ordered language pairs. Secondary per-pair analyses are limited to pairs with at least 100 observed transition events; under the current screening results, only Malayalam→Tamil (`ml→ta`) qualifies, with 140 events.

The primary panel contains 205,664 at-risk actor-year rows and 949 first-entry events. The chronological split contains 611 training events through 2016, 122 validation events in 2017–2019, and 216 test events in 2020–2024.

Differences between screening outputs, dataset versions, and the completed analysis are documented where they affect the reported counts or interpretation. Methodological deviations and data limitations are stated explicitly. Interpretations are limited to what the available data, code, and analysis results support.