"""Reusable secret redaction for provider diagnostics and request identities."""

from __future__ import annotations

from collections.abc import Mapping
import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


REDACTED = "[REDACTED]"
_SENSITIVE_CANONICAL_NAMES = {
    "apikey", "xapikey", "authorization", "token", "accesstoken", "secret", "clientsecret",
}
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|x-api-key|authorization|token|access_token|secret|client_secret)"
    r"(\s*[:=]\s*)([^&\s,;]+)"
)


def canonical_sensitive_name(name: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def is_sensitive_name(name: object) -> bool:
    return canonical_sensitive_name(name) in _SENSITIVE_CANONICAL_NAMES


def redact_text(value: object) -> str:
    text = str(value)
    text = _BEARER_RE.sub(f"Bearer {REDACTED}", text)
    text = _ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}", text)
    return text


def redact_url(url: str) -> str:
    """Return a diagnostic URL with secret query values and userinfo removed."""
    parts = urlsplit(str(url))
    query = [
        (key, REDACTED if is_sensitive_name(key) else redact_text(value))
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    netloc = f"{REDACTED}@{host}" if parts.username is not None or parts.password is not None else parts.netloc
    return urlunsplit((parts.scheme, netloc, parts.path, urlencode(query), redact_text(parts.fragment)))


def redact_headers(headers: Mapping[str, Any] | None) -> tuple[tuple[str, str], ...]:
    safe = []
    for key, value in (headers or {}).items():
        safe.append((str(key), REDACTED if is_sensitive_name(key) else redact_text(value)))
    return tuple(sorted(safe, key=lambda item: item[0].lower()))


def _stable_value(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return str(value)


def safe_parameters(parameters: Mapping[str, Any] | None) -> tuple[tuple[str, str], ...]:
    """Normalize non-secret parameters; secret names are omitted, not redacted."""
    safe = [
        (str(key).lower(), _stable_value(value))
        for key, value in (parameters or {}).items()
        if not is_sensitive_name(key)
    ]
    return tuple(sorted(safe))
