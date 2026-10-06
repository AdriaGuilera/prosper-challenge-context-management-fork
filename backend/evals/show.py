#
# Print a conversation from a run, compactly:  python -m evals.show <run_dir|latest> [job substring]
#

import json
import sys
from pathlib import Path

from .personas import EVAL_DIR


def show(path: Path) -> None:
    d = json.loads(path.read_text())
    row = d["row"]
    print(f"== {row['job']}  {'PASS' if row['pass'] else 'FAIL'} {row['tier']}  {row['violations'] + row['failures'] + row['tier_reasons']}")
    print(f"   oracle: {json.dumps(row['oracle'])[:400]}")
    args = {e["id"]: e.get("args") for e in d["events"] if e["type"] == "tool_started"}
    for e in d["events"]:
        t = e["type"]
        if t == "agent":
            print(f"  AGENT [{e.get('node')}]: {e['text']}")
        elif t == "caller":
            print(f"  CALLER ({e['act']}): {e['text']}")
        elif t == "tool_finished":
            print(f"     tool {e['tool']} {json.dumps(args.get(e['id']))} -> {json.dumps(e.get('result'), default=str)[:300]}")
        elif t == "edge_taken":
            print(f"     edge {e['function']} -> {e['target']} {e.get('args') or ''}")
        elif t in ("error", "stuck", "bad_function"):
            print(f"     !! {t}: {e}")
    print(f"   booking: {row['booking']}  quality: {row.get('quality')}")


def main(argv):
    runs = EVAL_DIR / "runs"
    run = sorted(runs.iterdir())[-1] if not argv or argv[0] == "latest" else Path(argv[0])
    if not run.is_absolute() and not run.exists():
        run = runs / argv[0]
    pattern = argv[1] if len(argv) > 1 else ""
    for p in sorted((run / "transcripts").glob("*.json")):
        if pattern in p.name:
            show(p)


if __name__ == "__main__":
    main(sys.argv[1:])
