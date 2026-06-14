"""Intentional, user-facing domain errors.

The CLI catches ONLY these (clean message + exit 1). Everything else — KeyError,
AttributeError, etc. — is a programming bug and should surface as a traceback,
not be masked by an over-broad handler.
"""
from __future__ import annotations


class MooCloudBillError(Exception):
    """Base for expected, operator-actionable failures."""


class ColumnMapError(MooCloudBillError):
    """The CUR column map points a required field at an absent or invalid column."""
