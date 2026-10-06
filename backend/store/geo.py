#
# Places (decisions 006, 007): a caller's spoken place -> a point on the fictional
# plane, then KNN over clinic locations. Distance ranks; it never admits.
#
# Resolution order: exact name/alias, then trigram. An exact hit that is still
# trigram-close to another place is flagged as confusable (STT turns "Geary" into
# "Gary"), and a place with several points (a street that spans areas) is ambiguous
# until the caller names an area.
#

from dataclasses import dataclass, field

from .bundle import normalize

CONFUSABLE_SIM = 0.35
FUZZY_MIN = 0.3


@dataclass
class PlaceResolution:
    status: str                       # resolved | ambiguous | unknown
    place: str = None
    point: tuple = None
    candidates: list = field(default_factory=list)   # [{name, kind, area?}]
    confusable_with: list = field(default_factory=list)


async def gazetteer_version(conn) -> str:
    return await conn.fetchval("SELECT gazetteer_version FROM gazetteer_places ORDER BY gazetteer_version DESC LIMIT 1")


async def resolve_place(conn, text: str) -> PlaceResolution:
    q = normalize(text)
    for filler in ("near ", "close to ", "around ", "by ", "in "):
        if q.startswith(filler):
            q = q[len(filler):]
    if not q:
        return PlaceResolution("unknown")
    gv = await gazetteer_version(conn)
    rows = await conn.fetch(
        """SELECT name, kind, ST_NumGeometries(geom) AS n,
                  (SELECT max(greatest(similarity($2, s), word_similarity(s, $2))) FROM unnest(search_names) s) AS sim,
                  $2 = ANY(search_names) AS exact,
                  EXISTS (SELECT 1 FROM unnest(search_names) s WHERE position(' ' || s || ' ' IN ' ' || $2 || ' ') > 0) AS contained
           FROM gazetteer_places WHERE gazetteer_version = $1
           ORDER BY exact DESC, contained DESC, sim DESC LIMIT 6""",
        gv, q)
    exact = [r for r in rows if r["exact"] or r["contained"]]
    best = exact[0] if exact else (rows[0] if rows and rows[0]["sim"] >= FUZZY_MIN else None)
    if best is None:
        return PlaceResolution("unknown")
    confusable = [r["name"] for r in rows if r["name"] != best["name"] and r["sim"] >= CONFUSABLE_SIM and not r["exact"]]
    if not exact:
        close = [r for r in rows if r["sim"] >= max(FUZZY_MIN, best["sim"] - 0.1)]
        if len(close) > 1:
            return PlaceResolution("ambiguous", candidates=[{"name": r["name"], "kind": r["kind"]} for r in close])
    if best["n"] > 1:
        # a street through several areas: name the areas it passes so the caller can pick one
        areas = await conn.fetch(
            """SELECT DISTINCT ON (pt.path) pt.path[1] AS i, g.name
               FROM gazetteer_places p, ST_Dump(p.geom) pt, gazetteer_places g
               WHERE p.gazetteer_version = $1 AND p.name = $2 AND g.gazetteer_version = $1
                 AND g.kind <> 'street' AND ST_NumGeometries(g.geom) = 1
               ORDER BY pt.path, ST_Distance(pt.geom, g.geom)""",
            gv, best["name"])
        return PlaceResolution("ambiguous", place=best["name"],
                               candidates=[{"name": best["name"], "kind": best["kind"], "area": a["name"]} for a in areas])
    pt = await conn.fetchrow(
        "SELECT ST_X(ST_GeometryN(geom, 1)) x, ST_Y(ST_GeometryN(geom, 1)) y FROM gazetteer_places WHERE gazetteer_version = $1 AND name = $2",
        gv, best["name"])
    return PlaceResolution("resolved", place=best["name"], point=(pt["x"], pt["y"]),
                           confusable_with=confusable if exact else [])


async def near_km(conn) -> tuple:
    """(near, far) thresholds are data in the gazetteer file; mirrored here for the tools."""
    return 4.0, 9.0
