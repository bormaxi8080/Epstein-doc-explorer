"""Explicit bounded multimodal Messages calls; queue owns retries and budgets."""

import base64
import hashlib
import json
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from .provider import FCCProvider
from .transport import tls_context


class FCCFailure(ValueError):
    def __init__(self, reason, retryable=False, retry_after=5):
        super().__init__(reason)
        self.retryable = retryable
        self.retry_after = retry_after


@dataclass(frozen=True)
class FCCConfig:
    model: str
    routing_revision: str
    base_url: str = "http://127.0.0.1:8082"
    timeout: float = 120
    max_tokens: int = 8192
    temperature: float = 0
    max_image_bytes: int = 8 * 1024**2
    max_response_bytes: int = 8 * 1024**2

    def __post_init__(self):
        FCCProvider(
            self.base_url,
            self.model,
            self.routing_revision,
            timeout=self.timeout,
            max_tokens=self.max_tokens,
        )
        if not 0 <= self.temperature <= 1 or min(self.max_image_bytes, self.max_response_bytes) < 1:
            raise ValueError("Invalid FCC corpus limits")


class FCCMessages:
    def __init__(self, config, api_key="", recorder=None):
        self.config, self.api_key = config, api_key
        self.recorder = recorder

    def identity(self):
        return asdict(self.config)

    def generate(self, prompt, image=None, media_type="image/jpeg"):
        c = self.config
        content = prompt
        if image is not None:
            if (
                media_type not in ("image/jpeg", "image/png")
                or not image
                or len(image) > c.max_image_bytes
            ):
                raise ValueError("Invalid image MIME/size")
            content = [
                {"type": "text", "text": prompt},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": base64.b64encode(image).decode("ascii"),
                    },
                },
            ]
        payload = {
            "model": c.model,
            "max_tokens": c.max_tokens,
            "temperature": c.temperature,
            "stream": False,
            "messages": [{"role": "user", "content": content}],
        }
        headers = {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        request = urllib.request.Request(
            c.base_url.rstrip("/") + "/v1/messages",
            data=json.dumps(payload).encode(),
            headers=headers,
        )

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None

        opener = urllib.request.build_opener(
            NoRedirect(), urllib.request.HTTPSHandler(context=tls_context())
        )
        try:
            with opener.open(request, timeout=c.timeout) as response:
                raw = response.read(c.max_response_bytes + 1)
        except urllib.error.HTTPError as exc:
            status, retry_after = exc.code, 5
            value = exc.headers.get("Retry-After", "")
            try:
                retry_after = (
                    float(value)
                    if value.isdigit()
                    else (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
                )
            except (ValueError, TypeError, OverflowError):
                pass
            if self.recorder:
                try:
                    body = exc.read(c.max_response_bytes + 1)
                    self.recorder(
                        json.dumps(
                            {
                                "http_error": status,
                                "body_base64": base64.b64encode(
                                    body[: c.max_response_bytes]
                                ).decode("ascii"),
                                "truncated": len(body) > c.max_response_bytes,
                                "retry_after_seconds": max(0, min(retry_after, 300)),
                            }
                        ).encode()
                    )
                except (OSError, TimeoutError):
                    pass
            exc.close()
            raise FCCFailure(
                "FCC HTTP " + str(status),
                status in (408, 429, 500, 502, 503, 504),
                max(0, min(retry_after, 300)),
            ) from None
        except (OSError, urllib.error.URLError, TimeoutError):
            raise FCCFailure(
                "FCC transport failure; upstream outcome may be unknown", True
            ) from None
        if len(raw) > c.max_response_bytes:
            raise FCCFailure("FCC response exceeds byte budget")
        try:
            if self.recorder:
                self.recorder(raw)
            result = json.loads(raw)
            if result.get("stop_reason") != "end_turn":
                raise FCCFailure("FCC response is truncated or incomplete")
            blocks = result.get("content", [])
            if not blocks or any(
                b.get("type") not in ("text", "thinking", "redacted_thinking") for b in blocks
            ):
                raise FCCFailure("FCC returned unsupported content/tool blocks")
            body = "".join(b["text"] for b in blocks if b.get("type") == "text").strip()
            # Accept only one complete fenced JSON block; no prose/tool extraction.
            if body.startswith("```json\n") and body.endswith("\n```"):
                body = body[8:-4]
            output = json.loads(body)
            if not isinstance(output, dict):
                raise FCCFailure("FCC structured response must be an object")
            return {
                "output": output,
                "reported_model": result.get("model"),
                "response_id": result.get("id"),
                "usage": result.get("usage", {}),
                "provider": self.identity(),
                "response_sha256": hashlib.sha256(raw).hexdigest(),
            }
        except (AttributeError, KeyError, TypeError, json.JSONDecodeError):
            raise FCCFailure("FCC response is not the expected structured JSON") from None
