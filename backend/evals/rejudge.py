#
# Re-judge a finished run's saved transcripts with the LLM judge and rebuild its summary,
# e.g. after a rubric change:  python -m evals.rejudge <run_dir> [--judge-model gpt-4o]
# The conversations are not replayed.
#

import argparse
import asyncio
import json
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

from store.bundle import load_bundle

from .judge import JUDGE_MODEL, judge
from .oracle import compute_oracle
from .report import write_summary

load_dotenv(Path(__file__).parent.parent / ".env", override=True)


async def _one(f: Path, model: str, bundles: dict, sem) -> tuple:
    d = json.loads(f.read_text())
    row = d["row"]
    bundle = bundles.setdefault(row["catalog"], load_bundle(row["catalog"]))
    oracle = compute_oracle(d["persona"], bundle, date.fromisoformat(d["anchor"]), datetime.fromisoformat(d["clock"]))
    bookings = [dict(b, starts_at=datetime.fromisoformat(b["starts_at"])) for b in d["bookings"]]
    async with sem:
        v = await judge(oracle, d["events"], bookings, bundle, d["persona"], model=model, error=row.get("error"))
    changed = v["pass"] != row["pass"]
    row.update({k: v[k] for k in ("pass", "tier", "score", "violations", "failures", "tier_reasons", "quality")})
    row["oracle"] = oracle.summary()
    d["row"] = row
    f.write_text(json.dumps(d, indent=1, default=str))
    return row, changed


async def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--judge-model", default=JUDGE_MODEL)
    ap.add_argument("--concurrency", type=int, default=8)
    args = ap.parse_args(argv)
    run = Path(args.run)
    sem, bundles = asyncio.Semaphore(args.concurrency), {}
    done = await asyncio.gather(*[_one(f, args.judge_model, bundles, sem)
                                  for f in sorted((run / "transcripts").glob("*.json"))])
    rows = [r for r, _ in done]
    (run / "results.jsonl").write_text("".join(json.dumps(r, default=str) + "\n" for r in rows))
    meta = json.loads((run / "meta.json").read_text())
    print(f"{len(rows)} conversations re-judged, {sum(c for _, c in done)} verdicts changed -> {write_summary(run, rows, meta)}")


if __name__ == "__main__":
    asyncio.run(main())
