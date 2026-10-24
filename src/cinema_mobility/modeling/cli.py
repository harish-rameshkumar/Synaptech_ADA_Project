from __future__ import annotations
import argparse
from pathlib import Path
import pandas as pd
from .pipeline import (load_credits, select_pairs, build_actor_year, evaluate, clustered_coefficients,
                       likelihood_ratio_tests, run_placebo, timing_permutation_test, graph_connectivity_audit,
                       preflight_tables, run_reverse_direction_check)


def main():
    p = argparse.ArgumentParser(prog="cinema-mobility-model")
    sub = p.add_subparsers(dest="command", required=True)
    screen = sub.add_parser("pair-screen")
    screen.add_argument("--credits", required=True)
    screen.add_argument("--min-events", type=int, default=10)
    screen.add_argument("--min-risk-years", type=int, default=100)
    screen.add_argument("--strict", action="store_true")
    screen.add_argument("--count-unknown-in-share", action="store_true")
    screen.add_argument("--out", default="results/pair_screen.csv")
    preflight = sub.add_parser("preflight", help="Write model-free pair, event-year, and label-coverage tables")
    preflight.add_argument("--credits", required=True)
    preflight.add_argument("--min-events", type=int, default=10)
    preflight.add_argument("--min-risk-years", type=int, default=100)
    preflight.add_argument("--strict", action="store_true")
    preflight.add_argument("--count-unknown-in-share", action="store_true")
    preflight.add_argument("--outdir", default="results/modeling/preflight")
    build = sub.add_parser("build")
    build.add_argument("--credits", required=True)
    build.add_argument("--source")
    build.add_argument("--destination")
    build.add_argument("--exposure-lookback-years", type=int)
    build.add_argument("--strict", action="store_true")
    build.add_argument("--count-unknown-in-share", action="store_true")
    build.add_argument("--out", required=True)
    analyze = sub.add_parser("analyze")
    analyze.add_argument("--actor-years", required=True)
    analyze.add_argument("--train-end-year", type=int, required=True)
    analyze.add_argument("--validation-end-year", type=int, required=True)
    analyze.add_argument("--bootstrap-reps", type=int, default=1000)
    analyze.add_argument("--outdir", default="results")
    perm = sub.add_parser("permutation")
    perm.add_argument("--credits", required=True)
    perm.add_argument("--source")
    perm.add_argument("--destination")
    perm.add_argument("--train-end-year", type=int, required=True)
    perm.add_argument("--validation-end-year", type=int, required=True)
    perm.add_argument("--reps", type=int, default=1000)
    perm.add_argument("--seed", type=int, default=2026)
    perm.add_argument("--strict", action="store_true")
    perm.add_argument("--out", default="results/timing_permutation.csv")
    graph = sub.add_parser("graph-audit")
    graph.add_argument("--credits", required=True)
    graph.add_argument("--out", default="results/graph_connectivity.csv")
    args = p.parse_args()

    if args.command == "pair-screen":
        result = select_pairs(load_credits(args.credits), args.min_events, args.min_risk_years,
                              args.strict, args.count_unknown_in_share)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        result.to_csv(args.out, index=False)
        print(result.to_string(index=False))
    elif args.command == "preflight":
        credits = load_credits(args.credits)
        pair_counts, events_by_year, label_coverage = preflight_tables(
            credits, args.min_events, args.min_risk_years, args.strict,
            args.count_unknown_in_share)
        outdir = Path(args.outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        pair_counts.to_csv(outdir / "pair_screen.csv", index=False)
        events_by_year.to_csv(outdir / "events_by_year.csv", index=False)
        label_coverage.to_csv(outdir / "label_coverage_by_year.csv", index=False)
        print(f"Wrote pre-model screening tables to {outdir}; no model was fitted.")
    elif args.command == "build":
        result = build_actor_year(load_credits(args.credits), args.source, args.destination,
                                  args.exposure_lookback_years, strict=args.strict,
                                  count_unknown=args.count_unknown_in_share)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        if Path(args.out).suffix.lower() == ".csv":
            result.to_csv(args.out, index=False)
        else:
            result.to_parquet(args.out, index=False)
        print(f"Wrote {len(result):,} risk rows; events={int(result.event.sum())}")
    elif args.command == "analyze":
        panel = pd.read_csv(args.actor_years) if Path(args.actor_years).suffix.lower() == ".csv" else pd.read_parquet(args.actor_years)
        outdir = Path(args.outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        metrics, split = evaluate(panel, args.train_end_year, args.validation_end_year,
                                  bootstrap_reps=args.bootstrap_reps)
        placebo = run_placebo(panel, args.train_end_year, args.validation_end_year)
        reverse = run_reverse_direction_check(panel, args.train_end_year, args.validation_end_year,
                                              bootstrap_reps=args.bootstrap_reps)
        metrics.to_csv(outdir / "model_comparison.csv", index=False)
        clustered_coefficients(panel).to_csv(outdir / "clustered_coefficients.csv", index=False)
        likelihood_ratio_tests(panel).to_csv(outdir / "likelihood_ratio_tests.csv", index=False)
        placebo.to_csv(outdir / "placebo_comparison.csv", index=False)
        reverse.to_csv(outdir / "reverse_direction_comparison.csv", index=False)
        pd.DataFrame([split]).to_json(outdir / "split.json", orient="records", indent=2)
        print(metrics.to_string(index=False))
        print("\nLead-exposure placebo\n", placebo.to_string(index=False))
    elif args.command == "permutation":
        result = timing_permutation_test(load_credits(args.credits), args.source, args.destination,
                                         args.train_end_year, args.validation_end_year,
                                         args.reps, args.seed, strict=args.strict)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        result.to_csv(args.out, index=False)
        print(result.to_string(index=False))
    else:
        result = graph_connectivity_audit(load_credits(args.credits))
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        result.to_csv(args.out, index=False)
        print(result.to_string(index=False))

if __name__ == "__main__":
    main()
