"""Secret-safe application configuration resolution for explicit live V1 work."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
import os
from pathlib import Path


class LiveConfigurationSource(str, Enum):
    ENVIRONMENT = "environment"
    EXPLICIT_INJECTED = "explicit_injected_config"
    MISSING = "missing"


def prepare_live_environment(
    environment: Mapping[str, str],
    *,
    project_root: Path,
) -> tuple[dict[str, str], LiveConfigurationSource]:
    """Resolve ambient `.env` configuration without mutating or exposing it.

    Explicitly injected mappings are authoritative and never gain ambient file
    values.  The normal Streamlit path passes ``os.environ`` and therefore uses
    the same `.env` loading convention as the approved live audit scripts.
    """
    from stock_analyser.live_smoke import load_dotenv_safely

    resolved = dict(environment)
    if environment is os.environ:
        load_dotenv_safely(
            (project_root / ".env", project_root.parent / ".env"),
            resolved,
        )
        source = LiveConfigurationSource.ENVIRONMENT
    else:
        source = LiveConfigurationSource.EXPLICIT_INJECTED
    return resolved, source
