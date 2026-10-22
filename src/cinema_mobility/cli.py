from __future__ import annotations
import argparse
from pathlib import Path
import numpy as np
import pandas as pd
from .ingest import from_imdb, title_status, build_credits
from . import quality, review
from .resolver import resolve_labels, TooManyFailures
from .pipeline import load_credits, select_pairs, build_actor_year, evaluate, run_placebo


def _focal(args):
    return tuple(x.strip().lower() for x in args.focal_languages.split(",") if x.strip())


def _status(end_year, focal):
    i = "data/interim/"
    return title_status(pd.read_parquet(i + "titles.parquet"), pd.read_parquet(i + "candidates.parquet"),
                        pd.read_parquet(i + "labels.parquet"), end_year, focal)


def _derived(args):
    i = "data/interim/"
    if args.command == "make-sample":
        credits = pd.read_parquet(args.credits)
        out = quality.make_sample(credits, args.n, seed=args.seed)
        out.to_parquet(args.out, index=False)
        print(f"Wrote {len(out):,} credits, {out.title_id.nunique():,} titles, {out.actor_id.nunique():,} actors to {args.out}")
        print(out.label_source.value_counts().to_string())
        return
    status = _status(args.end_year, _focal(args))
    status.to_parquet(i + "title_status.parquet", index=False)
    raw = pd.read_parquet(i + "credits_raw.parquet")
    if args.command == "build-credits":
        out = build_credits(raw, status, args.out)
        print(f"Wrote {len(out):,} credits ({out.title_id.nunique():,} titles, {out.actor_id.nunique():,} actors) to {args.out}")
        print(status.status.value_counts().to_string())
        return
    credits = build_credits(raw, status)
    labels = pd.read_parquet(i + "labels.parquet")
    summary = pd.read_parquet(i + "akas_summary.parquet")
    stages = quality.stage_counts(args.basics, args.principals)
    pairs = quality.pair_table(credits)
    focal = _focal(args)
    scenarios = {
        "A: all kept titles (unknown and other-language kept as ties)": quality.connectivity(credits),
        "B: unknown titles dropped": quality.connectivity(credits[credits.language.notna()]),
        "C: focal-language titles only": quality.connectivity(credits[credits.language.isin(focal)]),
    }
    dq = quality.data_quality(status, labels, summary, credits, stages, pairs, scenarios,
                              quality.target_connectivity(credits, focal), quality.eligibility_sensitivity(credits, focal))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    dq.to_csv(args.out, index=False)
    basics = pd.read_csv(args.basics, sep="\t", na_values="\\N", usecols=["tconst", "primaryTitle"]).rename(columns={"tconst": "title_id"})
    review = quality.review_shortlist(credits, status, labels, summary, basics[basics.title_id.isin(credits.title_id)], pairs)
    review.to_csv(args.review_out, index=False)
    print(f"Wrote {args.out} ({len(dq)} rows) and {args.review_out} ({len(review):,} titles)")
    for sec in ["stage", "tier_overall", "tier_contribution", "tier_exclusive", "connectivity", "connectivity_target_actors", "eligibility_sensitivity", "unknown_by_akas_language", "unknown_by_period", "tier_by_language", "orphan_ids", "merged_ids"]:
        print("\n##", sec); print(dq[dq.section == sec].dropna(axis=1, how="all").drop(columns="section").to_string(index=False))


