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


def _cases():
    for name in list_bundles():
        bundle = load_bundle(name)
        for p in load_personas() + generate_personas(bundle):
            yield bundle, p


@pytest.mark.parametrize("bundle,persona", list(_cases()), ids=lambda x: getattr(x, "name", None) or x["id"])
def test_no_oracle_leak(bundle, persona):
    behaviors = load_behaviors()
    caller_text = json.dumps({k: persona["caller"].get(k) for k in ("goal", "opening", "preferences", "answers", "knows")}).lower()
    oracle = compute_oracle(persona, bundle, bundle.anchor, datetime.combine(bundle.anchor, datetime.min.time()).replace(hour=8))
    for b in persona.get("behaviors", ["cooperative"]):
        view = load_for_simulator(persona, behaviors[b])
        prompt = simulator_prompt(view)
        low = prompt.lower()
        assert not ID_RE.search(json.dumps(view) + prompt), "catalog id in the simulator payload"
        assert not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", prompt), "a slot time reached the simulator"
        if oracle.soonest:
            for fmt in ("%-I:%M", "%-I%p", "%-I:%M%p", "%H:%M"):
                token = oracle.soonest.strftime(fmt).lower()
                assert token not in low or token in caller_text, f"the oracle's soonest slot ({token}) leaked"
        # every catalog fact the oracle derived must come from the caller's own words, if present at all
        facts = [bundle.locations[l]["name"] for l in oracle.best_locations]
        facts += [bundle.providers[p]["name"].replace("Dr. ", "") for p in oracle.provider_match]
        for item in oracle.info:
            facts += item.get("locations", []) + item.get("open_locations", []) + item.get("providers", [])
        for fact in facts:
            assert fact.lower() not in low or fact.lower() in caller_text, f"oracle fact '{fact}' leaked"
        for st in oracle.must_state:
            phrase = st.replace("_", " ")
            if not re.search(rf"\b{phrase}", caller_text):
                assert not re.search(rf"\b{phrase}\b", low), f"the expected statement '{st}' leaked"
        # registry state the caller doesn't know about stays out
        for r in persona["caller"].get("record", {}).get("referrals", []):
            name = (r if isinstance(r, str) else r["type"]).lower()
            if name not in caller_text:
                assert name not in low, f"referral '{name}' leaked from the registry"
