#
# Re-run the deterministic judge over a finished run's saved transcripts and rebuild
# its summary, e.g. after a judge fix:  python -m evals.rejudge <run_dir>
# The conversations are not replayed; the LLM quality scores are kept as they were.
#

import json
import sys
from datetime import date, datetime
from pathlib import Path

from store.bundle import load_bundle

from .judge import deterministic
from .oracle import compute_oracle
from .report import write_summary


def main(argv) -> None:
    run = Path(argv[0])
    bundles: dict = {}
    rows, changed = [], 0
    for f in sorted((run / "transcripts").glob("*.json")):
        d = json.loads(f.read_text())
        row = d["row"]
        bundle = bundles.setdefault(row["catalog"], load_bundle(row["catalog"]))
        oracle = compute_oracle(d["persona"], bundle, date.fromisoformat(d["anchor"]), datetime.fromisoformat(d["clock"]))
        bookings = [dict(b, starts_at=datetime.fromisoformat(b["starts_at"])) for b in d["bookings"]]
        v = deterministic(oracle, d["events"], bookings, bundle)
        if row.get("error"):
            v["pass"] = False
            v["failures"].append(f"conversation error: {row['error']}")
        changed += v["pass"] != row["pass"]
        row.update({k: v[k] for k in ("pass", "tier", "score", "violations", "failures", "tier_reasons")})
        row["oracle"] = oracle.summary()
        d["row"] = row
        f.write_text(json.dumps(d, indent=1, default=str))
        rows.append(row)
    (run / "results.jsonl").write_text("".join(json.dumps(r, default=str) + "\n" for r in rows))
    meta = json.loads((run / "meta.json").read_text())
    print(f"{len(rows)} conversations re-judged, {changed} verdicts changed -> {write_summary(run, rows, meta)}")


if __name__ == "__main__":
    main(sys.argv[1:])
