"""Release route selection with one explicit legacy rollback switch."""

from __future__ import annotations

import os
from collections.abc import Mapping


USE_LEGACY_UI_ENV = "USE_LEGACY_UI"
ENABLE_V1_REPORT_UI_ENV = "ENABLE_V1_REPORT_UI"
_ENABLED_VALUES = frozenset({"1", "true", "yes", "on"})


def is_legacy_ui_enabled(environment: Mapping[str, str] | None = None) -> bool:
    """Return whether the operator explicitly requested the legacy rollback UI."""
    values = os.environ if environment is None else environment
    return values.get(USE_LEGACY_UI_ENV, "").strip().lower() in _ENABLED_VALUES


def is_v1_report_ui_enabled(environment: Mapping[str, str] | None = None) -> bool:
    """Return the default release route; retained as a compatibility helper.

    ``ENABLE_V1_REPORT_UI`` is deprecated and intentionally has no routing
    authority after the 1.0 cutover. Only an explicit ``USE_LEGACY_UI`` true
    value selects rollback mode, so old/new combinations cannot be ambiguous.
    """
    return not is_legacy_ui_enabled(environment)
