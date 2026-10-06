#
# The caller simulator. It sees only the briefing built by personas.load_for_simulator
# and the agent's words; it tags each turn with an act so the judge can check
# "nothing booked without an explicit yes" without parsing free text.
#

import json

from . import llm
from .personas import simulator_prompt

SIM_MODEL = "gpt-4o"          # fixed across agent models, or the comparison is confounded
SIM_TEMPERATURE = 0.6
ACTS = {"inform", "answer", "confirm", "decline", "ask", "goodbye"}


class CallerSimulator:
    def __init__(self, view: dict, log, model: str = SIM_MODEL):
        self.system = simulator_prompt(view)
        self.history: list = []
        self.log = log
        self.model = model

    async def reply(self, agent_text: str) -> dict:
        self.history.append({"role": "user", "content": agent_text or "(silence)"})
        msg = await llm.chat(self.model, [{"role": "system", "content": self.system}] + self.history,
                             meter=self.log, role="simulator", temperature=SIM_TEMPERATURE,
                             response_format={"type": "json_object"})
        try:
            out = json.loads(msg.content)
        except (json.JSONDecodeError, TypeError):
            out = {"say": msg.content or "", "act": "inform"}
        say = str(out.get("say", "")).strip()
        act = out.get("act") if out.get("act") in ACTS else "inform"
        self.history.append({"role": "assistant", "content": json.dumps({"say": say, "act": act})})
        return {"say": say, "act": act}
