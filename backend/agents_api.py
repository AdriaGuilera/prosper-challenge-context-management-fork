#
# Agents API — list / load / save agent JSON files for the builder UI.
#
# Agents live as plain files in backend/agents/<id>.json, so an agent is still
# "just data": the UI edits the same JSON a developer would, and a save is only
# accepted if AgentBuilder can compile it.
#

import json
import os
import re
from pathlib import Path

import aiohttp
from fastapi import APIRouter, HTTPException

from agent_builder import AgentBuilder
from agent_builder.tools import describe_tools

AGENTS_DIR = Path(__file__).parent / "agents"
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

router = APIRouter(prefix="/api")


def agent_path(agent_id: str) -> Path:
    if not _ID.match(agent_id):
        raise HTTPException(400, "Agent ids are lowercase letters, digits and dashes.")
    return AGENTS_DIR / f"{agent_id}.json"


def load_agent(agent_id: str) -> dict:
    path = agent_path(agent_id)
    if not path.exists():
        raise HTTPException(404, f"No agent '{agent_id}'.")
    return json.loads(path.read_text())


@router.get("/agents")
def list_agents() -> list:
    out = []
    for path in sorted(AGENTS_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text())
            out.append({"id": path.stem, "name": data.get("name", path.stem), "nodes": len(data.get("nodes", []))})
        except json.JSONDecodeError:
            continue
    return out


@router.get("/agents/{agent_id}")
def get_agent(agent_id: str) -> dict:
    return load_agent(agent_id)


@router.put("/agents/{agent_id}")
def save_agent(agent_id: str, agent: dict) -> dict:
    try:
        AgentBuilder.from_dict(agent)  # reject anything the runtime couldn't compile
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(422, f"Agent doesn't compile: {e}")
    agent_path(agent_id).write_text(json.dumps(agent, indent=2) + "\n")
    return {"id": agent_id}


@router.delete("/agents/{agent_id}")
def delete_agent(agent_id: str) -> dict:
    path = agent_path(agent_id)
    if path.exists():
        path.unlink()
    return {"ok": True}


@router.get("/tools")
def list_tools() -> list:
    return describe_tools()


# Models offered in the picker. Benchmarked on a spoken-reply + tool-call turn
# (time to first token, streaming, tools attached): all land around 1-2s, which
# a voice turn needs. GPT-5.6 models run with reasoning off for the same reason.
MODELS = [
    {"id": "gpt-4o", "label": "GPT-4o", "note": "Default. Proven on this agent"},
    {"id": "gpt-4o-mini", "label": "GPT-4o mini", "note": "Faster, cheaper"},
    {"id": "gpt-5.6-luna", "label": "GPT-5.6 Luna", "note": "Newest. Fast tier, made for conversation", "reasoning_effort": "none"},
    {"id": "gpt-5.6-terra", "label": "GPT-5.6 Terra", "note": "Newest. Balanced, stronger reasoning", "reasoning_effort": "none"},
]

def model_extra(model: str) -> dict:
    """Request params a model needs beyond its id (GPT-5 models reason unless told not to)."""
    for m in MODELS:
        if m["id"] == model and "reasoning_effort" in m:
            return {"reasoning_effort": m["reasoning_effort"]}
    return {}


# ElevenLabs stock voices, used when the API key can't list the account's
# voices (it needs the voices_read permission).
STOCK_VOICES = [
    ("21m00Tcm4TlvDq8ikWAM", "Rachel", "female · American"),
    ("EXAVITQu4vr4xnSDxMaL", "Sarah", "female · American"),
    ("FGY2WhTYpPnrIDTdsKH5", "Laura", "female · American"),
    ("cgSgspJ2msm6clMCkdW9", "Jessica", "female · American"),
    ("XrExE9yKIg1WjnnlVkGX", "Matilda", "female · American"),
    ("Xb7hH8MSUJpSbSDYk0k2", "Alice", "female · British"),
    ("pFZP5JQG7iQjIQuC4Bku", "Lily", "female · British"),
    ("XB0fDUnXU5powFXDhCwa", "Charlotte", "female · Swedish"),
    ("SAz9YHcvj6GT2YYXdXww", "River", "neutral · American"),
    ("pNInz6obpgDQGcFmaJgB", "Adam", "male · American"),
    ("nPczCjzI2devNBz1zQrb", "Brian", "male · American"),
    ("cjVigY5qzO86Huf0OWal", "Eric", "male · American"),
    ("iP95p4xoKVk53GoZ742B", "Chris", "male · American"),
    ("TX3LPaxmHKxFdv7VOQHJ", "Liam", "male · American"),
    ("bIHbv24MWmeRgasZH58o", "Will", "male · American"),
    ("JBFqnCBsd6RMkjVDRZzb", "George", "male · British"),
    ("onwK4e9ZLuTAKqWW03F9", "Daniel", "male · British"),
    ("N2lVS1w4EtoT3dr4eOWO", "Callum", "male · Transatlantic"),
    ("IKne3meq5aSn9XLyUdCD", "Charlie", "male · Australian"),
]

_voices_cache: list | None = None


async def _account_voices() -> list | None:
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        return None
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            async with session.get("https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": key}) as r:
                if r.status != 200:
                    return None
                data = await r.json()
    except (aiohttp.ClientError, TimeoutError):
        return None
    out = []
    for v in data.get("voices", []):
        labels = v.get("labels") or {}
        note = " · ".join(x for x in (labels.get("gender"), labels.get("accent")) if x)
        out.append({"id": v["voice_id"], "label": v["name"], "note": note or v.get("category", "")})
    return out or None


@router.get("/models")
def list_models() -> list:
    return [{"id": m["id"], "label": m["label"], "note": m["note"]} for m in MODELS]


@router.get("/voices")
async def list_voices() -> list:
    global _voices_cache
    if _voices_cache is None:
        _voices_cache = await _account_voices() or [{"id": i, "label": n, "note": d} for i, n, d in STOCK_VOICES]
    return _voices_cache
