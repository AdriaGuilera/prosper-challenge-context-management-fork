#
# Text runner: drives the REAL AgentBuilder node graph with Pipecat Flows semantics,
# minus audio. What it reproduces from pipecat_flows.FlowManager (v1.4):
#
#   role_message   -> the LLM's system instruction (LLMUpdateSettingsFrame), persists until changed
#   task_messages  -> appended to the context on every node entry (ContextStrategy.APPEND)
#   functions      -> the tool set is swapped per node (LLMSetToolsFrame)
#   edge handler   -> returns (result, next NodeConfig): result goes into context, then transition
#   tool handler   -> returns (result, None): result goes into context, LLM runs again
#   end node       -> post_actions end_conversation: the call ends after the agent's reply
#
# The handlers are the builder's own closures, so tool calls, edge state and the
# builder's events are exactly what a voice call would produce.
#

import json
import time
from types import SimpleNamespace

from agents_api import model_extra

from . import llm

MAX_STEPS_PER_TURN = 10


class FlowRunner:
    def __init__(self, builder, model: str, log):
        self.builder = builder
        self.model = model
        self.log = log
        self.fm = SimpleNamespace(state={})          # what handlers see as flow_manager
        self.system = ""
        self.messages: list = []
        self.functions: dict = {}
        self.node = None
        self.ending = False
        self.ended = False

    def enter(self, node_cfg: dict) -> None:
        self.node = node_cfg["name"]
        if node_cfg.get("role_message"):
            self.system = node_cfg["role_message"]
            self.log({"type": "context", "text": self.system})
        self.messages += [dict(m) for m in node_cfg.get("task_messages", [])]
        self.functions = {f.name: f for f in node_cfg.get("functions", [])}
        self.ending = any(a.get("type") == "end_conversation" for a in node_cfg.get("post_actions", []))

    def _tools(self) -> list:
        return [{"type": "function", "function": {
            "name": f.name, "description": f.description,
            "parameters": {"type": "object", "properties": f.properties, "required": f.required}}}
            for f in self.functions.values()]

    async def start(self) -> str:
        await self.builder.prepare()
        self.enter(self.builder.build_initial_node())
        await self.builder.announce_start()
        return await self.agent_turn()

    async def caller_says(self, text: str) -> str:
        self.messages.append({"role": "user", "content": text})
        return await self.agent_turn()

    async def agent_turn(self) -> str:
        started = time.perf_counter()
        for _ in range(MAX_STEPS_PER_TURN):
            tools = {"tools": self._tools()} if self.functions else {}
            msg = await llm.chat(self.model, [{"role": "system", "content": self.system}] + self.messages,
                                 meter=self.log, **tools, **model_extra(self.model))
            if msg.tool_calls:
                self.messages.append({
                    "role": "assistant", "content": msg.content,
                    "tool_calls": [{"id": tc.id, "type": "function",
                                    "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                                   for tc in msg.tool_calls]})
                next_node = None
                for tc in msg.tool_calls:
                    f = self.functions.get(tc.function.name)
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    if f is None:
                        result = {"error": f"'{tc.function.name}' is not available in this step."}
                        self.log({"type": "bad_function", "name": tc.function.name, "node": self.node})
                    else:
                        result, nxt = await f.handler(args, self.fm)
                        next_node = nxt or next_node
                    self.messages.append({"role": "tool", "tool_call_id": tc.id,
                                          "content": json.dumps(result, default=str)})
                if next_node:
                    self.enter(next_node)
                continue
            text = (msg.content or "").strip()
            self.messages.append({"role": "assistant", "content": text})
            self.log({"type": "agent_turn_ms", "ms": round((time.perf_counter() - started) * 1000, 1)})
            if self.ending:
                self.ended = True
            return text
        self.log({"type": "stuck", "node": self.node})
        return ""
