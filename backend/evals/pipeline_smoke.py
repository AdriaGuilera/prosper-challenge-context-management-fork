#
# Voice-pipeline smoke test without audio: the REAL Pipecat pipeline that bot.py builds
# (OpenAILLMService + LLMContextAggregatorPair + PipelineWorker + pipecat_flows.FlowManager +
# AgentBuilder over the Postgres tools), with only WebRTC/STT/TTS replaced by text: the
# caller simulator's words go in as user turns, the LLM's text frames come out.
#
#   python -m evals.pipeline_smoke [persona ...]      # default: happy-flu-shot chen-echo-downtown
#   SMOKE_MODEL=gpt-4o-mini python -m evals.pipeline_smoke   # override the agent JSON's model
#
# It then runs the same LLM judge as the text eval, so a PASS here is a judgement, not a rule check.
# What it proves beyond the text runner: FlowManager.initialize/transition semantics, the async tool handlers inside a real
# PipelineWorker, the context aggregators, and the builder's event sink, exactly as a call wires them.
#

import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime, time as dtime
from pathlib import Path

from dotenv import load_dotenv

BACKEND = Path(__file__).parent.parent
load_dotenv(BACKEND / ".env", override=True)

from pipecat.frames.frames import (  # noqa: E402
    EndFrame, LLMFullResponseEndFrame, LLMFullResponseStartFrame, LLMMessagesAppendFrame, LLMTextFrame,
)
from pipecat.pipeline.pipeline import Pipeline  # noqa: E402
from pipecat.pipeline.worker import PipelineParams, PipelineWorker  # noqa: E402
from pipecat.processors.aggregators.llm_context import LLMContext  # noqa: E402
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair  # noqa: E402
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor  # noqa: E402
from pipecat.services.openai.llm import OpenAILLMService  # noqa: E402
from pipecat.workers.runner import WorkerRunner  # noqa: E402
from pipecat_flows import FlowManager  # noqa: E402

from agent_builder import AgentBuilder  # noqa: E402
from agent_builder.tools import ToolContext  # noqa: E402
from agents_api import model_extra  # noqa: E402
from store import db  # noqa: E402
from store.bundle import load_bundle  # noqa: E402

from .judge import judge  # noqa: E402
from .oracle import compute_oracle  # noqa: E402
from .personas import load_behaviors, load_for_simulator, load_personas  # noqa: E402
from .run import collect_bookings, seed_patient  # noqa: E402
from .simulator import CallerSimulator  # noqa: E402


class Mouth(FrameProcessor):
    """Stands where TTS would: collects what the agent says, one response at a time."""

    def __init__(self):
        super().__init__()
        self.buffer, self.responses, self.last_activity = [], asyncio.Queue(), time.monotonic()

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, (LLMFullResponseStartFrame, LLMTextFrame, LLMFullResponseEndFrame)):
            self.last_activity = time.monotonic()
        if isinstance(frame, LLMTextFrame):
            self.buffer.append(frame.text)
        elif isinstance(frame, LLMFullResponseEndFrame) and self.buffer:
            await self.responses.put("".join(self.buffer).strip())
            self.buffer = []
        await self.push_frame(frame, direction)

    async def next_turn(self, settle: float = 1.5, timeout: float = 90) -> str:
        """The agent's full turn: responses until the pipeline goes quiet (tool calls run in between)."""
        parts = [await asyncio.wait_for(self.responses.get(), timeout)]
        while True:
            try:
                parts.append(await asyncio.wait_for(self.responses.get(), settle))
            except asyncio.TimeoutError:
                if time.monotonic() - self.last_activity >= settle:
                    return " ".join(parts)


