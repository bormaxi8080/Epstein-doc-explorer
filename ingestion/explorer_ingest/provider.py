"""FCC Anthropic Messages adapter; no tools, agents, or credential persistence."""

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse

from .transport import tls_context


@dataclass(frozen=True)
class FCCProvider:
    base_url: str
    model: str
    routing_revision: str
    api_key: str = ""
    timeout: float = 120
    retries: int = 2
    max_tokens: int = 4096

    def __post_init__(self):
        parsed = urlparse(self.base_url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "FCC_BASE_URL must be an HTTP(S) base URL without credentials or query"
            )
        if parsed.scheme == "http" and parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("Remote FCC endpoints require HTTPS")
        if not self.model.strip() or not self.routing_revision.strip():
            raise ValueError("Set FCC_MODEL and FCC_ROUTING_REVISION explicitly")
        if self.timeout <= 0 or self.retries < 0 or self.max_tokens < 1:
            raise ValueError("Invalid provider limits")

    def identity(self):
        return {
            "base_url": self.base_url,
            "requested_model": self.model,
            "routing_revision": self.routing_revision,
            "max_tokens": self.max_tokens,
        }

    def generate(self, prompt):
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "stream": False,
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
            headers["x-api-key"] = self.api_key
        request = urllib.request.Request(
            self.base_url.rstrip("/") + "/v1/messages",
            data=json.dumps(payload).encode(),
            headers=headers,
        )

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None  # Never forward credentials through an HTTP redirect.

        opener = urllib.request.build_opener(
            NoRedirect(), urllib.request.HTTPSHandler(context=tls_context())
        )
        for attempt in range(self.retries + 1):
            try:
                with opener.open(request, timeout=self.timeout) as response:
                    raw = response.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise ValueError("FCC response exceeds size limit")
                result = json.loads(raw)
                if result.get("stop_reason") != "end_turn":
                    raise ValueError("FCC response is incomplete or requests a tool")
                blocks = result.get("content", [])
                if not blocks or any(
                    x.get("type") not in ("text", "thinking", "redacted_thinking") for x in blocks
                ):
                    raise ValueError("FCC returned unsupported content blocks")
                text = "".join(x["text"] for x in blocks if x.get("type") == "text")
                return {
                    "analysis": json.loads(text),
                    "reported_model": result.get("model"),
                    "usage": result.get("usage", {}),
                    "response_id": result.get("id"),
                }
            except urllib.error.HTTPError as exc:
                status = exc.code
                retry_after = exc.headers.get("Retry-After", "")
                exc.close()
                if status not in (429, 500, 502, 503, 504) or attempt == self.retries:
                    raise ValueError(f"FCC HTTP {status}") from None
                delay = (
                    min(float(retry_after), 10) if retry_after.isdigit() else min(2**attempt, 10)
                )
                time.sleep(delay)
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt == self.retries:
                    raise ValueError("FCC transport failure") from None
                time.sleep(min(2**attempt, 10))
        raise AssertionError("Unreachable retry state")
