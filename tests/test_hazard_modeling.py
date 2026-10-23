import unittest

import numpy as np
import pandas as pd

from cinema_mobility.modeling.pipeline import (
    build_actor_year,
    evaluate,
    graph_connectivity_audit,
    load_credits,
    permute_timing_features,
    preflight_tables,
    run_reverse_direction_check,
)


def clean_credit(actor, title, year, language, source="T1", ambiguous=False):
    return {"actor_id": actor, "title_id": title, "year": year, "language": language,
            "billing_order": 1, "label_source": source, "is_dubbed": False,
            "is_ambiguous": ambiguous}


class HazardModelingTests(unittest.TestCase):
    def test_unknown_and_ambiguous_titles_stay_as_graph_ties(self):
        rows = []
        for actor in ["a", "b"]:
            for i, year in enumerate([2000, 2001, 2002, 2003, 2004]):
                rows.append(clean_credit(actor, f"{actor}-ta-{i}", year, "ta"))
        rows.extend([
            clean_credit("b", "b-hi", 2006, "hi"),
            clean_credit("a", "unknown-tie", 2005, "unknown", "UNKNOWN"),
            clean_credit("b", "unknown-tie", 2005, "unknown", "UNKNOWN"),
        ])
        credits = load_credits(pd.DataFrame(rows))
        panel = build_actor_year(credits, "ta", "hi")
        self.assertEqual(int(panel.event.sum()), 1)
        graph = graph_connectivity_audit(credits).set_index("graph")
        self.assertEqual(int(graph.loc["all_titles_including_unknown", "components"]), 1)
        self.assertEqual(int(graph.loc["resolved_clean_titles_only", "components"]), 2)

    def test_eligibility_counts_three_distinct_years_not_a_span(self):
        rows = [clean_credit("gapped", f"ta-{i}", y, "ta")
                for i, y in enumerate([2000, 2000, 2001, 2001, 2002])]
        rows.append(clean_credit("gapped", "hi-entry", 2004, "hi"))
        panel = build_actor_year(load_credits(pd.DataFrame(rows)), "ta", "hi")
        self.assertEqual(int(panel.event.sum()), 1)
        self.assertEqual(set(panel.year), {2004})  # absent 2003 is not fabricated as an observed row
        self.assertEqual(set(panel.qualifying_year), {2003})

    def test_risk_duration_restarts_after_source_eligibility_is_interrupted(self):
        rows = []
        # ta qualifies at the start of 2003. Two en credits in 2003 make ta
        # neither the latest language nor 70% of prior labelled credits. Three
        # more ta credits in 2004 restore eligibility at the start of 2005.
        for i, year in enumerate([2000, 2000, 2001, 2002, 2002]):
            rows.append(clean_credit("actor", f"ta-before-{i}", year, "ta"))
        rows.extend([
            clean_credit("actor", "en-2003-a", 2003, "en"),
            clean_credit("actor", "en-2003-b", 2003, "en"),
            clean_credit("actor", "ta-2004-a", 2004, "ta"),
            clean_credit("actor", "ta-2004-b", 2004, "ta"),
            clean_credit("actor", "ta-2004-c", 2004, "ta"),
            clean_credit("other", "unrelated-2005", 2005, "unknown", "UNKNOWN"),
            clean_credit("other", "unrelated-2006", 2006, "unknown", "UNKNOWN"),
            clean_credit("actor", "hi-entry", 2007, "hi"),
        ])
        panel = build_actor_year(load_credits(pd.DataFrame(rows)), "ta", "hi")
        actor_rows = panel[panel.actor_id.eq("actor")].sort_values("year")
        self.assertEqual(actor_rows.qualifying_year.tolist(), [2003, 2005, 2005, 2005])
        self.assertEqual(actor_rows.risk_duration.tolist(), [1, 1, 2, 3])

    def test_ambiguous_credit_is_not_language_evidence_but_remains_a_tie(self):
        rows = [clean_credit("a", f"a-ta-{i}", y, "ta")
                for i, y in enumerate([2000, 2001, 2002, 2003, 2004])]
        rows += [clean_credit("a", "a-amb", 2005, "unknown", "RULE_AMBIGUOUS", True),
                 clean_credit("b", "a-amb", 2005, "unknown", "RULE_AMBIGUOUS", True)]
        credits = load_credits(pd.DataFrame(rows))
        self.assertIn("b", credits.actor_id.unique())
        graph = graph_connectivity_audit(credits).set_index("graph")
        self.assertEqual(int(graph.loc["all_titles_including_unknown", "components"]), 1)

    def test_strict_mode_uses_t1_language_events(self):
        rows = [clean_credit("a", f"ta-{i}", y, "ta")
                for i, y in enumerate([2000, 2001, 2002, 2003, 2004])]
        rows.append(clean_credit("a", "hi-entry", 2006, "hi", "T2"))
        credits = load_credits(pd.DataFrame(rows))
        with self.assertRaisesRegex(ValueError, "No eligible"):
            build_actor_year(credits, "ta", "hi", strict=True)

    def test_prior_destination_exposure_and_one_year_lead_are_distinct(self):
        rows = []
        # The shared title creates a tie before collaborator b's first hi entry.
        for actor in ["a", "b"]:
            for i, year in enumerate([1999, 2000, 2001, 2002]):
                rows.append(clean_credit(actor, f"{actor}-ta-{i}", year, "ta"))
        rows.extend([
            clean_credit("a", "shared-ta", 2003, "ta"),
            clean_credit("b", "shared-ta", 2003, "ta"),
            # The study has observed titles in 2004, though focal actor a has none.
            clean_credit("c", "unrelated-2004", 2004, "unknown", "UNKNOWN"),
            clean_credit("c", "unrelated-2006", 2006, "unknown", "UNKNOWN"),
            clean_credit("b", "b-hi", 2005, "hi"),
            clean_credit("a", "a-hi", 2008, "hi"),
        ])
        panel = build_actor_year(load_credits(pd.DataFrame(rows)), "ta", "hi")
        focal = panel[panel.actor_id.eq("a")].set_index("year")
        # Entry in the same year is not prior exposure, but is a valid t+1 lead
        # for the preceding year. It becomes S→D exposure from the next year.
        self.assertEqual(int(focal.loc[2004, "sd_collaborators"]), 0)
        self.assertGreaterEqual(int(focal.loc[2004, "lead_sd_collaborators"]), 1)
        self.assertGreaterEqual(int(focal.loc[2006, "sd_collaborators"]), 1)

    def test_reverse_direction_feature_uses_prior_D_to_S_collaborator_move(self):
        rows = []
        for i, year in enumerate([2000, 2001, 2002, 2003, 2004]):
            rows.append(clean_credit("a", f"a-ta-{i}", year, "ta"))
        for i, year in enumerate([1999, 2000, 2001, 2002, 2003]):
            rows.append(clean_credit("b", f"b-hi-{i}", year, "hi"))
        rows.extend([
            clean_credit("a", "shared-unknown", 2004, "unknown", "UNKNOWN"),
            clean_credit("b", "shared-unknown", 2004, "unknown", "UNKNOWN"),
            clean_credit("b", "b-ta-entry", 2006, "ta"),
            clean_credit("c", "unrelated-2007", 2007, "unknown", "UNKNOWN"),
            clean_credit("a", "a-hi-entry", 2008, "hi"),
        ])
        panel = build_actor_year(load_credits(pd.DataFrame(rows)), "ta", "hi")
        focal = panel[(panel.actor_id == "a") & (panel.year == 2007)].iloc[0]
        self.assertEqual(int(focal.reverse_sd_collaborators), 1)
        self.assertGreaterEqual(int(focal.reverse_sd_collaborations), 1)

    def test_timing_permutation_preserves_actor_path_exposure_totals(self):
        panel = pd.DataFrame({
            "actor_id": ["a"] * 4,
            "source": ["ta"] * 4,
            "destination": ["hi"] * 4,
            "year": [2000, 2001, 2002, 2003],
            "event": [0, 0, 0, 1],
            "sd_collaborations": [0, 1, 3, 3],
            "sd_collaborators": [0, 1, 2, 2],
            "career_exposure": [0, 2, 4, 6],
        })
        permuted = permute_timing_features(panel, seed=3)
        self.assertEqual(permuted.sd_collaborations.sum(), panel.sd_collaborations.sum())
        self.assertEqual(permuted.sd_collaborators.sum(), panel.sd_collaborators.sum())
        self.assertEqual(permuted.career_exposure.tolist(), panel.career_exposure.tolist())

    def test_preflight_counts_risk_rows_without_network_feature_builder(self):
        rows = [clean_credit("a", f"ta-{i}", y, "ta")
                for i, y in enumerate([2000, 2001, 2002, 2003, 2004])]
        rows.append(clean_credit("b", "unrelated-2005", 2005, "unknown", "UNKNOWN"))
        rows.append(clean_credit("a", "hi-entry", 2006, "hi"))
        credits = load_credits(pd.DataFrame(rows))
        pairs, events_by_year, coverage = preflight_tables(credits)
        pair = pairs[(pairs.source == "ta") & (pairs.destination == "hi")].iloc[0]
        self.assertEqual(int(pair.events), 1)
        self.assertEqual(int(pair.at_risk_actor_years), 2)
        self.assertEqual(int(events_by_year.first_entry_events.sum()), 1)
        self.assertFalse(coverage.empty)

    def test_three_models_and_actor_clustered_paired_intervals(self):
        rows = []
        rng = np.random.default_rng(8)
        for i in range(36):
            actor = f"actor-{i:02d}"
            pair = "ta->hi" if i % 2 == 0 else "te->hi"
            source, destination = pair.split("->")
            event_year = {0: 2005, 1: 2010, 2: 2015}.get(i % 4)
            last = event_year if event_year is not None else 2018
            for year in range(2000, last + 1):
                rows.append({
                    "actor_id": actor, "source": source, "destination": destination,
                    "pair": pair, "year": year, "event": int(year == event_year),
                    "risk_duration": year - 2000 + 1, "career_age": year - 1995,
                    "career_exposure": max(0, year - 2000), "source_credits": 5 + year - 2000,
                    "other_languages": 0, "annual_source_credits": int(year % 2 == 0),
                    "xd_collaborations": int(rng.integers(0, 4)),
                    "xd_collaborators": int(rng.integers(0, 3)),
                    "sd_collaborations": int(rng.integers(0, 4)),
                    "sd_collaborators": int(rng.integers(0, 3)),
                    "reverse_sd_collaborations": int(rng.integers(0, 4)),
                    "reverse_sd_collaborators": int(rng.integers(0, 3)),
                    "lead_sd_collaborations": int(rng.integers(0, 3)),
                    "lead_sd_collaborators": int(rng.integers(0, 2)),
                })
        panel = pd.DataFrame(rows)
        scores, split = evaluate(panel, train_end_year=2007, validation_end_year=2012,
                                 bootstrap_reps=20, seed=19)
        self.assertEqual(list(scores.model), ["career_only", "career_plus_XD", "career_plus_XD_SD"])
        self.assertEqual(split["bootstrap_unit"], "actor cluster")
        for column in ["test_AUPRC_CI_low", "test_ROC_AUC_CI_high", "test_Brier_CI_low",
                       "delta_XD_minus_career_AUPRC_CI_low", "delta_SD_increment_Brier_CI_high"]:
            self.assertIn(column, scores.columns)
        reverse = run_reverse_direction_check(panel, train_end_year=2007,
                                              validation_end_year=2012,
                                              bootstrap_reps=20, seed=19)
        self.assertEqual(list(reverse.model), ["career_plus_XD", "career_plus_XD_SD",
                                               "career_plus_XD_reverse_DS"])
        self.assertIn("delta_vs_XD_AUPRC_CI_low", reverse.columns)


if __name__ == "__main__":
    unittest.main()
