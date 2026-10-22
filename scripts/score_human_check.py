"""Score a filled human-verification sheet against the AI key.

    python scripts/score_human_check.py [--sheet data/review/human_check.csv] [--key data/review/human_check_key.csv]

Read-only: opens both files for reading, writes nothing. Run it only after the sheet is filled in.
"""
from __future__ import annotations
import argparse
import math
import sys
import pandas as pd

NAMES = {"hindi": "hi", "urdu": "ur", "tamil": "ta", "telugu": "te", "malayalam": "ml", "kannada": "kn", "bengali": "bn",
         "bangla": "bn", "marathi": "mr", "punjabi": "pa", "gujarati": "gu", "odia": "or", "oriya": "or", "english": "en",
         "assamese": "as", "bhojpuri": "bho", "tulu": "tcy", "konkani": "kok", "sanskrit": "sa"}
YES, NO = {"yes", "y", "1", "true", "t"}, {"no", "n", "0", "false", "f"}


def wilson(x: int, n: int, z: float = 1.96):
    if n == 0:
        return float("nan"), float("nan"), float("nan")
    p = x / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return p, max(0.0, centre - half), min(1.0, centre + half)


def norm_lang(x: str) -> str:
    x = (x or "").strip().lower()
    return NAMES.get(x, x)


def norm_flag(x: str):
    x = (x or "").strip().lower()
    return True if x in YES else False if x in NO else None


def load(sheet_path, key_path) -> pd.DataFrame:
    sheet = pd.read_csv(sheet_path, dtype=str, keep_default_na=False)
    key = pd.read_csv(key_path, dtype=str, keep_default_na=False)
    if set(sheet.tconst) != set(key.tconst) or sheet.tconst.duplicated().any():
        raise SystemExit("sheet and key do not cover the same titles")
    m = sheet.merge(key.drop(columns="primaryTitle"), on="tconst")
    m["me_lang"] = m.my_language.map(norm_lang)
    m["me_bi"] = m.my_bilingual.map(norm_flag)
    m["me_dub"] = m.my_dubbed.map(norm_flag)
    m["ai_lang"] = m.ai_language.map(norm_lang)
    m["ai_bi"] = m.ai_ambiguous.map(norm_flag)
    m["ai_dub"] = m.ai_dubbed.map(norm_flag)
    return m


def score(m: pd.DataFrame) -> dict:
    out = {}
    for name, me, ai in [("language", "me_lang", "ai_lang"), ("bilingual vs ambiguous", "me_bi", "ai_bi"), ("dubbed", "me_dub", "ai_dub")]:
        answered = m[m[me].notna() & (m[me] != "")]
        match = answered[me] == answered[ai]
        out[name] = {"answered": len(answered), "match": int(match.sum()), "mask": match, "rows": answered.index}
    both = m.index[m.me_lang.ne("") & m.me_bi.notna()]
    full = (m.loc[both, "me_lang"] == m.loc[both, "ai_lang"]) & (m.loc[both, "me_bi"] == m.loc[both, "ai_bi"])
    out["language and bilingual both"] = {"answered": len(both), "match": int(full.sum()), "mask": full, "rows": both}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sheet", default="data/review/human_check.csv")
    ap.add_argument("--key", default="data/review/human_check_key.csv")
    args = ap.parse_args(argv)
    m = load(args.sheet, args.key)
    unanswered = m[m.me_lang.eq("") & m.me_bi.isna()]
    print(f"Rows: {len(m)}; not answered at all: {len(unanswered)}")
    if len(unanswered):
        print("  unanswered:", ", ".join(unanswered.primaryTitle))
    bad = m[(m.my_bilingual.str.strip() != "") & m.me_bi.isna()]
    if len(bad):
        print("  WARNING: my_bilingual should be yes/no; unreadable for:", ", ".join(bad.primaryTitle))
    s = score(m)
    print("\nAgreement with the AI key (Wilson 95% interval)")
    for name, r in s.items():
        p, lo, hi = wilson(r["match"], r["answered"])
        print(f"  {name:28} {r['match']:>2}/{r['answered']:<2} = {p:6.1%}   95% CI {lo:.1%}-{hi:.1%}")
    print("\nBy group (language match / bilingual match):")
    for g, sub in m.groupby("group"):
        ls, bs = s["language"], s["bilingual vs ambiguous"]
        lm = ls["mask"][ls["mask"].index.isin(sub.index)]
        bm = bs["mask"][bs["mask"].index.isin(sub.index)]
        print(f"  {g:26} language {int(lm.sum())}/{len(lm)}   bilingual {int(bm.sum())}/{len(bm)}")
    print("\nDisagreements")
    n = 0
    for i, r in m.iterrows():
        issues = []
        if r.me_lang and r.me_lang != r.ai_lang:
            issues.append(f"language: me={r.me_lang!r} AI={r.ai_lang!r}")
        if r.me_bi is not None and r.me_bi != r.ai_bi:
            issues.append(f"bilingual: me={'yes' if r.me_bi else 'no'} AI ambiguous={'yes' if r.ai_bi else 'no'}")
        if r.me_dub is not None and r.me_dub != r.ai_dub:
            issues.append(f"dubbed: me={'yes' if r.me_dub else 'no'} AI={'yes' if r.ai_dub else 'no'}")
        if issues:
            n += 1
            print(f"- {r.primaryTitle} ({r.year}, {r.tconst}) [{r.group}, AI confidence {r.ai_confidence}]")
            for t in issues:
                print(f"    {t}")
            print(f"    AI notes: {r.ai_notes}")
            if r.my_notes or r.sources_checked:
                print(f"    my notes: {r.my_notes} | sources: {r.sources_checked}")
    if not n:
        print("  none")
    print("\nNote: the AI flagged 'ambiguous' for any source conflict or multi-language source, which can differ from 'bilingual'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
