#
# Eval CLI: every (catalog x persona x behaviour x model x repeat) conversation, judged,
# with cost, into eval/runs/<run_id>/ (gitignored) plus a markdown summary.
#
#   python -m evals.run --catalog given --models gpt-4o-mini --n 3
#   python -m evals.run --catalog all --models gpt-4o,gpt-4o-mini,gpt-5.6-luna,gpt-5.6-terra --n 5 --systems tools,baseline
#
# Each conversation gets its own calendar scope, so concurrent runs never see each
# other's bookings, and its own copy of the persona's patient record.
#

import argparse
import asyncio
import fnmatch
import json
import random
import sys
import time
import uuid
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

from dotenv import load_dotenv

BACKEND = Path(__file__).parent.parent
load_dotenv(BACKEND / ".env", override=True)

from agent_builder import AgentBuilder  # noqa: E402
from agent_builder.tools import ToolContext  # noqa: E402
from store import db  # noqa: E402
from store.bundle import list_bundles, load_bundle, normalize  # noqa: E402

from . import judge as judges  # noqa: E402
from .baseline import baseline_config  # noqa: E402
from .generators import generate_personas  # noqa: E402
from .llm import cost_usd  # noqa: E402
from .oracle import compute_oracle, seeded_patient_id  # noqa: E402
from .personas import EVAL_DIR, load_behaviors, load_for_simulator, load_personas  # noqa: E402
from .report import write_summary  # noqa: E402
from .runner import FlowRunner  # noqa: E402
from .simulator import CallerSimulator  # noqa: E402

AGENT_PATH = BACKEND / "agents" / "clinic-scheduler.json"
RUNS_DIR = EVAL_DIR / "runs"


async def seed_patient(conn, cv: str, scope: str, persona: dict, bundle) -> None:
    caller = persona["caller"]
    rec = caller.get("record", {})
    if not rec.get("existing"):
        return
    pid = seeded_patient_id(caller["name"])
    await conn.execute("INSERT INTO patients VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT DO NOTHING",
                       cv, scope, pid, caller["name"], normalize(caller["name"]), date.fromisoformat(caller["dob"]))
    ids_by_name = bundle.type_ids_by_name()
    for r in rec.get("referrals", []):
        name = r if isinstance(r, str) else r["type"]
        expires = date(2099, 1, 1) if isinstance(r, str) else date.fromisoformat(r.get("expires", "2099-01-01"))
        for tid in ids_by_name.get(normalize(name), []):
            await conn.execute("INSERT INTO referrals VALUES ($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING",
                               cv, scope, pid, tid, expires)


async def collect_bookings(conn, cv: str, scope: str) -> list:
    """The bookings a conversation wrote, then wipe its scope."""
    rows = await conn.fetch(
        """SELECT confirmation, patient_id, provider_id, location_id, appointment_type_id, starts_at FROM appointments
           WHERE catalog_version = $1 AND scope = $2 AND status = 'booked' ORDER BY id""", cv, scope)
    for table in ("appointments", "referrals", "patients"):
        await conn.execute(f"DELETE FROM {table} WHERE catalog_version = $1 AND scope = $2", cv, scope)
    return [dict(r) for r in rows]


async def converse(job: dict, max_turns: int) -> dict:
    events: list = []
    t0 = time.perf_counter()

    def log(e):
        events.append({"t": round(time.perf_counter() - t0, 3), **e})

    async def sink(e):
        log(dict(e))

    persona, bundle = job["persona"], job["bundle"]
    ctx = ToolContext(catalog=bundle.name, scope=job["scope"], clock=lambda: job["clock"])
    if job["system"] == "baseline":
        config = baseline_config(bundle, job["model"])
    else:
        config = json.loads(AGENT_PATH.read_text())
    config["model"] = job["model"]
    builder = AgentBuilder.from_dict(config)
    builder.ctx = ctx
    builder.set_event_sink(sink)
    runner = FlowRunner(builder, job["model"], log)
    sim = CallerSimulator(load_for_simulator(persona, job["behavior"]), log)

    pool = await db.pool()
    async with pool.acquire() as conn:
        await seed_patient(conn, job["cv"], job["scope"], persona, bundle)
    error = None
    try:
        agent_text = await runner.start()
        log({"type": "agent", "text": agent_text, "node": runner.node})
        for _ in range(max_turns):
            if runner.ended:
                break
            turn = await sim.reply(agent_text)
            log({"type": "caller", "text": turn["say"], "act": turn["act"]})
            agent_text = await runner.caller_says(turn["say"])
            log({"type": "agent", "text": agent_text, "node": runner.node})
            if turn["act"] == "goodbye":
                break  # the agent has answered the goodbye; the caller hangs up
    except Exception as e:  # a crashed conversation is a result, not a crash of the run
        error = f"{type(e).__name__}: {e}"
        log({"type": "error", "text": error})

    async with pool.acquire() as conn:
        bookings = await collect_bookings(conn, job["cv"], job["scope"])

    oracle = compute_oracle(persona, bundle, job["anchor"], job["clock"])
    verdict = await judges.judge(oracle, events, bookings, bundle, persona, model=job["judge_model"], meter=log, error=error)
    return {"events": events, "verdict": verdict, "oracle": oracle.summary(), "bookings": bookings, "error": error}


def metrics(events: list, model: str) -> dict:
    agent = [e for e in events if e["type"] == "llm_call" and e["role"] == "agent"]
    turns = [e["ms"] for e in events if e["type"] == "agent_turn_ms"]
    tools = [e for e in events if e["type"] == "tool_finished"]
    prompt = sum(e["prompt_tokens"] for e in agent)
    cached = sum(e["cached_tokens"] for e in agent)
    completion = sum(e["completion_tokens"] for e in agent)
    return {
        "agent_llm_calls": len(agent), "prompt_tokens": prompt, "cached_tokens": cached,
        "completion_tokens": completion, "cost_usd": cost_usd(model, prompt, cached, completion),
        "turns": len([e for e in events if e["type"] == "caller"]),
        "turn_ms": turns, "tool_calls": len(tools),
        "tool_ms": [e["ms"] for e in tools],
        "max_prompt_tokens": max((e["prompt_tokens"] for e in agent), default=0),
    }


