#
# Leak tripwire (persona-design §1): the simulator's briefing must carry nothing the
# caller couldn't know. Checked for every persona, hand-written and generated, on
# every catalog bundle on disk.
#

import json
import re
from datetime import date, datetime

import pytest

from evals.generators import generate_personas
from evals.oracle import compute_oracle
from evals.personas import load_behaviors, load_for_simulator, load_personas, simulator_prompt
from store.bundle import list_bundles, load_bundle

ID_RE = re.compile(r"\b(prov|loc|appt|pat|sched)_\w+")
ORACLE_KEYS = ("expect", "ideal", "acceptable", "suboptimal", "must_ask", "must_state", "oracle", "record", "referrals")


def _cases():
    for name in list_bundles():
        bundle = load_bundle(name)
        for p in load_personas() + generate_personas(bundle):
            yield bundle, p


@pytest.mark.parametrize("bundle,persona", list(_cases()), ids=lambda x: getattr(x, "name", None) or x["id"])
def test_no_oracle_leak(bundle, persona):
    behaviors = load_behaviors()
    for b in persona.get("behaviors", ["cooperative"]):
        view = load_for_simulator(persona, behaviors[b])
        prompt = simulator_prompt(view)
        blob = json.dumps(view) + prompt
        assert not ID_RE.search(blob), "catalog id in the simulator payload"
        for key in ORACLE_KEYS:
            assert f'"{key}"' not in json.dumps(view), f"oracle key '{key}' reached the simulator"
        assert not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", blob), "a slot time reached the simulator"
        oracle = compute_oracle(persona, bundle, date(2026, 10, 12), datetime(2026, 10, 12, 8))
        if oracle.soonest:
            assert oracle.soonest.strftime("%-I:%M") not in prompt or oracle.soonest.minute == 0
        # a location the oracle expects must come from the caller's own words, never from the oracle
        caller_text = json.dumps({k: persona["caller"].get(k) for k in ("goal", "opening", "preferences", "answers")})
        for loc_id in oracle.best_locations:
            name = bundle.locations[loc_id]["name"]
            assert name not in prompt or name in caller_text, f"oracle location '{name}' leaked"
        # registry state the caller doesn't know about stays out
        for r in persona["caller"].get("record", {}).get("referrals", []):
            name = r if isinstance(r, str) else r["type"]
            if name.lower() not in caller_text.lower():
                assert name not in prompt, f"referral '{name}' leaked from the registry"
