#
# Monitoring — every call is traced with OpenTelemetry and exported to Langfuse.
#
#   conversation (one call)  ->  turns  ->  STT / LLM / TTS spans (Pipecat)
#                                       ->  tool and edge spans (AgentBuilder)
#
# Langfuse groups a call's trace under its session id (= the call's conversation id),
# which bot.py also writes in its log lines. Tracing is off when the keys are missing.
#

import base64
import os
from functools import cache

from loguru import logger


@cache
def setup_tracing() -> bool:
    """Install the Langfuse OTLP exporter once per process; True when calls will be traced."""
    public, secret = os.environ.get("LANGFUSE_PUBLIC_KEY"), os.environ.get("LANGFUSE_SECRET_KEY")
    if not (public and secret):
        logger.warning("LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set: calls are not traced")
        return False

    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from pipecat.utils.tracing.setup import setup_tracing as pipecat_setup_tracing

    base = os.environ.get("LANGFUSE_BASE_URL", "https://cloud.langfuse.com").rstrip("/")
    token = base64.b64encode(f"{public}:{secret}".encode()).decode()
    exporter = OTLPSpanExporter(
        endpoint=f"{base}/api/public/otel/v1/traces",
        headers={"Authorization": f"Basic {token}"},
    )
    enabled = pipecat_setup_tracing("prosper-voice-agent", exporter=exporter)
    logger.info(f"Tracing calls to Langfuse at {base}" if enabled else "Tracing setup failed")
    return enabled
