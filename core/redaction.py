"""Redaction stub hooks for trace data: secret scan, PII mask, truncate.

These are MVP stubs that define the interface. Real implementations would
integrate with tools like detect-secrets, presidio, etc.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.models import RedactionRule

# Maximum blob size before truncation (1 MB for MVP)
MAX_BLOB_BYTES = 1_048_576

# Patterns that suggest secrets (simplified MVP patterns)
_SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"""(?i)(?:api[_-]?key|secret|token|password|passwd|credential)\s*[:=]\s*['"]?[^\s'"]{8,}"""),
    re.compile(r"""(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36,}"""),  # GitHub tokens
    re.compile(r"""AKIA[0-9A-Z]{16}"""),  # AWS access key IDs
    re.compile(r"""-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"""),
]

# Simplified PII patterns (email, phone)
_PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"""[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"""), "[EMAIL_REDACTED]"),
    (re.compile(r"""\b\d{3}[-.]?\d{3}[-.]?\d{4}\b"""), "[PHONE_REDACTED]"),
]


@dataclass
class RedactionResult:
    """Result of applying redaction rules to content."""
    content: bytes
    rules_applied: list[RedactionRule] = field(default_factory=list)
    was_modified: bool = False
    was_truncated: bool = False
    original_length: int = 0


def secret_scan(text: str) -> tuple[str, bool]:
    """Scan text for potential secrets and mask them.

    Returns (redacted_text, was_modified).
    """
    modified = False
    for pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            text = pattern.sub("[SECRET_REDACTED]", text)
            modified = True
    return text, modified


def pii_mask(text: str) -> tuple[str, bool]:
    """Mask PII patterns (emails, phone numbers) in text.

    Returns (redacted_text, was_modified).
    """
    modified = False
    for pattern, replacement in _PII_PATTERNS:
        if pattern.search(text):
            text = pattern.sub(replacement, text)
            modified = True
    return text, modified


def truncate_large(data: bytes, max_bytes: int = MAX_BLOB_BYTES) -> tuple[bytes, bool]:
    """Truncate data if it exceeds max_bytes.

    Returns (data, was_truncated).
    """
    if len(data) <= max_bytes:
        return data, False
    return data[:max_bytes], True


def apply_redaction(
    data: bytes,
    rules: list[RedactionRule] | None = None,
    max_bytes: int = MAX_BLOB_BYTES,
) -> RedactionResult:
    """Apply the requested redaction rules to raw blob data.

    Args:
        data: Raw bytes content.
        rules: Which redaction rules to apply. None means apply all.
        max_bytes: Maximum size before truncation.

    Returns:
        RedactionResult with the (possibly modified) content and metadata.
    """
    if rules is None:
        rules = [RedactionRule.secret_scan, RedactionRule.pii_mask, RedactionRule.truncate_large]

    result = RedactionResult(content=data, original_length=len(data))
    applied: list[RedactionRule] = []

    # Text-based rules only apply to decodable content
    if RedactionRule.secret_scan in rules or RedactionRule.pii_mask in rules:
        try:
            text = data.decode("utf-8")
            text_modified = False

            if RedactionRule.secret_scan in rules:
                text, changed = secret_scan(text)
                if changed:
                    text_modified = True
                    applied.append(RedactionRule.secret_scan)

            if RedactionRule.pii_mask in rules:
                text, changed = pii_mask(text)
                if changed:
                    text_modified = True
                    applied.append(RedactionRule.pii_mask)

            if text_modified:
                result.content = text.encode("utf-8")
                result.was_modified = True
        except UnicodeDecodeError:
            pass  # Binary content — skip text-based rules

    if RedactionRule.truncate_large in rules:
        result.content, was_truncated = truncate_large(result.content, max_bytes)
        if was_truncated:
            applied.append(RedactionRule.truncate_large)
            result.was_truncated = True
            result.was_modified = True

    result.rules_applied = applied
    return result
