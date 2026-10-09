"""Secret redaction for logs, error payloads and generated documents.

The agent handles credentials, cloud connection strings and search results that
may embed secrets. Redaction is applied at the logging boundary so that no
downstream component has to remember to do it.
"""

from __future__ import annotations

import re
from typing import Any, Final

REDACTED: Final = "[REDACTED]"

_SENSITIVE_KEY_PATTERN: Final = re.compile(
    r"(api[_-]?key|secret|password|passwd|token|credential|authorization|cookie|"
    r"private[_-]?key|access[_-]?key|session[_-]?id|connection[_-]?string|dsn)",
    re.IGNORECASE,
)

_MAX_DEPTH: Final = 12


def is_sensitive_key(key: str) -> bool:
    """Return whether a mapping key is likely to hold a credential."""
    return _SENSITIVE_KEY_PATTERN.search(key) is not None


def redact(value: Any, *, max_depth: int = _MAX_DEPTH) -> Any:
    """Recursively replace credential-like values with a placeholder.

    Mapping keys matching a credential pattern have their value replaced
    wholesale. ``SecretStr`` instances are unwrapped by type, never by string
    matching, so the underlying secret is never materialised as a string.
    """
    if max_depth <= 0:
        return REDACTED

    secret_types = _secret_types()
    if secret_types and isinstance(value, secret_types):
        return REDACTED

    if isinstance(value, dict):
        return {
            key: REDACTED
            if isinstance(key, str) and is_sensitive_key(key)
            else redact(item, max_depth=max_depth - 1)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple | set | frozenset):
        return type(value)(redact(item, max_depth=max_depth - 1) for item in value)

    return value


def _secret_types() -> tuple[type[Any], ...]:
    """Resolve ``pydantic.SecretStr``/``SecretBytes`` types for isinstance checks."""
    try:
        from pydantic import SecretBytes, SecretStr
    except ImportError:  # pragma: no cover - pydantic is a hard dependency
        return ()
    return (SecretStr, SecretBytes)


def redact_text(text: str) -> str:
    """Best-effort scrub of credential-shaped substrings inside free text."""
    patterns = (
        re.compile(r"(?i)\b(?:aws_secret_access_key|api_key|apikey|password|token)\b\s*[:=]\s*\S+"),
        re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"),
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    )
    scrubbed = text
    for pattern in patterns:
        scrubbed = pattern.sub(REDACTED, scrubbed)
    return scrubbed
