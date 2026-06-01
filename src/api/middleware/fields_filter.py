"""
First-level ?fields= attribute projection for TMF list and singleton responses.

href is always included regardless of the ?fields= value.
Nested objects are kept whole — only top-level keys are filtered.
"""
from __future__ import annotations


def apply_fields(
    data: dict | list,
    fields: str | None,
) -> dict | list:
    """
    Filter top-level keys of a resource dict (or list of dicts) to the
    comma-separated `fields` set.  `href` is always preserved.
    Returns the original object unchanged when `fields` is None or empty.
    """
    if not fields:
        return data
    keep = {f.strip() for f in fields.split(",") if f.strip()} | {"href"}
    if isinstance(data, list):
        return [{k: v for k, v in item.items() if k in keep} for item in data]
    return {k: v for k, v in data.items() if k in keep}
