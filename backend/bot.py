#
# Voice pipeline — Prosper AI Software Engineer Challenge
#
# The runnable voice agent: WebRTC transport + ElevenLabs STT/TTS + OpenAI LLM,
# driven by a Pipecat Flows node graph. This file is generic — it loads an agent
# definition (JSON) via AgentBuilder and runs it. Swapping the agent is a data
# change (edit/replace the JSON), not a code change.
#
#   agents/<id>.json  ->  AgentBuilder  ->  Pipecat Flows graph  ->  FlowManager
#
# The builder UI picks the agent per call (`/start` body: {"agent": "<id>"}) and
# receives node/edge/tool events back over the call's RTVI channel.
#
# Run:  python bot.py   then open http://localhost:7860 (builder) or /client
#

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.elevenlabs.stt import ElevenLabsRealtimeSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.workers.runner import WorkerRunner
from pipecat_flows import FlowManager

from agent_builder import AgentBuilder
from agents_api import AGENTS_DIR, agent_path, model_extra, router

# Load .env next to this file, so the bot runs the same from the repo root or backend/.
load_dotenv(Path(__file__).parent / ".env", override=True)


# The agent a call runs when the client doesn't name one (e.g. the prebuilt /client UI).
DEFAULT_AGENT = "clinic-scheduler"
FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"


transport_params = {
    "webrtc": lambda: TransportParams(audio_in_enabled=True, audio_out_enabled=True),
}


async def run_bot(
    transport: BaseTransport, runner_args: RunnerArguments, builder: AgentBuilder
) -> None:
    config = builder.config
    logger.info(f"Starting '{config.name}' with {len(config.nodes)} nodes")

    stt = ElevenLabsRealtimeSTTService(api_key=os.environ["ELEVENLABS_API_KEY"])
    tts = ElevenLabsTTSService(
        api_key=os.environ["ELEVENLABS_API_KEY"],
        settings=ElevenLabsTTSService.Settings(voice=config.voice_id),
    )
    llm = OpenAILLMService(
        api_key=os.environ["OPENAI_API_KEY"],
        settings=OpenAILLMService.Settings(model=config.model, extra=model_extra(config.model)),
    )

    context = LLMContext()
    context_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            context_aggregator.user(),
            llm,
            tts,
            transport.output(),
            context_aggregator.assistant(),
        ]
    )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        idle_timeout_secs=runner_args.pipeline_idle_timeout_secs,
    )

    # Stream graph events (node entered, edge taken, tool calls) to the builder UI.
    builder.set_event_sink(worker.rtvi.send_server_message)

    flow_manager = FlowManager(
        llm=llm,
        context_aggregator=context_aggregator,
        worker=worker,
        transport=transport,
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Client connected — starting flow at initial node")
        await flow_manager.initialize(builder.build_initial_node())
        await builder.announce_start()

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected")
        await worker.cancel()

    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)
    await runner.run()


async def bot(runner_args: RunnerArguments):
    """Entry point invoked by the Pipecat dev runner (and Pipecat Cloud)."""
    body = getattr(runner_args, "body", None) or {}
    agent_id = body.get("agent") or DEFAULT_AGENT
    builder = AgentBuilder.from_json(agent_path(agent_id))
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args, builder)


if __name__ == "__main__":
    from pipecat.runner.run import app, main

    # Registered before main() adds the runner's own routes, so "/" lands on the builder.
    app.include_router(router)
    if FRONTEND_DIST.exists():
        app.mount("/builder", StaticFiles(directory=FRONTEND_DIST, html=True), name="builder")

        @app.get("/", include_in_schema=False)
        async def builder_root():
            return RedirectResponse(url="/builder/")
    else:
        logger.warning(f"No built frontend at {FRONTEND_DIST}; run `make build` (or `make dev` for the Vite server).")
    logger.info(f"Agents directory: {AGENTS_DIR}")

    main()
