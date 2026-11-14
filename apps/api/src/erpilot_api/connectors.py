"""Shared HTTP connector configuration for independently deployed ERP systems."""

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from erpilot_api.modules import BusinessSessionContext

_TRUE = {"1", "true", "yes"}
_FALSE = {"0", "false", "no"}
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in _TRUE:
        return True
    if normalized in _FALSE:
        return False
    raise ValueError(f"{name} must be 1/0, true/false or yes/no")


def _safe_header(value: str, field_name: str) -> str:
    if not value or any(character in value for character in "\r\n"):
        raise ValueError(f"{field_name} is not safe for an HTTP header")
    try:
        value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field_name} must contain ASCII characters") from exc
    return value


@dataclass(frozen=True)
class HttpConnectorConfig:
    """Validated endpoint and server-side credentials for one ERP connector."""

    base_url: str
    service_token: str | None = field(default=None, repr=False)
    timeout_seconds: float = 10.0
    verify_tls: bool = True

    def __post_init__(self):
        endpoint = self.base_url.rstrip("/")
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "ERP connector URL must be an http(s) origin/path without credentials, "
                "query or fragment"
            )
        if not 0.1 <= self.timeout_seconds <= 120:
            raise ValueError("ERP connector timeout must be between 0.1 and 120 seconds")
        token = self.service_token.strip() if self.service_token else None
        if token:
            _safe_header(token, "ERP connector service token")
        if token and parsed.scheme != "https" and parsed.hostname not in _LOOPBACK_HOSTS:
            raise ValueError("ERP service credentials require HTTPS outside loopback")
        object.__setattr__(self, "base_url", endpoint)
        object.__setattr__(self, "service_token", token)

    @classmethod
    def from_env(cls, prefix: str, *, default_url: str | None = None):
        """Load PREFIX_URL/TOKEN/TIMEOUT/VERIFY_TLS without exposing the token."""
        normalized = prefix.rstrip("_")
        url_name = f"{normalized}_URL"
        base_url = os.getenv(url_name, default_url or "").strip()
        if not base_url:
            raise ValueError(f"{url_name} is required")
        timeout_name = f"{normalized}_TIMEOUT"
        try:
            timeout = float(os.getenv(timeout_name, "10"))
        except ValueError as exc:
            raise ValueError(f"{timeout_name} must be a number") from exc
        return cls(
            base_url=base_url,
            service_token=os.getenv(f"{normalized}_SERVICE_TOKEN") or None,
            timeout_seconds=timeout,
            verify_tls=_env_bool(f"{normalized}_VERIFY_TLS", True),
        )

    def headers(self, context: BusinessSessionContext | None = None) -> dict[str, str]:
        """Build trusted server-to-server and actor headers for an ERP request."""
        headers = {"Accept": "application/json", "User-Agent": "Erpilot-Connector/1"}
        if self.service_token:
            headers["Authorization"] = f"Bearer {self.service_token}"
        if context is not None:
            identity_headers = {
                "X-Erpilot-Session-Id": context.session_id,
                "X-Erpilot-User-Id": context.user_id,
                "X-Erpilot-Username": context.username,
                "X-Erpilot-Role": context.role,
            }
            headers.update({
                name: _safe_header(value, name)
                for name, value in identity_headers.items()
                if value is not None
            })
        return headers

    def public_summary(self) -> dict[str, object]:
        """Return diagnostics that are safe to expose; credentials are never included."""
        return {
            "scheme": urlsplit(self.base_url).scheme,
            "authenticated": self.service_token is not None,
            "verify_tls": self.verify_tls,
            "timeout_seconds": self.timeout_seconds,
        }
