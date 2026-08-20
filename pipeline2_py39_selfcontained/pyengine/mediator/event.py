"""Event abstraction -- pass-through.

Faithful port of BusinessEntities\\TAK\\Event.cs:94-128. The Event operator
returns the raw event DataInstance list verbatim (interval + value unchanged);
attributes are only validated to exist. Not needed for the 43-concept gate
(events are leaves), included for completeness.
"""

from __future__ import annotations


def calculate(entity, name, spec, data, knowledge, event_id):
    instances = data.get(event_id)
    if not instances:
        return []
    return list(instances)
