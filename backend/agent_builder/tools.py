#
# Catalog tools: the lookups a node can expose to the LLM, by name.
#
# A node lists tool names in its `tools` field; AgentBuilder turns each into a
# FlowsFunctionSchema that answers WITHOUT a transition. A tool is a name, a
# description, its arguments (a pydantic model in tool_schemas) and the function
# that runs it (tool_functions). This module is the registry and the dispatch:
# check the arguments, then run.
#

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional, Type

from pydantic import BaseModel, ValidationError

from store import db
from store.bundle import normalize

from . import tool_functions as fn
from . import tool_schemas as s
from .tool_functions import MAX_RESULTS, ToolContext

__all__ = [
    "TOOLS",
    "Tool",
    "ToolContext",
    "describe_tools",
    "result_size",
    "run_tool",
    "schema_properties",
]


@dataclass
class Tool:
    name: str
    description: str
    args: Type[BaseModel]
    run: Callable[[BaseModel, ToolContext, object], Awaitable[dict]]
    enums: dict = field(
        default_factory=dict
    )  # property -> ctx.enums key, filled per catalog
    hidden: bool = False  # not offered in the builder UI (eval baseline only)

    @property
    def required(self) -> list:
        return [k for k, f in self.args.model_fields.items() if f.is_required()]


TOOLS = {
    t.name: t
    for t in [
        Tool(
            "lookup_patient",
            "Look the caller up by full name and date of birth. Tells you whether they are an existing or new patient "
            "and which referrals are on file. Do this before checking availability or booking.",
            s.LookupPatientArgs,
            fn.lookup_patient,
        ),
        Tool(
            "search_appointment_types",
            "Match what the caller wants to the clinic's appointment types, using the caller's own words. Returns the "
            "best candidates with the facts that tell them apart; when two are easily confused it returns status 'ask' "
            "with the ones to ask the caller about.",
            s.SearchAppointmentTypesArgs,
            fn.search_appointment_types,
            enums={"specialty": "specialty"},
        ),
        Tool(
            "find_providers",
            "Find providers by name, specialty, appointment type, location or language. Names are not unique: when "
            "several match it returns status 'ask' and what tells them apart. Includes counts by specialty.",
            s.FindProvidersArgs,
            fn.find_providers,
            enums={"specialty": "specialty", "language": "language"},
        ),
        Tool(
            "find_locations",
            "Clinic locations with address, phone, opening hours and services. Filter by name, a day they're open, "
            "or rank by distance from a place the caller names (a neighbourhood, street or landmark). To find where "
            "a particular visit can be done, pass its appointment_type_id (look it up first): not every location "
            "offers every visit.",
            s.FindLocationsArgs,
            fn.find_locations,
        ),
        Tool(
            "check_availability",
            "Open slots for an appointment type: soonest first, or nearest first when 'near' is given. Optionally "
            "narrowed to a provider and/or location. Applies the patient's referral and new-patient rules. Only "
            "returns provider-location pairs that can actually host the type. Offer only times it returns.",
            s.CheckAvailabilityArgs,
            fn.check_availability,
        ),
        Tool(
            "list_slots",
            "Raw calendar (naive baseline only): next free times for a provider at a location, for a type's duration. "
            "Does NOT check any clinic rule.",
            s.ListSlotsArgs,
            fn.list_slots,
            hidden=True,
        ),
        Tool(
            "book_appointment",
            "Book a slot the caller explicitly confirmed. Pass the slot's 'start' exactly as check_availability "
            "returned it. Every clinic rule is re-checked; a rejection comes back with plain reasons.",
            s.BookAppointmentArgs,
            fn.book_appointment,
        ),
    ]
}


def _plain(prop: dict) -> dict:
    """A pydantic property as a plain JSON-schema one: Optional[x] is just x (not required
    says it may be left out), and no titles or null defaults."""
    for variant in prop.pop("anyOf", []):
        if variant.get("type") != "null":
            prop.update(variant)
    prop.pop("title", None)
    if prop.get("default", ...) is None:
        del prop["default"]
    return prop


def schema_properties(tool: Tool, ctx: Optional[ToolContext]) -> dict:
    """The tool's JSON-schema properties, with this catalog's finite vocabularies as enums."""
    props = {
        k: _plain(v) for k, v in tool.args.model_json_schema()["properties"].items()
    }
    if ctx is not None:
        for prop, key in tool.enums.items():
            if ctx.enums.get(key):
                props[prop]["enum"] = ctx.enums[key]
    return props


_ENUM_ARGS = {
    "specialty": "specialty",
    "language": "language",
}


def _parse(tool: Tool, args: dict) -> tuple:
    """(the tool's args model, error dict or None)."""
    try:
        return tool.args.model_validate(args), None
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(map(str, err['loc'])) or 'arguments'}: {err['msg']}"
            for err in e.errors()
        )
        return None, {"error": f"Bad arguments for {tool.name}: {problems}."}


