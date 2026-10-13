#
# Markdown summary of a run: accuracy and cost side by side, always.
#

import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path


def _pct(xs: list) -> str:
    return f"{100 * sum(xs) / len(xs):.0f}%" if xs else "–"


def _mean(xs: list, fmt: str = "{:.0f}") -> str:
    xs = [x for x in xs if x is not None]
    return fmt.format(statistics.mean(xs)) if xs else "–"


def _p(xs: list, q: float) -> str:
    xs = sorted(xs)
    if not xs:
        return "–"
    return f"{xs[min(len(xs) - 1, int(q * len(xs)))]:.0f}"


def _group_table(rows: list, keys: tuple) -> list:
    groups = defaultdict(list)
    for r in rows:
        groups[tuple(r[k] for k in keys)].append(r)
    out = [
        "| " + " | ".join(keys) + " | convs | gate pass | mean score | ideal/acc/sub/wrong | invariant breaks | "
        "LLM calls/conv | prompt tok/conv | max prompt tok | compl tok/conv | $/conv | turn p50/p95 ms | tool p50/p95 ms | quality |",
        "|" + "---|" * (len(keys) + 13),
    ]
    for k, rs in sorted(groups.items()):
        tiers = Counter(r["tier"] for r in rs)
        turn_ms = [m for r in rs for m in r["turn_ms"]]
        tool_ms = [m for r in rs for m in r.get("tool_ms", [])]
        cost = [r["cost_usd"] for r in rs if r["cost_usd"] is not None]
        quality = [r["quality"]["mean"] for r in rs if r.get("quality") and "mean" in r["quality"]]
        out.append(
            "| " + " | ".join(str(x) for x in k) + f" | {len(rs)} | {_pct([r['pass'] for r in rs])} | "
            f"{_mean([r['score'] for r in rs], '{:.2f}')} | "
            f"{tiers['ideal']}/{tiers['acceptable']}/{tiers['suboptimal']}/{tiers['wrong']} | "
            f"{sum(1 for r in rs if r['violations'])} | {_mean([r['agent_llm_calls'] for r in rs], '{:.1f}')} | "
            f"{_mean([r['prompt_tokens'] for r in rs])} | {_mean([r['max_prompt_tokens'] for r in rs])} | "
            f"{_mean([r['completion_tokens'] for r in rs])} | "
            f"{('$%.4f' % statistics.mean(cost)) if cost else 'n/a'} | {_p(turn_ms, .5)}/{_p(turn_ms, .95)} | "
            f"{_p(tool_ms, .5)}/{_p(tool_ms, .95)} | "
            f"{_mean(quality, '{:.2f}')} |")
    return out


def _target_cell(oracle: dict) -> str:
    t = oracle.get("target")
    if t:
        return t["booking"] + (f" (+{t['equally_good']} equally good)" if t["equally_good"] else "")
    return {"no_booking": "no booking", "information": "information only"}.get(oracle.get("outcome"), "–")


def write_summary(out_dir: Path, rows: list, meta: dict) -> Path:
    lines = [f"# Eval run {meta['run_id']}", "",
             f"- conversations: {len(rows)}; catalogs: {', '.join(meta['catalogs'])}; models: {', '.join(meta['models'])}; "
             f"systems: {', '.join(meta['systems'])}",
             f"- args: `{meta['args']}`",
             f"- judge = LLM ({meta['judge_model']}) against the oracle's answer key: pass = no invariant "
             "broken, outcome tier >= acceptable, required questions asked, required facts stated. quality = the same "
             "judge's 1-5 mean, never part of pass.",
             "- tool time is inside turn latency; simulator and judge tokens are excluded from cost.", ""]
    lines += ["## By system and model", ""] + _group_table(rows, ("system", "model")) + [""]
    if len({r["catalog"] for r in rows}) > 1:
        lines += ["## By catalog, system and model", ""] + _group_table(rows, ("catalog", "system", "model")) + [""]

    lines += ["## Per case (pass k/N per model)", ""]
    models = sorted({(r["system"], r["model"]) for r in rows})
    lines.append("| catalog | persona | behaviour | target booking | " + " | ".join(f"{s}:{m}" for s, m in models) + " |")
    lines.append("|---|---|---|---|" + "---|" * len(models))
    cases = defaultdict(lambda: defaultdict(list))
    targets = {}
    for r in rows:
        cases[(r["catalog"], r["persona"], r["behavior"])][(r["system"], r["model"])].append(r["pass"])
        targets[(r["catalog"], r["persona"])] = _target_cell(r.get("oracle") or {})
    for (c, p, b), by_model in sorted(cases.items()):
        cells = [f"{sum(by_model[m])}/{len(by_model[m])}" if by_model[m] else "–" for m in models]
        lines.append(f"| {c} | {p} | {b} | {targets[(c, p)]} | " + " | ".join(cells) + " |")
    lines.append("")

    lines += ["## By tag", "", "| tag | " + " | ".join(f"{s}:{m}" for s, m in models) + " |",
              "|---|" + "---|" * len(models)]
    tags = defaultdict(lambda: defaultdict(list))
    for r in rows:
        for t in r["tags"] or ["untagged"]:
            tags[t][(r["system"], r["model"])].append(r["pass"])
    for t, by_model in sorted(tags.items()):
        lines.append(f"| {t} | " + " | ".join(_pct(by_model[m]) if by_model[m] else "–" for m in models) + " |")
    lines.append("")

    reasons = Counter()
    for r in rows:
        for x in r["violations"]:
            reasons[("violation", x.split(":")[0][:70], r["system"])] += 1
        for x in r["failures"]:
            reasons[("failure", x[:70], r["system"])] += 1
        if not r["pass"]:
            for x in r["tier_reasons"]:
                reasons[("tier", x.split(":")[0][:70], r["system"])] += 1
    lines += ["## Why conversations failed", "", "| kind | reason | system | count |", "|---|---|---|---|"]
    for (kind, reason, system), n in reasons.most_common(40):
        lines.append(f"| {kind} | {reason} | {system} | {n} |")
    lines.append("")
    path = out_dir / "summary.md"
    path.write_text("\n".join(lines))
    return path


def main(argv) -> None:
    """Rebuild summary.md from a run's results.jsonl (e.g. after an interrupted run)."""
    run = Path(argv[0])
    rows = [json.loads(line) for line in (run / "results.jsonl").read_text().splitlines() if line.strip()]
    meta = json.loads((run / "meta.json").read_text())
    print(write_summary(run, rows, meta))


if __name__ == "__main__":
    main(sys.argv[1:])
