"""DataInstance model + granularity/duration arithmetic.

Faithful port of the Mediator core value types:
  * BusinessEntities\\Data\\DataInstance.cs
  * BusinessEntities\\TAK\\Misc\\Duration.cs (granularity <-> TimeSpan)

DataInstance
------------
An interval carrying a string value for one concept and one entity (patient):
    EntityId (=PatientId), ConceptName, StartTime, EndTime, Value(str).
`TimeSpan = EndTime - StartTime` is computed, never stored.  A *point* / instant
is `StartTime == EndTime`.  Value is ALWAYS a string (states, numbers, booleans
all serialised to string) -- matching DataInstance.cs:24.

Duration
--------
The engine converts (value, granularity) to a fixed-length timedelta.  Crucially
month and year are NOT calendar-aware in the .NET source (Duration.cs:39-75):
    month = value * 30 days, year = value * 365 days.
We reproduce that exactly with fixed multiples; do NOT use calendar arithmetic.
"""

from __future__ import annotations

from datetime import datetime, timedelta


# ---------------------------------------------------------------------------
# granularity  (Duration.cs:39-75 forward, :102-132 inverse)
# ---------------------------------------------------------------------------

# seconds-per-unit, fixed lengths (month=30d, year=365d) exactly as the C#.
_SECONDS_PER_UNIT = {
    "second": 1.0,
    "minute": 60.0,
    "hour": 3600.0,
    "day": 86400.0,
    "week": 7 * 86400.0,
    "month": 30 * 86400.0,
    "year": 365 * 86400.0,
}


def granularity_seconds(granularity):
    """Seconds in one unit of `granularity` (case-insensitive). 0 if unknown
    (mirrors Duration.cs default `new TimeSpan()`)."""
    if not granularity:
        return 0.0
    return _SECONDS_PER_UNIT.get(granularity.strip().lower(), 0.0)


def duration_to_timedelta(value, granularity):
    """(value, granularity) -> timedelta.  Duration.TimeSpan getter.

    value may be a str (from XML) or number.  Unknown granularity -> zero
    timedelta (Duration.cs default branch)."""
    v = float(value) if value not in (None, "") else 0.0
    return timedelta(seconds=v * granularity_seconds(granularity))


def timespan_in_granularity(td, granularity):
    """timedelta -> float count of `granularity` units.  Duration.GetDuration
    (Duration.cs:102-132); default granularity -> -1.0 as in the C#."""
    secs_per = granularity_seconds(granularity)
    if secs_per <= 0.0:
        return -1.0
    return td.total_seconds() / secs_per


def gap_from_duration_dict(d):
    """Convenience: a tak_parse time-gap dict {value, granularity} -> timedelta."""
    if not d:
        return timedelta(0)
    return duration_to_timedelta(d.get("value"), d.get("granularity"))


# ---------------------------------------------------------------------------
# DataInstance  (DataInstance.cs)
# ---------------------------------------------------------------------------

class DataInstance:
    """One interval [StartTime, EndTime] with a string Value for one concept.

    Mirrors BusinessEntities\\Data\\DataInstance.cs.  Mutable -- the .NET code
    mutates StartTime/EndTime in place during Smoosh and Concatenate, and we
    reproduce that.
    """

    __slots__ = ("entity_id", "concept_name", "start", "end", "value")

    def __init__(self, entity_id, concept_name, start, end, value):
        self.entity_id = entity_id
        self.concept_name = concept_name
        self.start = start          # datetime
        self.end = end              # datetime
        self.value = value          # str

    # DataInstance.cs:27
    @property
    def timespan(self):
        return self.end - self.start

    @property
    def is_point(self):
        """Zero-length instant (Start == End)."""
        return self.start == self.end

    def copy(self):
        """Copy-ctor DataInstance.cs:42 (drops any Candidate metadata; here we
        carry none)."""
        return DataInstance(self.entity_id, self.concept_name,
                            self.start, self.end, self.value)

    # DataInstance.cs:58-69
    def clip(self, new_end):
        """Shrink EndTime to `new_end` iff Start < new_end < End.  Returns True
        on success, False if the clip point is at/before start (caller breaks)."""
        if self.start < new_end:
            if new_end < self.end:
                self.end = new_end
            return True
        return False

    def __repr__(self):
        return (f"DI({self.entity_id!r},{self.concept_name!r},"
                f"{self.start:%Y-%m-%d %H:%M:%S}..{self.end:%Y-%m-%d %H:%M:%S},"
                f"{self.value!r})")


# ---------------------------------------------------------------------------
# timestamp formatting for output rows
# ---------------------------------------------------------------------------

def fmt_ts(dt):
    """Canonical output timestamp `YYYY-MM-DD HH:MM:SS` (matches the fixture /
    compare_abstractions normalisation)."""
    return dt.strftime("%Y-%m-%d %H:%M:%S")
