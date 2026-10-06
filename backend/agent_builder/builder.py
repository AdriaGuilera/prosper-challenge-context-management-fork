#
# AgentBuilder — loads a declarative agent (JSON / dict) and compiles its node
# graph into Pipecat Flows objects.
#
#   JSON  ->  AgentConfig (validated)  ->  Pipecat Flows NodeConfig graph
#
# This is the seam between "agent as data" (what the Phase 2 Copilot produces)
# and "agent as a running conversation" (what bot.py executes). Keeping the
# compile + validation here means bot.py never touches the graph internals.
#

import json
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional, Union

from loguru import logger
from pipecat_flows import FlowManager, FlowsFunctionSchema, NodeConfig

from .schema import AgentConfig, Edge, Node
from .tools import TOOLS, ToolContext, result_size, run_tool, schema_properties

# Call events for the builder UI: node_entered, edge_taken, tool_started, tool_finished.
EventSink = Callable[[dict], Awaitable[None]]


class AgentBuilder:
    """Builds a runnable Pipecat Flows graph from a declarative AgentConfig."""

    def __init__(self, config: AgentConfig, emit: Optional[EventSink] = None, ctx: Optional[ToolContext] = None):
        self.config = config
        self._nodes_by_name = {n.name: n for n in config.nodes}
        self._emit = emit
        # Per-call tool state: catalog, calendar scope, clock, and what the call has established.
        self.ctx = ctx or ToolContext()
        self._validate()

    async def prepare(self) -> None:
        """Resolve the catalog version and its enums before the first node is built."""
        await self.ctx.prepare()

    def set_event_sink(self, emit: EventSink) -> None:
        self._emit = emit

    async def _event(self, kind: str, **data) -> None:
        if self._emit:
            await self._emit({"type": kind, **data})

    # ---- loading -----------------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict) -> "AgentBuilder":
        return cls(AgentConfig.from_dict(data))

    @classmethod
    def from_json(cls, path: Union[str, Path]) -> "AgentBuilder":
        data = json.loads(Path(path).read_text())
        return cls.from_dict(data)

    # ---- validation --------------------------------------------------------
    def _validate(self) -> None:
        names = set(self._nodes_by_name)
        if not names:
            raise ValueError("Agent has no nodes.")
        if self.config.initial_node not in names:
            raise ValueError(
                f"initial_node '{self.config.initial_node}' is not a defined node."
            )
        for node in self.config.nodes:
            for edge in node.edges:
                if edge.target not in names:
                    raise ValueError(
                        f"Edge '{edge.function}' in node '{node.name}' targets "
                        f"unknown node '{edge.target}'."
                    )
            for tool in node.tools:
                if tool not in TOOLS:
                    raise ValueError(f"Node '{node.name}' uses unknown tool '{tool}'.")

    # ---- compilation -------------------------------------------------------
    def build_initial_node(self) -> NodeConfig:
        """Return the entry NodeConfig; downstream nodes are built lazily on transition."""
        return self._make_node(self._nodes_by_name[self.config.initial_node])

    async def announce_start(self) -> None:
        await self._event("node_entered", node=self.config.initial_node)

    def _make_node(self, node: Node) -> NodeConfig:
        node_config: NodeConfig = {
            "name": node.name,
            "role_message": node.role_message or self.config.persona,
            "task_messages": node.task_messages,
            "functions": [self._make_edge_function(node, edge) for edge in node.edges]
            + [self._make_tool_function(node, name) for name in node.tools],
        }
        if node.pre_actions:
            node_config["pre_actions"] = node.pre_actions
        # Explicit post_actions win; otherwise a terminal node ends the call.
        if node.post_actions:
            node_config["post_actions"] = node.post_actions
        elif node.end:
            node_config["post_actions"] = [{"type": "end_conversation"}]
        return node_config

    def _make_edge_function(self, node: Node, edge: Edge) -> FlowsFunctionSchema:
        async def handler(args: dict, flow_manager: FlowManager):
            # Persist what the caller gave us so later nodes can use it.
            flow_manager.state.update(args)
            logger.info(f"[{edge.function}] -> {edge.target} | collected: {args}")
            await self._event("edge_taken", source=node.name, target=edge.target, function=edge.function, args=args)
            await self._event("node_entered", node=edge.target)
            next_node = self._make_node(self._nodes_by_name[edge.target])
            return {"status": "success", **args}, next_node

        return FlowsFunctionSchema(
            name=edge.function,
            description=edge.description,
            properties=edge.properties,
            required=edge.required,
            handler=handler,
        )

    def _make_tool_function(self, node: Node, name: str) -> FlowsFunctionSchema:
        tool = TOOLS[name]

        async def handler(args: dict, flow_manager: FlowManager):
            call_id = f"{name}-{time.monotonic_ns()}"
            await self._event("tool_started", id=call_id, node=node.name, tool=name, args=args)
            started = time.perf_counter()
            try:
                result = await run_tool(name, args, self.ctx)
            except Exception as e:  # a broken lookup must not end the call
                logger.exception(f"[{name}] failed")
                result = {"error": str(e)}
            ms = round((time.perf_counter() - started) * 1000, 2)
            logger.info(f"[{name}] {ms}ms | args: {args}")
            await self._event(
                "tool_finished",
                id=call_id,
                node=node.name,
                tool=name,
                ms=ms,
                rows=result_size(result),
                ok="error" not in result and result.get("booked", True) is not False,
                result=result,
            )
            return result, None  # stay on this node

        return FlowsFunctionSchema(
            name=tool.name,
            description=tool.description,
            properties=schema_properties(tool, self.ctx),
            required=tool.required,
            handler=handler,
        )
