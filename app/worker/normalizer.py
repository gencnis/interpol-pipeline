"""Payload normalisation, hashing, and field-level diffing.

All functions are pure (no I/O) and therefore unit-testable without any
infrastructure.

Design note — thumbnail_url is intentionally excluded from the content hash.
A photo replacement does NOT raise an "updated" alarm: photos are an auxiliary
asset stored in MinIO, and the hash tracks only the textual/structural fields of
a notice.  If this policy ever changes, remove "thumbnail_url" from _HASH_EXCLUDE
and add "thumbnail_object_key" (the MinIO key) instead.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

# Keys excluded from the content hash and from diff comparison.
_HASH_EXCLUDE: frozenset[str] = frozenset(
    {"thumbnail_url", "cycle_id", "_links", "raw_notice"}
)


def normalize(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a stable subset of payload fields suitable for hashing and storage.

    Drops volatile / metadata keys so that re-processing the same notice with a
    different cycle_id does not appear as a change.

    >>> normalize({"notice_id": "X", "cycle_id": "y", "thumbnail_url": "u"})
    {'notice_id': 'X'}
    """
    return {k: v for k, v in payload.items() if k not in _HASH_EXCLUDE}


def content_hash(normalized: dict[str, Any]) -> str:
    """SHA-256 hex digest of the deterministically serialised normalised payload.

    >>> content_hash({"a": 1}) == content_hash({"a": 1})
    True
    >>> content_hash({"a": 1}) == content_hash({"a": 2})
    False
    >>> len(content_hash({"notice_id": "2021/1", "name": "DOE"}))
    64
    """
    blob = json.dumps(normalized, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def compute_diff(
    old: dict[str, Any], new: dict[str, Any]
) -> dict[str, Any]:
    """Field-level diff between two normalised payloads.

    Returns only the changed fields, each carrying {"old": ..., "new": ...}.
    List fields (e.g. nationalities, arrest_warrant_countries) are compared
    order-independently so ["TR","US"] and ["US","TR"] are treated as equal.
    Nested structures are compared via their canonical JSON representation.

    >>> compute_diff({"name": "Smith"}, {"name": "Jones"})
    {'name': {'old': 'Smith', 'new': 'Jones'}}
    >>> compute_diff({"nationalities": ["TR", "US"]}, {"nationalities": ["US", "TR"]})
    {}
    >>> compute_diff({"name": "X"}, {"name": "X", "sex_id": "M"})
    {'sex_id': {'old': None, 'new': 'M'}}
    """
    diff: dict[str, Any] = {}
    for key in set(old) | set(new):
        old_val = old.get(key)
        new_val = new.get(key)
        if not _values_equal(old_val, new_val):
            diff[key] = {"old": old_val, "new": new_val}
    return diff


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _canonical(v: Any) -> str:
    """Stable string representation used only for equality comparison.

    Lists are sorted element-by-element so that order does not matter.
    Dicts are serialised with sorted keys.
    """
    if isinstance(v, list):
        return json.dumps(sorted(_canonical(x) for x in v))
    return json.dumps(v, sort_keys=True, ensure_ascii=False, default=str)


def _values_equal(a: Any, b: Any) -> bool:
    return _canonical(a) == _canonical(b)
