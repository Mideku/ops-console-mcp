"""Secret redaction module, shared logic with the C# server-side redactor
described in 03-sicurezza-threat-model.md (section 6).

Pure stdlib, no I/O, safe to import at any time. The single public entry
point is redact(text) -> str: it replaces every recognized secret-shaped
substring with a fixed placeholder "[REDACTED:<category>]" and returns the
result. It never raises on malformed input (empty string, None-like input
is not expected but falsy input is handled defensively).

Categories (see 03 section 6):
  - token        : known provider token prefixes + generic key=value/bearer patterns
  - private_key  : PEM private key blocks
  - conn_string  : URI connection strings with embedded credentials
  - healthcheck  : healthcheck ping URLs (e.g. hc-ping.com/<uuid>-style paths)
  - internal_host: private IPv4 addresses and internal-looking hostnames
  - entropy      : fallback heuristic for high-entropy strings that did not
                    match any known pattern (fail-safe: prefer a false
                    positive redaction over a false negative exposure)
"""

from __future__ import annotations

import math
import re

PLACEHOLDER = "[REDACTED:{category}]"

# --- Known / generic tokens ---------------------------------------------

# Recognizable provider token prefixes (GitHub PAT family, fine-grained PAT).
_KNOWN_TOKEN_PREFIXES = re.compile(
    r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,255}\b"
    r"|\bgithub_pat_[A-Za-z0-9_]{20,255}\b"
)

# Generic "token=", "key=", "authorization:", "bearer " style assignments.
# Captures the key/prefix separately so it is preserved and only the value
# is redacted.
_GENERIC_TOKEN_ASSIGNMENT = re.compile(
    r"(?i)\b(token|api[_-]?key|secret|password|passwd|pwd|authorization)\s*[:=]\s*"
    r"['\"]?([A-Za-z0-9\-_./+=]{8,})['\"]?"
)

_BEARER_HEADER = re.compile(r"(?i)\bbearer\s+([A-Za-z0-9\-_.+/=]{8,})")

# --- PEM private keys -----------------------------------------------------

_PEM_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)

# --- Connection strings ----------------------------------------------------

# scheme://user:password@host[:port]/... — any scheme (postgres, redis, etc).
_CONN_STRING = re.compile(
    r"\b[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s:/@]+:[^\s@/]+@[^\s/'\"]+"
)

# --- Healthcheck ping URLs --------------------------------------------------

# healthchecks.io-style ping URLs: https://hc-ping.com/<uuid> or a self-hosted
# equivalent path ending in a UUID-shaped identifier used as a bearer token.
_HEALTHCHECK_URL = re.compile(
    r"(?i)\bhttps?://[^\s'\"]*\bhc-ping\b[^\s'\"]*"
    r"|\bhttps?://[^\s'\"]+/ping/[0-9a-f-]{16,}[^\s'\"]*"
)

# --- Private IPs / internal hostnames --------------------------------------

_PRIVATE_IPV4 = re.compile(
    r"\b(?:"
    r"10(?:\.\d{1,3}){3}"
    r"|172\.(?:1[6-9]|2\d|3[0-1])(?:\.\d{1,3}){2}"
    r"|192\.168(?:\.\d{1,3}){2}"
    # CGNAT 100.64.0.0/10 — used by mesh VPNs such as Tailscale.
    r"|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])(?:\.\d{1,3}){2}"
    r"|127(?:\.\d{1,3}){3}"
    r")\b"
)

# Internal-looking hostnames: single-label or *.local / *.internal / *.lan
# suffixes. Conservative on purpose to avoid redacting public domains.
_INTERNAL_HOSTNAME = re.compile(
    r"\b[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.(?:local|internal|lan|home)\b"
)

# --- Entropy heuristic -------------------------------------------------------

_CANDIDATE_TOKEN = re.compile(r"\b[A-Za-z0-9+/_=-]{20,}\b")
_ENTROPY_THRESHOLD_BITS_PER_CHAR = 3.5


def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts: dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    length = len(s)
    entropy = 0.0
    for count in counts.values():
        p = count / length
        entropy -= p * math.log2(p)
    return entropy


def _looks_like_secret_by_entropy(candidate: str) -> bool:
    # Require a mix of letters and digits (pure hex digest-looking strings
    # are common and mostly benign, e.g. git commit hashes) OR high enough
    # entropy regardless of composition. This keeps false positives on
    # things like long words or hashes lower while still being fail-safe.
    has_digit = any(c.isdigit() for c in candidate)
    has_alpha = any(c.isalpha() for c in candidate)
    if not (has_digit and has_alpha):
        return False
    return _shannon_entropy(candidate) >= _ENTROPY_THRESHOLD_BITS_PER_CHAR


def _redact_entropy(text: str) -> str:
    def _sub(match: re.Match) -> str:
        candidate = match.group(0)
        if _looks_like_secret_by_entropy(candidate):
            return PLACEHOLDER.format(category="entropy")
        return candidate

    return _CANDIDATE_TOKEN.sub(_sub, text)


def redact(text: str) -> str:
    """Redact known secret shapes from text. Never raises; returns the
    input unchanged (coerced to str) if it is falsy."""
    if not text:
        return text if text is not None else ""

    result = str(text)

    result = _PEM_PRIVATE_KEY.sub(PLACEHOLDER.format(category="private_key"), result)
    result = _CONN_STRING.sub(PLACEHOLDER.format(category="conn_string"), result)
    result = _HEALTHCHECK_URL.sub(PLACEHOLDER.format(category="healthcheck"), result)
    result = _KNOWN_TOKEN_PREFIXES.sub(PLACEHOLDER.format(category="token"), result)

    def _sub_generic(match: re.Match) -> str:
        return f"{match.group(1)}={PLACEHOLDER.format(category='token')}"

    result = _GENERIC_TOKEN_ASSIGNMENT.sub(_sub_generic, result)
    result = _BEARER_HEADER.sub(
        lambda m: "bearer " + PLACEHOLDER.format(category="token"), result
    )

    result = _PRIVATE_IPV4.sub(PLACEHOLDER.format(category="internal_host"), result)
    result = _INTERNAL_HOSTNAME.sub(PLACEHOLDER.format(category="internal_host"), result)

    result = _redact_entropy(result)

    return result
