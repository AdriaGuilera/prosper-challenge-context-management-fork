#
# Free slots in SQL, joined LAST: only for the (provider, location)
# pairs that already survived resolution, ranking and the policy relation.
#
# Schedule blocks on the requested days, stepped by the type's duration, minus any
# overlap with the provider's bookings at any site. `calendar.free_slots` is the
# independent Python reference the tests compare this against.
#

from datetime import date, datetime, timedelta

from .calendar import HORIZON_DAYS

SLOTS_SQL = """
WITH pairs AS (
    SELECT * FROM unnest($2::text[], $3::text[]) AS p(provider_id, location_id)
), days AS (
    SELECT d::date AS day FROM generate_series($4::date, $5::date, interval '1 day') d
), blocks AS (
    SELECT s.provider_id, s.location_id, d.day + s.starts AS bstart, d.day + s.ends AS bend
    FROM schedules s
    JOIN pairs USING (provider_id, location_id)
    JOIN days d ON extract(isodow FROM d.day) = s.weekday
    WHERE s.catalog = $1
), cand AS (
    SELECT b.provider_id, b.location_id, gs AS starts
    FROM blocks b,
         generate_series(b.bstart, b.bend - make_interval(mins => $6), make_interval(mins => $6)) gs
), free AS (
    SELECT c.* FROM cand c
    WHERE c.starts >= $7
      AND ($9::int IS NULL OR extract(hour FROM c.starts) >= $9)
      AND ($10::int IS NULL OR extract(hour FROM c.starts) < $10)
      AND NOT EXISTS (
          SELECT 1 FROM appointments a
          WHERE a.catalog = $1 AND a.scope = ANY($8) AND a.status = 'booked'
            AND a.provider_id = c.provider_id
            AND a.starts_at < c.starts + make_interval(mins => $6)
            AND a.ends_at > c.starts)
), numbered AS (
    SELECT f.*, row_number() OVER (PARTITION BY provider_id, location_id ORDER BY starts) AS n
    FROM free f
)
SELECT provider_id, location_id, starts FROM numbered WHERE n <= $11 ORDER BY starts, provider_id, location_id
"""

PARTS_OF_DAY = {"morning": (None, 12), "afternoon": (12, None), "any": (None, None)}


async def free_slots(conn, catalog: str, pairs: list, duration_min: int, now: datetime, scopes: list,
                     part_of_day: str = "any", per_pair: int = 3, horizon_days: int = HORIZON_DAYS) -> list:
    """[(provider_id, location_id, starts)] ordered by time, at most per_pair per pair."""
    if not pairs:
        return []
    lo, hi = PARTS_OF_DAY.get(part_of_day or "any", (None, None))
    rows = await conn.fetch(
        SLOTS_SQL, catalog, [p for p, _ in pairs], [l for _, l in pairs],
        now.date(), now.date() + timedelta(days=horizon_days), duration_min, now, scopes,
        lo, hi, per_pair)
    return [(r["provider_id"], r["location_id"], r["starts"]) for r in rows]


def spoken(dt: datetime, today: date) -> str:
    """'tomorrow, Tuesday October 13 at 9:30am' — what the agent can say as-is."""
    clock = dt.strftime("%-I%p").lower() if dt.minute == 0 else dt.strftime("%-I:%M%p").lower()
    day = dt.strftime("%A %B %-d")
    if dt.date() == today:
        day = f"today, {day}"
    elif dt.date() == today + timedelta(days=1):
        day = f"tomorrow, {day}"
    return f"{day} at {clock}"
