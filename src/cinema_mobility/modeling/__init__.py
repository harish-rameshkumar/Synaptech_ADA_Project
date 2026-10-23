"""Discrete-time hazard modeling package."""
from . import cli, pipeline
from .pipeline import (
    load_credits,
    select_pairs,
    build_actor_year,
    evaluate,
    clustered_coefficients,
    likelihood_ratio_tests,
    run_placebo,
    timing_permutation_test,
    graph_connectivity_audit,
    yearly_event_counts,
    yearly_label_coverage,
)

__all__ = [
    "cli",
    "pipeline",
    "load_credits",
    "select_pairs",
    "build_actor_year",
    "evaluate",
    "clustered_coefficients",
    "likelihood_ratio_tests",
    "run_placebo",
    "timing_permutation_test",
    "graph_connectivity_audit",
    "yearly_event_counts",
    "yearly_label_coverage",
]
