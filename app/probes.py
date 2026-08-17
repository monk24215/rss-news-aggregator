"""Service probes for the health check.

The health endpoint has to answer two different audiences:

  - a load balancer, which only needs ok / not-ok, and
  - a human debugging a broken deploy, who needs to know *why*.

Section 1 answered only the first. When the web service came up on Railway with
`redis: false`, the health payload gave no way to tell a wrong URL from a DNS failure
from a refused connection — the exception was swallowed. These probes keep the boolean
contract and add a redacted reason, which is the smallest useful step toward the
observability §45 asks for.

Redaction rule (Non-Negotiable #10): never echo a connection string. We report the
exception type, its message with any credentials stripped, and the host:port we aimed
at — never the password.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit


@dataclass(frozen=True)
class Probe:
    """Result of checking one dependency."""

    ok: bool
    error: str | None = None
    target: str | None = None

    def as_dict(self) -> dict:
        out: dict = {"ok": self.ok}
        if self.target:
            out["target"] = self.target
        if self.error:
            out["error"] = self.error
        return out


def redact_url(url: str) -> str:
    """Return the URL with any userinfo (user:password@) removed.

    >>> redact_url("redis://default:hunter2@redis.internal:6379/0")
    'redis://redis.internal:6379/0'
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparseable url>"
    netloc = parts.hostname or ""
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def scrub(text: str, url: str) -> str:
    """Strip anything credential-shaped out of an exception message."""
    cleaned = text.strip()
    # Some drivers embed the whole DSN in the message — swap it for the redacted form
    # BEFORE stripping the bare password, or the DSN no longer matches.
    if url and url in cleaned:
        cleaned = cleaned.replace(url, redact_url(url))
    try:
        password = urlsplit(url).password
    except ValueError:
        password = None
    if password:
        cleaned = cleaned.replace(password, "***")
    return cleaned[:300]


def describe_failure(exc: BaseException, url: str) -> str:
    """One-line, credential-free description of a failed connection."""
    detail = scrub(str(exc), url)
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__