async def run_job(job: dict, sem, out_dir: Path, max_turns: int, results: list, progress) -> None:
    async with sem:
        started = time.perf_counter()
        res = await converse(job, max_turns)
        v = res["verdict"]
        row = {
            "job": job["id"], "catalog": job["bundle"].name, "persona": job["persona"]["id"],
            "source": job["persona"].get("source", "hand"), "tags": job["persona"].get("tags", []),
            "behavior": job["behavior"]["name"], "model": job["model"],
            "system": job["system"], "rep": job["rep"],
            **{k: v[k] for k in ("pass", "tier", "score", "violations", "failures", "tier_reasons", "booking", "quality")},
            "oracle": res["oracle"], "error": res["error"],
            "wall_s": round(time.perf_counter() - started, 1), **metrics(res["events"], job["model"]),
        }
        (out_dir / "transcripts" / f"{job['id']}.json").write_text(json.dumps(
            {"row": row, "persona": job["persona"], "events": res["events"], "bookings": res["bookings"],
             "anchor": job["anchor"].isoformat(), "clock": job["clock"].isoformat()}, indent=1, default=str))
        results.append(row)
        with (out_dir / "results.jsonl").open("a") as f:
            f.write(json.dumps(row, default=str) + "\n")
        progress(row)


async def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default="given", help="given | all | comma-separated bundle names")
    ap.add_argument("--models", default="gpt-4o-mini")
    ap.add_argument("--n", type=int, default=3, help="repeats per case")
    ap.add_argument("--cases", default="*", help="glob over persona ids")
    ap.add_argument("--behaviors", default="persona", help="persona (each persona's own) | all | name,name")
    ap.add_argument("--max-generated", type=int, default=12, help="generated personas per catalog (0 = hand-written only)")
    ap.add_argument("--systems", default="tools", help="tools | baseline (the naive full-catalog-in-prompt agent) | tools,baseline")
    ap.add_argument("--judge-model", default=judges.JUDGE_MODEL, help="the LLM that judges every conversation")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--max-turns", type=int, default=16)
    ap.add_argument("--label", default="")
    args = ap.parse_args(argv)

    names = list_bundles() if args.catalog == "all" else args.catalog.split(",")
    models = args.models.split(",")
    behaviors = load_behaviors()
    systems = args.systems.split(",")
    globs = args.cases.split(",")

    pool = await db.pool()
    async with pool.acquire() as conn:
        ingested = {r["name"]: r for r in await conn.fetch("SELECT name, catalog_version, calendar_anchor FROM catalogs")}

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + (f"-{args.label}" if args.label else "")
    out_dir = RUNS_DIR / run_id
    (out_dir / "transcripts").mkdir(parents=True, exist_ok=True)

    jobs = []
    for name in names:
        bundle = load_bundle(name)
        if name not in ingested or ingested[name]["catalog_version"] != bundle.version:
            raise SystemExit(f"Catalog '{name}' is not ingested at its current version: run `make ingest CATALOG={name}`.")
        cv, anchor = ingested[name]["catalog_version"], ingested[name]["calendar_anchor"]
        match = lambda p: any(fnmatch.fnmatch(p["id"], g) for g in globs)  # noqa: E731
        personas = list(filter(match, load_personas())) + list(filter(match, generate_personas(bundle)))[: args.max_generated]
        for persona in personas:
            if args.behaviors == "persona":
                bnames = persona.get("behaviors") or ["cooperative"]
            elif args.behaviors == "all":
                bnames = list(behaviors)
            else:
                bnames = args.behaviors.split(",")
            clock = datetime.combine(anchor, dtime(8)) + timedelta(hours=persona.get("clock_offset_hours", 0))
            for b in bnames:
                for model in models:
                    for system in systems:
                        for rep in range(args.n):
                            jid = f"{name}.{persona['id']}.{b}.{model}.{system}.{rep}"
                            jobs.append({
                                "id": jid, "persona": persona, "bundle": bundle, "cv": cv, "anchor": anchor,
                                "clock": clock, "behavior": behaviors[b], "model": model, "system": system,
                                "rep": rep, "scope": f"eval-{uuid.uuid4().hex[:12]}", "judge_model": args.judge_model,
                            })
    random.Random(0).shuffle(jobs)  # spread models and catalogs over time (rate limits)
    meta = {"run_id": run_id, "args": vars(args), "jobs": len(jobs), "catalogs": names, "models": models,
            "systems": systems, "judge_model": args.judge_model, "started": datetime.now().isoformat()}
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=1))
    print(f"run {run_id}: {len(jobs)} conversations -> {out_dir}")

    results: list = []
    done = {"n": 0}

    def progress(row):
        done["n"] += 1
        mark = "PASS" if row["pass"] else "FAIL"
        why = "; ".join(row["violations"] + row["failures"] + row["tier_reasons"])[:140]
        print(f"[{done['n']}/{len(jobs)}] {mark} {row['tier']:<10} {row['job']}  {why}", flush=True)

    sem = asyncio.Semaphore(args.concurrency)
    await asyncio.gather(*(run_job(j, sem, out_dir, args.max_turns, results, progress) for j in jobs))
    meta["finished"] = datetime.now().isoformat()
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=1))
    summary = write_summary(out_dir, results, meta)
    print(f"\nsummary: {summary}")
    await db.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