async def call(persona: dict, model: str = None, max_turns: int = 14) -> dict:
    pool = await db.pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT catalog_version, calendar_anchor FROM catalogs WHERE name = 'given'")
    bundle = load_bundle("given")
    anchor = row["calendar_anchor"]
    clock = datetime.combine(anchor, dtime(8))
    scope = f"smoke-{uuid.uuid4().hex[:10]}"
    events: list = []
    t0 = time.perf_counter()

    def log(e):
        events.append({"t": round(time.perf_counter() - t0, 3), **e})

    async def sink(e):
        log(dict(e))

    config = json.loads((BACKEND / "agents" / "clinic-scheduler.json").read_text())
    model = model or config["model"]   # the model the demo agent is configured with
    config["model"] = model
    builder = AgentBuilder.from_dict(config)
    builder.ctx = ToolContext(catalog="given", scope=scope, clock=lambda: clock)

    # ---- the same wiring as bot.run_bot, minus transport/STT/TTS ----
    llm = OpenAILLMService(api_key=os.environ["OPENAI_API_KEY"],
                           settings=OpenAILLMService.Settings(model=model, extra=model_extra(model)))
    context = LLMContext()
    aggregators = LLMContextAggregatorPair(context)
    mouth = Mouth()
    pipeline = Pipeline([aggregators.user(), llm, mouth, aggregators.assistant()])
    worker = PipelineWorker(pipeline, params=PipelineParams(enable_metrics=True, enable_usage_metrics=True))
    builder.set_event_sink(sink)
    flow_manager = FlowManager(llm=llm, context_aggregator=aggregators, worker=worker)

    async with pool.acquire() as conn:
        await seed_patient(conn, row["catalog_version"], scope, persona, bundle)
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    run = asyncio.create_task(runner.run())
    try:
        await asyncio.sleep(0.5)
        await builder.prepare()
        await flow_manager.initialize(builder.build_initial_node())
        await builder.announce_start()
        text = await mouth.next_turn()
        log({"type": "agent", "text": text, "node": flow_manager.current_node})
        sim = CallerSimulator(load_for_simulator(persona, load_behaviors()[persona["behaviors"][0]]), log)
        for _ in range(max_turns):
            turn = await sim.reply(text)
            log({"type": "caller", "text": turn["say"], "act": turn["act"]})
            await worker.queue_frames([LLMMessagesAppendFrame([{"role": "user", "content": turn["say"]}], run_llm=True)])
            text = await mouth.next_turn()
            log({"type": "agent", "text": text, "node": flow_manager.current_node})
            if turn["act"] == "goodbye" or flow_manager.current_node == "goodbye":
                break
    finally:
        await worker.queue_frames([EndFrame()])
        try:
            await asyncio.wait_for(run, 10)
        except asyncio.TimeoutError:
            await worker.cancel()
    async with pool.acquire() as conn:
        bookings = await collect_bookings(conn, row["catalog_version"], scope)
    verdict = await judge(compute_oracle(persona, bundle, anchor, clock), events, bookings, bundle, persona)
    return {"events": events, "verdict": verdict, "bookings": bookings}


async def main(argv) -> None:
    ids = argv or ["happy-flu-shot", "chen-echo-downtown"]
    personas = {p["id"]: p for p in load_personas()}
    failed = 0
    for pid in ids:
        res = await call(personas[pid], os.environ.get("SMOKE_MODEL"))
        v = res["verdict"]
        tools = [e["tool"] for e in res["events"] if e["type"] == "tool_finished"]
        nodes = [e["node"] for e in res["events"] if e["type"] == "node_entered"]
        print(f"\n== {pid}: {'PASS' if v['pass'] else 'FAIL'} {v['tier']} {v['violations'] + v['failures'] + v['tier_reasons']}")
        print(f"   nodes: {' -> '.join(nodes)}")
        print(f"   tools: {', '.join(tools)}")
        for e in res["events"]:
            if e["type"] in ("agent", "caller"):
                print(f"   {'AGENT' if e['type'] == 'agent' else 'CALLER'}: {e['text'][:160]}")
        failed += not v["pass"]
    await db.close()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