def _review(args):
    if args.command == "review-packet":
        info = review.make_packet(args.review_csv, args.outdir, [x.strip() for x in args.reviewers.split(",")],
                                  args.top, args.overlap, args.control, args.seed)
        print(f"Wrote packet to {args.outdir}: {info['per_reviewer']} rows per reviewer "
              f"({info['unique_each']} unique + {info['overlap']} shared), control {info['control']}, "
              f"{info['unreviewed']} titles left unreviewed. Keep packet_key.csv private.")
        return
    files = [Path(f).resolve() for f in args.reviewed + ([args.control] if getattr(args, "control", None) else [])]
    for f in files:
        if not f.is_file():
            raise SystemExit(f"reviewed file not found: {f}")
    hashes = {f: review.sha256(f) for f in files}
    def guard(*outs):
        for o in outs:
            if Path(o).resolve() in hashes:
                raise SystemExit(f"refusing to write {o}: it is a reviewed input file")
    if args.command == "review-agreement":
        guard(args.out)
        reviews = [review.read_review(f) for f in [Path(x).resolve() for x in args.reviewed]]
        key = pd.read_csv(args.key) if Path(args.key).exists() else None
        overlap = set(key[key.set == "overlap"].tconst) if key is not None else set.intersection(*[set(r.tconst) for r in reviews])
        rep = review.agreement_report([r[r.tconst.isin(overlap)] for r in reviews])
        print(f"Overlap titles: {len(overlap)}; reviewers: {len(reviews)}")
        print(rep.round(3).to_string(index=False))
        if args.control and key is not None:
            ctl = review.consensus(review.read_review(args.control))
            err = review.control_error_rate(ctl, int((key.set == "unreviewed").sum()))
            if err:
                print("\nControl sample (random titles below the reviewed block):")
                print(f"  {err['n_flagged']}/{err['n_control_reviewed']} excluded = {err['error_rate']:.1%} "
                      f"(95% CI {err['ci_low']:.1%}-{err['ci_high']:.1%})")
                print(f"  expected exclusions among the {err['n_unreviewed_titles']:,} unreviewed titles: "
                      f"{err['expected_flagged_unreviewed']:.0f} ({err['expected_low']:.0f}-{err['expected_high']:.0f})")
                rep = pd.concat([rep, pd.DataFrame([{"field": "control_error_rate", "n_titles": err["n_control_reviewed"],
                                                     "positive_rate": err["error_rate"], "pct_agree": np.nan}])])
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        rep.to_csv(args.out, index=False)
        return
    # apply-review
    guard(args.credits, args.baseline, args.decisions_out, args.labels_out)
    i = "data/interim/"
    votes = pd.concat([review.read_review(f) for f in files])
    decisions = review.consensus(votes)
    credits_path, baseline_path = Path(args.credits), Path(args.baseline)
    if not baseline_path.exists():
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_bytes(credits_path.read_bytes())
        print(f"Saved pre-review credits to {baseline_path}")
    baseline = pd.read_parquet(baseline_path)
    end_year = args.end_year or int(baseline.year.max())
    titles = pd.read_parquet(i + "titles.parquet")
    new, status, lab = review.apply_review(decisions, titles, pd.read_parquet(i + "candidates.parquet"),
                                           pd.read_parquet(i + "labels.parquet"), pd.read_parquet(i + "credits_raw.parquet"),
                                           end_year, _focal(args), args.excluded_as)
    ch = review.credit_changes(baseline, new)
    new.to_parquet(credits_path, index=False)
    Path(args.decisions_out).parent.mkdir(parents=True, exist_ok=True)
    decisions.to_csv(args.decisions_out, index=False)
    lab.to_parquet(args.labels_out, index=False)
    assert all(review.sha256(f) == h for f, h in hashes.items()), "a reviewed file changed during apply-review"
    d = decisions[decisions.reviewed.astype(bool)]
    unknown = int((~d.tconst.isin(titles.title_id)).sum())
    print(f"Reviewed titles: {len(d):,} (from {len(files)} files; {unknown} not in the titles table)")
    print(f"  dubbed {int(d.is_dubbed.sum())}, ambiguous {int(d.is_ambiguous.sum())}, split-vote conflicts {int(d.conflict.sum())}, "
          f"language corrections {int((d.corrected_language.notna() & ~d.exclude).sum())}")
    print(f"  excluded titles: {int(d.exclude.sum()):,} ({'kept as ties, no language' if args.excluded_as == 'ties' else 'credits dropped'})")
    print(f"Credits table rebuilt with end-year {end_year}: {ch['credits_before']:,} -> {ch['credits_after']:,} credits")
    print(f"  titles changed {ch['titles_changed']:,} ({ch['titles_to_ties_only']:,} to ties only, {ch['titles_relabelled']:,} relabelled)")
    print(f"  credits changed {ch['credits_changed']:,} ({ch['credits_removed']:,} removed); actors affected {ch['actors_affected']:,}")
    print("Reviewed files verified unchanged (sha256).")


