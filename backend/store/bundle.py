#
# A catalog bundle: the payload a source delivers plus the derived artifacts we
# author for it. The payload is read, never modified.
#
#   catalogs/<name>/bundle.json
#     catalog    path to catalog.json (the source's own payload)
#     patients   the mocked patient registry (referrals by type name)
#     calendar   {seed, anchor, density, prebook_rate}
#
# Every catalog maps into the one shared gazetteer: a location sits in the area its
# name says ("Downtown Health Center" -> Downtown). Its point is made up from that.
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
    places: dict = field(default_factory=dict)       # loc id -> {"place": area, "at": [x, y]}
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


def _made_up(key: str, span: float) -> float:
    """A stable number in [-span, span] for `key`."""
    return (int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF * 2 - 1) * span


def place_locations(catalog: dict, gazetteer: dict) -> dict:
    """location id -> {"place", "at"}. The place is the longest area named in the location's
    name. The first location in an area sits on its point, later ones up to 1 km away so
    "nearest" can tell them apart."""
    areas = {normalize(p["name"]): p for p in gazetteer["places"] if p["kind"] != "street"}
    out, taken = {}, set()
    for l in catalog["locations"]:
        name = f" {normalize(l['name'])} "
        hits = [a for a in areas if f" {a} " in name]
        if not hits:
            raise ValueError(f"Location {l['id']} '{l['name']}' names no gazetteer area: put one in its name.")
        area = areas[max(hits, key=len)]
        x, y = area["points"][0]
        if area["name"] in taken:
            x, y = x + _made_up(l["id"] + "x", 1), y + _made_up(l["id"] + "y", 1)
        taken.add(area["name"])
        out[l["id"]] = {"place": area["name"], "at": [round(x, 1), round(y, 1)]}
    return out


def load_bundle(name_or_path) -> Bundle:
    path = Path(name_or_path)
    if not path.suffix:
        path = CATALOGS_DIR / str(name_or_path) / "bundle.json"
    spec = _read_json(path)
    base = path.parent
    raw = (base / spec["catalog"]).read_bytes()
    catalog, gazetteer = json.loads(raw), _read_json(GAZETTEER_PATH)
    return Bundle(
        name=spec["name"],
        path=path,
        catalog=catalog,
        version=payload_version(raw),
        places=place_locations(catalog, gazetteer),
        patients=_read_json(base / spec["patients"]) if spec.get("patients") else [],
        calendar=spec.get("calendar", {}),
        gazetteer=gazetteer,
    )


def list_bundles() -> list:
    return sorted(p.parent.name for p in CATALOGS_DIR.glob("*/bundle.json"))
