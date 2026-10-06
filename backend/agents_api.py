#
# Agents API — list / load / save agent JSON files for the builder UI.
#
# Agents live as plain files in backend/agents/<id>.json, so an agent is still
# "just data": the UI edits the same JSON a developer would, and a save is only
# accepted if AgentBuilder can compile it.
#

import json
import re
from pathlib import Path

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
