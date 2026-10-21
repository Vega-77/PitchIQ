"""Small helpers shared by more than one module, with no pipeline knowledge."""

from __future__ import annotations


def round_or_none(value, places: int) -> float | None:
    """`value` as a plain float rounded to `places`, or None when it is None.

    No default for `places` on purpose: the four copies this replaced each had a
    different one (1, 2, 3 and none), so every caller says which it means.
    """
    return None if value is None else round(float(value), places)
