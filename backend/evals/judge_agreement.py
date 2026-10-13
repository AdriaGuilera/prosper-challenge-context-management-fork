#
# How far the LLM judge agrees with the verdicts already saved in eval/runs (written by the
# rule-based judge it replaced). Re-judges a stratified sample of saved transcripts; nothing
# is replayed and nothing in the runs is changed.
#
#   python -m evals.judge_agreement --models gpt-4o,gpt-5.6-terra --per-system 50
#
# Writes eval/runs/judge-agreement-<time>/ with every pair of verdicts and a summary.
#

import argparse
import asyncio
import json
import random
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

from store.bundle import load_bundle

from .judge import judge
from .llm import cost_usd
from .oracle import TIERS, compute_oracle

load_dotenv(Path(__file__).parent.parent / ".env", override=True)
RUNS = Path(__file__).parent.parent.parent / "eval" / "runs"
DEFAULT_RUNS = ["20261007-001245-stage1-given", "20261007-002805-stage3-scale", "20261011-181219-policyset"]


def sample(runs: list, per_system: int, seed: int = 7) -> list:
    """Per system, half the sample from conversations the saved verdict failed (where the
    judges are likeliest to differ), half from passes."""
    by = defaultdict(lambda: {True: [], False: []})
    for run in runs:
        for f in sorted((RUNS / run / "transcripts").glob("*.json")):
            row = json.loads(f.read_text())["row"]
            if not row.get("error"):
                by[row["system"]][row["pass"]].append(f)
    rng = random.Random(seed)
    out = []
    for system, groups in sorted(by.items()):
        fails = rng.sample(groups[False], min(len(groups[False]), per_system // 2))
        passes = rng.sample(groups[True], min(len(groups[True]), per_system - len(fails)))
        out += fails + passes
    return out


async def rejudge(f: Path, model: str, bundles: dict, sem, meter) -> dict:
    d = json.loads(f.read_text())
    row = d["row"]
    bundle = bundles.setdefault(row["catalog"], load_bundle(row["catalog"]))
    oracle = compute_oracle(d["persona"], bundle, date.fromisoformat(d["anchor"]), datetime.fromisoformat(d["clock"]))
    bookings = [dict(b, starts_at=datetime.fromisoformat(b["starts_at"])) for b in d["bookings"]]
    async with sem:
        v = await judge(oracle, d["events"], bookings, bundle, d["persona"], model=model, meter=meter)
    return {"file": str(f.relative_to(RUNS)), "system": row["system"], "model_under_test": row["model"],
            "old": {k: row[k] for k in ("pass", "tier", "violations", "failures", "tier_reasons")},
            "new": {k: v[k] for k in ("pass", "tier", "violations", "failures", "tier_reasons")}}


def summarize(pairs: list) -> dict:
    n = len(pairs)
    agree = sum(p["old"]["pass"] == p["new"]["pass"] for p in pairs)
    tier = sum(p["old"]["tier"] == p["new"]["tier"] for p in pairs)
    rank = {t: i for i, t in enumerate(TIERS)}
    tier_near = sum(abs(rank[p["old"]["tier"]] - rank[p["new"]["tier"]]) <= 1 for p in pairs)
    old_v = [p for p in pairs if p["old"]["violations"]]
    new_v = [p for p in pairs if p["new"]["violations"]]
    caught = sum(bool(p["new"]["violations"]) for p in old_v)
    extra = sum(not p["old"]["violations"] for p in new_v)
    flips = Counter(("old pass, LLM fail" if p["old"]["pass"] else "old fail, LLM pass")
                    for p in pairs if p["old"]["pass"] != p["new"]["pass"])
    per_system = {}
    for s in sorted({p["system"] for p in pairs}):
        ps = [p for p in pairs if p["system"] == s]
        per_system[s] = {"n": len(ps), "pass_agreement": round(sum(p["old"]["pass"] == p["new"]["pass"] for p in ps) / len(ps), 3),
                         "old_pass_rate": round(sum(p["old"]["pass"] for p in ps) / len(ps), 3),
                         "llm_pass_rate": round(sum(p["new"]["pass"] for p in ps) / len(ps), 3)}
    return {"n": n, "pass_agreement": round(agree / n, 3), "tier_exact": round(tier / n, 3),
            "tier_within_one_step": round(tier_near / n, 3),
            "old_violation_convs": len(old_v), "llm_caught_of_those": caught,
            "llm_violation_convs_old_had_none": extra, "flips": dict(flips), "per_system": per_system}


async def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="gpt-4o")
    ap.add_argument("--per-system", type=int, default=50)
    ap.add_argument("--runs", default=",".join(DEFAULT_RUNS))
    ap.add_argument("--concurrency", type=int, default=8)
    args = ap.parse_args(argv)
    files = sample(args.runs.split(","), args.per_system)
    out_dir = RUNS / f"judge-agreement-{datetime.now():%Y%m%d-%H%M%S}"
    out_dir.mkdir(parents=True)
    report = {}
    for model in args.models.split(","):
        calls = []
        sem, bundles = asyncio.Semaphore(args.concurrency), {}
        pairs = await asyncio.gather(*[rejudge(f, model, bundles, sem, calls.append) for f in files])
        s = summarize(pairs)
        s["judge_cost_usd"] = round(sum(cost_usd(c["model"], c["prompt_tokens"], c["cached_tokens"], c["completion_tokens"]) or 0
                                        for c in calls), 2)
        report[model] = s
        (out_dir / f"pairs-{model}.json").write_text(json.dumps(pairs, indent=1, default=str))
        print(model, json.dumps(s, indent=1))
    (out_dir / "summary.json").write_text(json.dumps(report, indent=1))
    print(f"-> {out_dir}")


if __name__ == "__main__":
    asyncio.run(main())
