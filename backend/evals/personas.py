#
# Personas: the simulated caller, in catalog-independent terms.
#
# A persona file holds three things:
#   caller    what the caller knows about themselves: name, date of birth, goal,
#             preferences, how they answer questions. Plus `record`, the registry state
#             we seed for them (existing patient? referrals?), which the caller does
#             NOT see unless their goal says so.
#   behaviour a profile name, crossed with goals (eval/behaviors/*.json)
#   expect    the outcome spec in names, never ids: type names, surnames, places.
#             The oracle turns it into ground truth for one catalog (evals/oracle.py).
#
# `load_for_simulator` is the ONLY function that builds the simulator's view.
#

import json
from pathlib import Path

EVAL_DIR = Path(__file__).parent.parent.parent / "eval"
PERSONA_DIR = EVAL_DIR / "personas"
HIDDEN_DIR = EVAL_DIR / "personas-hidden"   # written by evals/hidden.py; never used to tune the agent
BEHAVIOR_DIR = EVAL_DIR / "behaviors"

# Keys the simulator may see from `caller`. Everything else stays with the judge.
CALLER_VISIBLE = ("name", "dob", "goal", "opening", "preferences", "answers", "knows")


def load_personas(which: str = "dev") -> list:
    """The hand-written personas ("dev") or the hidden test set ("hidden")."""
    folder = HIDDEN_DIR if which == "hidden" else PERSONA_DIR
    return [json.loads(p.read_text()) for p in sorted(folder.glob("*.json"))]


def load_behaviors() -> dict:
    behaviors = [json.loads(p.read_text()) for p in sorted(BEHAVIOR_DIR.glob("*.json"))]
    return {b["name"]: b for b in behaviors}


def load_for_simulator(persona: dict, behavior: dict) -> dict:
    """The caller's briefing: self-knowledge and behaviour, nothing about the clinic."""
    caller = persona["caller"]
    view = {k: caller[k] for k in CALLER_VISIBLE if k in caller}
    view["been_here_before"] = bool(caller.get("record", {}).get("existing"))
    view["behaviour"] = behavior["instructions"]
    return view


def simulator_prompt(view: dict) -> str:
    lines = [
        "You are role-playing a person phoning a medical clinic's scheduling line. Stay in character.",
        f"Your name: {view['name']}. Your date of birth: {view['dob']} (say it naturally when asked).",
        "You have been a patient at this clinic before." if view["been_here_before"]
        else "You have never been a patient at this clinic.",
        f"What you want from this call: {view['goal']}",
    ]
    if view.get("opening"):
        lines.append(f"Open the call with something like: \"{view['opening']}\"")
    if view.get("preferences"):
        prefs = "; ".join(f"{k}: {v}" for k, v in view["preferences"].items())
        lines.append(f"Your preferences (mention them when relevant or when asked): {prefs}")
    if view.get("answers"):
        ans = "; ".join(f"if asked about {k}: \"{v}\"" for k, v in view["answers"].items())
        lines.append(f"How you answer questions: {ans}")
    if view.get("knows"):
        lines.append("Other things you know: " + "; ".join(view["knows"]))
    lines += [
        f"How you behave: {view['behaviour']}",
        "Rules: you only know what's written above about yourself. You do not know the clinic's doctors, "
        "locations, appointment type names or schedules beyond what the assistant tells you. Never make up "
        "medical facts about yourself beyond the above. Speak like a person on the phone: short, natural, no lists.",
        "Before agreeing to a booking, make sure the details match what you want. Say yes clearly only when they do.",
        "When your goal is met (booked and you have the confirmation, or your question answered, or it can't be "
        "done), say goodbye.",
        "Reply ONLY with JSON: {\"say\": \"<your words>\", \"act\": \"<one of: inform, answer, confirm, decline, "
        "ask, goodbye>\"}. Use act=confirm only when you are explicitly agreeing to book the exact appointment just "
        "read back to you.",
    ]
    return "\n".join(lines)