def main():
    p = argparse.ArgumentParser(prog="cinema-mobility")
    sub = p.add_subparsers(dest="command", required=True)
    ingest = sub.add_parser("ingest", help="Join IMDb principals with title years and reviewed language labels")
    ingest.add_argument("--principals", required=True)
    ingest.add_argument("--basics", required=True)
    ingest.add_argument("--languages", required=True)
    ingest.add_argument("--out", default="data/processed/credits.parquet")
    res = sub.add_parser("resolve-labels", help="Label original language of candidate titles (T1 TMDB, T2 Wikidata)")
    res.add_argument("--titles", default="data/interim/titles.parquet")
    res.add_argument("--candidates", default="data/interim/candidates.parquet")
    res.add_argument("--end-year", type=int, default=None, help="drop titles after this year (default: no cutoff)")
    res.add_argument("--sample", type=int, help="random sample size, for a trial run")
    res.add_argument("--seed", type=int, default=42)
    res.add_argument("--rate", type=float, default=20, help="TMDB requests per second")
    res.add_argument("--cache", default="data/raw/label_cache.sqlite")
    res.add_argument("--out", default="data/interim/labels.parquet")
    bc = sub.add_parser("build-credits", help="Left-join credits to labels; unknown titles kept as ties")
    bc.add_argument("--end-year", type=int, default=None)
    bc.add_argument("--out", default="data/processed/credits.parquet")
    bc.add_argument("--focal-languages", default="hi,ta,te,ml,kn", help="comma-separated; only these can be sources/destinations")
    qr = sub.add_parser("quality-report", help="Write results/data_quality.csv, the review shortlist and connectivity")
    qr.add_argument("--end-year", type=int, default=None)
    qr.add_argument("--basics", default="data/raw/title.basics.tsv.gz")
    qr.add_argument("--principals", default="data/raw/title.principals.tsv.gz")
    qr.add_argument("--out", default="results/data_quality.csv")
    qr.add_argument("--focal-languages", default="hi,ta,te,ml,kn", help="comma-separated; only these can be sources/destinations")
    qr.add_argument("--review-out", default="data/raw/language_labels_review.csv")
    sm = sub.add_parser("make-sample", help="Write a ~5,000-credit shareable sample")
    sm.add_argument("--credits", default="data/processed/credits.parquet")
    sm.add_argument("--n", type=int, default=5000)
    sm.add_argument("--seed", type=int, default=42)
    sm.add_argument("--out", default="data/processed/credits_sample.parquet")
    rp = sub.add_parser("review-packet", help="Split the review shortlist into per-reviewer files, overlap set and control sample")
    rp.add_argument("--review-csv", default="data/raw/language_labels_review.csv")
    rp.add_argument("--outdir", default="data/review/packet")
    rp.add_argument("--reviewers", default="A,B,C", help="comma-separated reviewer names (file suffixes)")
    rp.add_argument("--top", type=int, default=1500)
    rp.add_argument("--overlap", type=int, default=100)
    rp.add_argument("--control", type=int, default=200)
    rp.add_argument("--seed", type=int, default=42)
    ra = sub.add_parser("review-agreement", help="Fleiss/Cohen kappa on the overlap set and error rate from the control sample")
    ra.add_argument("--reviewed", nargs="+", required=True, help="the reviewers' returned files")
    ra.add_argument("--control", help="reviewed control file")
    ra.add_argument("--key", default="data/review/packet/packet_key.csv")
    ra.add_argument("--out", default="results/review_agreement.csv")
    ap = sub.add_parser("apply-review", help="Merge reviewed CSVs into the labels and rebuild credits.parquet (reviewed files are read-only)")
    ap.add_argument("--reviewed", nargs="+", required=True)
    ap.add_argument("--excluded-as", choices=["ties", "drop"], default="ties",
                    help="ties: dubbed/ambiguous titles stay as collaboration ties with no language; drop: remove their credits")
    ap.add_argument("--end-year", type=int, default=None, help="default: the max year already in the credits table")
    ap.add_argument("--credits", default="data/processed/credits.parquet")
    ap.add_argument("--baseline", default="data/processed/credits_pre_review.parquet",
                    help="pre-review credits; copied from --credits the first time, then reused so reruns are idempotent")
    ap.add_argument("--decisions-out", default="data/interim/review_decisions.csv")
    ap.add_argument("--labels-out", default="data/interim/labels_reviewed.parquet")
    ap.add_argument("--focal-languages", default="hi,ta,te,ml,kn")
    pairs = sub.add_parser("select-pairs", help="Report pre-model pair event and actor support")
    pairs.add_argument("--credits", default="data/processed/credits.parquet")
    pairs.add_argument("--min-events", type=int, default=10)
    pairs.add_argument("--min-risk-years", type=int, default=100)
    pairs.add_argument("--out", default="results/pair_screen.csv")
    pairs.add_argument("--focal-languages", default="hi,ta,te,ml,kn", help="comma-separated; only these can be sources/destinations")
    build = sub.add_parser("build", help="Build actor-year risk set and temporal exposure variables")
    build.add_argument("--credits", default="data/processed/credits.parquet")
    build.add_argument("--source")
    build.add_argument("--destination")
    build.add_argument("--min-exposure-lag", type=int, default=1, choices=[1, 2, 3])
    build.add_argument("--focal-languages", default="hi,ta,te,ml,kn", help="comma-separated; only these can be sources/destinations")
    build.add_argument("--out", default="data/processed/actor_year.parquet")
    model = sub.add_parser("analyze", help="Run chronological baseline/network comparison and lead placebo")
    model.add_argument("--actor-years", default="data/processed/actor_year.parquet")
    model.add_argument("--split-year", type=int)
    model.add_argument("--outdir", default="results")
    args = p.parse_args()
    if args.command == "ingest":
        df = from_imdb(args.principals, args.basics, args.languages, args.out)
        print(f"Wrote {len(df):,} principal credits to {args.out}")
    elif args.command in ("review-packet", "review-agreement", "apply-review"):
        _review(args)
    elif args.command in ("build-credits", "quality-report", "make-sample"):
        _derived(args)
    elif args.command == "resolve-labels":
        titles = pd.read_parquet(args.titles)
        ids = titles[titles.title_id.isin(pd.read_parquet(args.candidates).title_id)]
        if args.end_year:
            ids = ids[ids.year <= args.end_year]
        if args.sample:
            ids = ids.sample(args.sample, random_state=args.seed)
        try:
            out = resolve_labels(ids.title_id, cache_db=args.cache, interval=1 / args.rate, workers=8)
        except TooManyFailures as e:
            raise SystemExit(f"STOPPED: {e}")
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        out.to_parquet(args.out, index=False)
        print(out.label_source.value_counts().to_string())
    elif args.command == "select-pairs":
        df = load_credits(args.credits)
        out = select_pairs(df, args.min_events, args.min_risk_years, _focal(args))
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(args.out, index=False)
        print(out.to_string(index=False))
    elif args.command == "build":
        df = load_credits(args.credits)
        out = build_actor_year(df, args.source, args.destination, args.min_exposure_lag, _focal(args))
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
