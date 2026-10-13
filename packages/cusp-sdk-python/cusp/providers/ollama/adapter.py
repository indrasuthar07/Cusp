"""Provider adapter for a local Ollama server.

The implementation deliberately uses Python's standard library so the local
provider remains free-first and does not add an HTTP client dependency to the
SDK package.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from cusp.providers.base import (
    AdapterError,
    AdapterHealth,
    CapabilitySet,
    CostProvenance,
    ProviderRequest,
    ProviderResponse,
    Usage,
)


@dataclass(frozen=True, slots=True)
class OllamaConfig:
    """Connection settings for a local Ollama server."""

    base_url: str = "http://localhost:11434"
    model: str = "llama3.2"
    api_key: str = ""
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("Ollama base_url must use http:// or https://")
        if not self.model.strip():
            raise ValueError("Ollama model must not be empty")
        if self.timeout_seconds <= 0:
            raise ValueError("Ollama timeout_seconds must be positive")


class OllamaAdapter:
    """Normalized adapter for Ollama's `/api/tags` and `/api/chat` APIs."""

    provider_kind = "ollama"
    capabilities = CapabilitySet(
        streaming=True,
        tools=True,
        structured_output=True,
        usage_reporting=False,
    )

    def __init__(self, config: OllamaConfig | None = None) -> None:
        self.config = config or OllamaConfig()

    def validate_profile(self, profile: dict[str, Any]) -> None:
        if profile.get("provider") != self.provider_kind:
            raise ValueError("Ollama adapter requires an ollama provider profile")
        if not str(profile.get("model", "")).strip():
            raise ValueError("Ollama provider profile requires a model")
        if profile.get("privacy_class") not in {None, "offline_private"}:
            raise ValueError("Ollama must use the offline_private privacy class")
        profile_capabilities = profile.get("capabilities")
        if profile_capabilities is not None:
            for name in ("streaming", "tools", "structured_output"):
                if profile_capabilities.get(name) is False and getattr(self.capabilities, name):
                    raise ValueError(f"Ollama profile disables required capability: {name}")

    def health(self) -> AdapterHealth:
        try:
            payload = self._request("GET", "/api/tags")
            models = payload.get("models", [])
            model_names = {str(model.get("name", "")) for model in models if isinstance(model, dict)}
            if self.config.model not in model_names:
                return AdapterHealth(
                    healthy=False,
                    provider=self.provider_kind,
                    message="configured_model_not_found",
                )
            return AdapterHealth(healthy=True, provider=self.provider_kind)
        except AdapterError as exc:
            return AdapterHealth(healthy=False, provider=self.provider_kind, message=exc.code)

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        if request.stream:
            raise AdapterError("streaming_not_supported_by_sync_adapter", "use a streaming adapter method")
        started = time.perf_counter()
        payload = self._request(
            "POST",
            "/api/chat",
            {
                "model": request.model or self.config.model,
                "messages": [{"role": "user", "content": request.prompt}],
                "stream": False,
                **({"tools": list(request.tools)} if request.tools else {}),
            },
        )
        message = payload.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise AdapterError("ollama_invalid_response", "Ollama response did not contain message content")
        return ProviderResponse(
            content=message["content"],
            usage=Usage(
                input_tokens=self._non_negative_int(payload.get("prompt_eval_count")),
                output_tokens=self._non_negative_int(payload.get("eval_count")),
                virtual_cost_micro_usd=0,
                cost_provenance=CostProvenance.VIRTUAL,
                latency_ms=round((time.perf_counter() - started) * 1000),
            ),
            finish_reason=str(payload.get("done_reason")) if payload.get("done_reason") else None,
            raw_metadata={"load_duration_ns": payload.get("load_duration")},
        )

    @staticmethod
    def _non_negative_int(value: Any) -> int:
        return value if isinstance(value, int) and value >= 0 else 0

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        url = urljoin(self.config.base_url.rstrip("/") + "/", path.lstrip("/"))
        headers = {"Accept": "application/json"}
        data: bytes | None = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.config.timeout_seconds) as response:
                decoded = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            retryable = exc.code in {408, 429, 500, 502, 503, 504}
            raise AdapterError("ollama_http_error", f"Ollama returned HTTP {exc.code}", retryable=retryable) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise AdapterError("ollama_unavailable", "Ollama is unavailable", retryable=True) from exc
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise AdapterError("ollama_invalid_response", "Ollama returned invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise AdapterError("ollama_invalid_response", "Ollama returned a non-object response")
        return decoded