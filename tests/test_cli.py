import sys
from cinema_mobility import cli


def test_quality_report_final_defaults_to_final_v2(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "_derived", lambda args: seen.update(vars(args)))
    monkeypatch.setattr(sys, "argv", ["cinema-mobility", "quality-report"])
    cli.main()
    assert seen["final"] == "data/processed/credits_final_v2.parquet"
    assert seen["baseline"] == "data/processed/credits.parquet"