async def _validate(args: dict, ctx: ToolContext, conn) -> tuple:
    """Check ids and enum values before a tool runs. An unknown value never becomes a silent
    empty result (the model would read that as "we have none"). A name passed where an id
    belongs is resolved when it is unambiguous; otherwise the error says how to fix the call.
    Returns (args, error dict or None, notes)."""
    args = {k: v for k, v in args.items() if v not in (None, "")}
    notes = []
    for key, enum in _ENUM_ARGS.items():
        if key in args:
            allowed = ctx.enums.get(enum, [])
            match = next(
                (a for a in allowed if a.lower() == str(args[key]).lower()), None
            )
            if match is None:
                return (
                    args,
                    {
                        "error": f"{key} '{args[key]}' is not one of: {', '.join(allowed)}."
                    },
                    notes,
                )
            args[key] = match
    if "appointment_type_id" in args:
        tid = args["appointment_type_id"]
        if not await conn.fetchval(
            "SELECT 1 FROM appointment_types WHERE catalog = $1 AND id = $2",
            ctx.catalog,
            tid,
        ):
            rows = await conn.fetch(
                "SELECT id FROM appointment_types WHERE catalog = $1 AND lower(name) = lower($2)",
                ctx.catalog,
                str(tid),
            )
            if len(rows) != 1:
                return (
                    args,
                    {
                        "error": f"appointment_type_id '{tid}' is not an id. Use an appointment_type_id from search_appointment_types."
                    },
                    notes,
                )
            args["appointment_type_id"] = rows[0]["id"]
            notes.append(f"read appointment type '{tid}' as {rows[0]['id']}")
    if "provider_id" in args:
        pid = args["provider_id"]
        if not await conn.fetchval(
            "SELECT 1 FROM providers WHERE catalog = $1 AND id = $2", ctx.catalog, pid
        ):
            q = fn._HONORIFIC.sub("", normalize(str(pid)))
            rows = await conn.fetch(
                "SELECT id, name FROM providers WHERE catalog = $1 AND name_score($2, search_name) >= 0.8",
                ctx.catalog,
                q,
            )
            if len(rows) != 1:
                err = {
                    "error": f"provider_id '{pid}' is not an id"
                    + (f" and that name fits {len(rows)} providers" if rows else "")
                    + ". Look the provider up with find_providers (and ask which one if several match)."
                }
                if len(rows) > 1:
                    err.update(
                        {
                            "status": "ask",
                            "ambiguous_arg": "provider_id",
                            "candidates": [r["name"] for r in rows][:MAX_RESULTS],
                        }
                    )
                return args, err, notes
            args["provider_id"] = rows[0]["id"]
            notes.append(f"read provider '{pid}' as {rows[0]['name']}")
    if "location_id" in args:
        lid = args["location_id"]
        if not await conn.fetchval(
            "SELECT 1 FROM locations WHERE catalog = $1 AND id = $2", ctx.catalog, lid
        ):
            q = normalize(str(lid))
            rows = await conn.fetch(
                """SELECT id, name FROM locations WHERE catalog = $1
                     AND (position($2 IN lower(name)) > 0 OR lower(coalesce(place, '')) = $2)""",
                ctx.catalog,
                q,
            )
            if len(rows) != 1:
                err = {
                    "error": f"location_id '{lid}' is not an id. Use a location_id from find_locations."
                }
                if len(rows) > 1:
                    err.update(
                        {
                            "status": "ask",
                            "ambiguous_arg": "location_id",
                            "candidates": [r["name"] for r in rows],
                        }
                    )
                return args, err, notes
            args["location_id"] = rows[0]["id"]
            notes.append(f"read location '{lid}' as {rows[0]['name']}")
    return args, None, notes


async def run_tool(name: str, args: dict, ctx: ToolContext) -> dict:
    await ctx.prepare()
    tool = TOOLS[name]
    pool = await db.pool()
    async with pool.acquire() as conn:
        args, error, notes = await _validate(args, ctx, conn)
        if error:
            return error
        parsed, error = _parse(tool, args)
        if error:
            return error
        result = await tool.run(parsed, ctx, conn)
        if notes and isinstance(result, dict):
            result["resolved"] = notes
        return result


# ------- FUNCTIONS FOR THE FRONTEND -------


def describe_tools() -> list:
    """Tool catalog for the builder UI."""
    return [
        {
            "name": t.name,
            "description": t.description,
            "parameters": list(t.args.model_fields),
        }
        for t in TOOLS.values()
        if not t.hidden
    ]


def result_size(result: dict) -> Optional[int]:
    if "shown" in result:
        return result["shown"]
    return None
