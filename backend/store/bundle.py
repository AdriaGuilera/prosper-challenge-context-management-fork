#
# A catalog bundle: the payload a source delivers plus the derived artifacts we
# author for it. The payload is read, never modified.
#
#   catalogs/<name>/bundle.json
#     catalog    path to catalog.json (the source's own payload)
#     lexicon    alias + ambiguity-cluster files, keyed by type NAME so they port
#                across catalogs that share vocabulary
#     places     location id -> gazetteer place (+ optional exact coordinates)
#     patients   the mocked patient registry (referrals by type name)
#     calendar   {seed, anchor, density, prebook_rate}
#
# Every catalog maps into the one shared gazetteer (decision 007).
#

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

CATALOGS_DIR = Path(__file__).parent.parent / "catalogs"
GAZETTEER_PATH = CATALOGS_DIR / "gazetteer.json"


def payload_version(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()[:16]


def normalize(text: str) -> str:
    """Lowercase, alphanumerics and single spaces: the key every lookup matches on."""
    return " ".join("".join(c.lower() if c.isalnum() else " " for c in text).split())


@dataclass
class Bundle:
    name: str
    path: Path
    catalog: dict
    version: str
    aliases: dict = field(default_factory=dict)      # phrase -> [type names]
    clusters: list = field(default_factory=list)     # [{id, members:[names], question, hints, safer_default}]
    places: dict = field(default_factory=dict)       # loc id -> {"place": name, "at": [x, y] | None}
    patients: list = field(default_factory=list)
    calendar: dict = field(default_factory=dict)
    gazetteer: dict = field(default_factory=dict)

    # ---- indexes -----------------------------------------------------------
    @property
    def locations(self) -> dict:
        return {l["id"]: l for l in self.catalog["locations"]}

    @property
    def providers(self) -> dict:
        return {p["id"]: p for p in self.catalog["providers"]}

    @property
    def types(self) -> dict:
        return {t["id"]: t for t in self.catalog["appointment_types"]}

    def type_ids_by_name(self) -> dict:
        out: dict = {}
        for t in self.catalog["appointment_types"]:
            out.setdefault(normalize(t["name"]), []).append(t["id"])
        return out

    @property
    def anchor(self) -> date:
        return date.fromisoformat(self.calendar.get("anchor", "2026-10-12"))

    @property
    def seed(self) -> int:
        return int(self.calendar.get("seed", 7))


def _read_json(path: Path):
    return json.loads(path.read_text())


def load_bundle(name_or_path) -> Bundle:
    path = Path(name_or_path)
    if not path.suffix:
        path = CATALOGS_DIR / str(name_or_path) / "bundle.json"
    spec = _read_json(path)
    base = path.parent
    raw = (base / spec["catalog"]).read_bytes()
    aliases: dict = {}
    clusters: list = []
    for lex_file in spec.get("lexicon", []):
        lex = _read_json(base / lex_file)
        for phrase, names in lex.get("aliases", {}).items():
            aliases.setdefault(normalize(phrase), [])
            aliases[normalize(phrase)] += [n for n in names if n not in aliases[normalize(phrase)]]
        clusters += lex.get("clusters", [])
    places = {}
    for loc_id, v in (_read_json(base / spec["places"]) if spec.get("places") else {}).items():
        places[loc_id] = {"place": v, "at": None} if isinstance(v, str) else v
    return Bundle(
        name=spec["name"],
        path=path,
        catalog=json.loads(raw),
        version=payload_version(raw),
        aliases=aliases,
        clusters=clusters,
        places=places,
        patients=_read_json(base / spec["patients"]) if spec.get("patients") else [],
        calendar=spec.get("calendar", {}),
        gazetteer=_read_json(GAZETTEER_PATH),
    )


def list_bundles() -> list:
    return sorted(p.parent.name for p in CATALOGS_DIR.glob("*/bundle.json"))
