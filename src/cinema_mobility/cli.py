from __future__ import annotations
import argparse
from pathlib import Path
import pandas as pd
from .ingest import from_imdb
from .pipeline import load_credits, select_pairs, build_actor_year, evaluate, run_placebo


def main():
    p = argparse.ArgumentParser(prog="cinema-mobility")
    sub = p.add_subparsers(dest="command", required=True)
    ingest = sub.add_parser("ingest", help="Join IMDb principals with title years and reviewed language labels")
    ingest.add_argument("--principals", required=True)
    ingest.add_argument("--basics", required=True)
    ingest.add_argument("--languages", required=True)
    ingest.add_argument("--out", default="data/processed/credits.parquet")
    pairs = sub.add_parser("select-pairs", help="Report pre-model pair event and actor support")
    pairs.add_argument("--credits", default="data/processed/credits.parquet")
    pairs.add_argument("--min-events", type=int, default=10)
    pairs.add_argument("--min-risk-years", type=int, default=100)
    pairs.add_argument("--out", default="results/pair_screen.csv")
    build = sub.add_parser("build", help="Build actor-year risk set and temporal exposure variables")
    build.add_argument("--credits", default="data/processed/credits.parquet")
    build.add_argument("--source")
    build.add_argument("--destination")
    build.add_argument("--min-exposure-lag", type=int, default=1, choices=[1, 2, 3])
    build.add_argument("--out", default="data/processed/actor_year.parquet")
    model = sub.add_parser("analyze", help="Run chronological baseline/network comparison and lead placebo")
    model.add_argument("--actor-years", default="data/processed/actor_year.parquet")
    model.add_argument("--split-year", type=int)
    model.add_argument("--outdir", default="results")
    args = p.parse_args()
    if args.command == "ingest":
        df = from_imdb(args.principals, args.basics, args.languages, args.out)
        print(f"Wrote {len(df):,} principal credits to {args.out}")
    elif args.command == "select-pairs":
        df = load_credits(args.credits)
        out = select_pairs(df, args.min_events, args.min_risk_years)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(args.out, index=False)
        print(out.to_string(index=False))
    elif args.command == "build":
        df = load_credits(args.credits)
        out = build_actor_year(df, args.source, args.destination, args.min_exposure_lag)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        out.to_parquet(args.out, index=False)
        print(f"Wrote {len(out):,} actor-pair-years to {args.out}; events={int(out.event.sum())}")
    else:
        df = pd.read_parquet(args.actor_years)
        outdir = Path(args.outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        metrics, split = evaluate(df, args.split_year)
        metrics.to_csv(outdir / "model_comparison.csv", index=False)
        placebo = run_placebo(df, args.split_year)
        placebo.to_csv(outdir / "placebo_comparison.csv", index=False)
        pd.DataFrame([split]).to_json(outdir / "split.json", orient="records", indent=2)
        print(metrics.to_string(index=False))
        print("\nLead-exposure placebo\n", placebo.to_string(index=False))

if __name__ == "__main__":
    main()
