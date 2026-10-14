"""Credential-optional hosted provider adapters.

The adapters are never constructed unless a caller explicitly supplies a key.
They use the standard library so the SDK remains installable for offline and
Ollama-only deployments.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin
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
class HostedProviderConfig:
    """Common settings for a hosted provider profile."""

    api_key: str
    model: str
    base_url: str
    timeout_seconds: float = 30.0
    input_micro_usd_per_1k_tokens: int | None = None
    output_micro_usd_per_1k_tokens: int | None = None

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise ValueError("hosted provider API key is required")
        if not self.model.strip():
            raise ValueError("hosted provider model is required")
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("hosted provider base_url must use http:// or https://")
        if self.timeout_seconds <= 0:
            raise ValueError("hosted provider timeout_seconds must be positive")
        for name, rate in (
            ("input_micro_usd_per_1k_tokens", self.input_micro_usd_per_1k_tokens),
            ("output_micro_usd_per_1k_tokens", self.output_micro_usd_per_1k_tokens),
        ):
            if rate is not None and rate < 0:
                raise ValueError(f"{name} cannot be negative")


class OpenAICompatibleAdapter:
    """Adapter for OpenAI-compatible chat-completions providers.

    This is used for Groq, Hugging Face Router, and optional OpenAI profiles.
    Capability validation prevents silently sending unsupported tools or
    structured-output options to a profile that does not advertise them.
    """

    def __init__(
        self,
        provider_kind: str,
        config: HostedProviderConfig,
        *,
        capabilities: CapabilitySet | None = None,
    ) -> None:
        self.provider_kind = provider_kind
        self.config = config
        self.capabilities = capabilities or CapabilitySet(True, True, True, True)

    def validate_profile(self, profile: dict[str, Any]) -> None:
        if profile.get("provider") != self.provider_kind:
            raise ValueError(
                f"adapter requires a {self.provider_kind} provider profile"
            )
        if profile.get("privacy_class") not in {
            None,
            "hosted_consent_required",
            "hosted_optional",
        }:
            raise ValueError("hosted providers require a hosted privacy class")
        advertised = profile.get("capabilities", {})
        for capability in ("streaming", "tools", "structured_output"):
            if advertised.get(capability) is False and getattr(
                self.capabilities, capability
            ):
                raise ValueError(f"profile disables required capability: {capability}")

    def health(self) -> AdapterHealth:
        try:
            payload = self._request("GET", "/models")
            if not isinstance(payload, dict):
                raise AdapterError(
                    "provider_invalid_response", "provider returned invalid models"
                )
            return AdapterHealth(True, self.provider_kind)
        except AdapterError as exc:
            return AdapterHealth(False, self.provider_kind, exc.code)

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        if request.stream:
            raise AdapterError(
                "streaming_not_supported_by_sync_adapter",
                "use a streaming adapter method",
            )
        started = time.perf_counter()
        body: dict[str, Any] = {
            "model": request.model or self.config.model,
            "messages": [{"role": "user", "content": request.prompt}],
            "stream": False,
        }
        if request.tools:
            if not self.capabilities.tools:
                raise AdapterError(
                    "tools_not_supported", "provider profile does not support tools"
                )
            body["tools"] = list(request.tools)
        payload = self._request("POST", "/chat/completions", body)
        choices = payload.get("choices")
        if (
            not isinstance(choices, list)
            or not choices
            or not isinstance(choices[0], dict)
        ):
            raise AdapterError(
                "provider_invalid_response", "provider returned no choices"
            )
        message = choices[0].get("message", {})
        content = message.get("content", "") if isinstance(message, dict) else ""
        if not isinstance(content, str):
            content = json.dumps(content, separators=(",", ":"))
        usage_payload = payload.get("usage", {})
        usage = self._usage(
            usage_payload, round((time.perf_counter() - started) * 1000)
        )
        return ProviderResponse(
            content=content,
            usage=usage,
            finish_reason=str(choices[0].get("finish_reason"))
            if choices[0].get("finish_reason")
            else None,
            raw_metadata={"provider": self.provider_kind},
        )

    def _usage(self, payload: Any, latency_ms: int) -> Usage:
        data = payload if isinstance(payload, dict) else {}
        input_tokens = _non_negative_int(
            data.get("prompt_tokens", data.get("input_tokens"))
        )
        output_tokens = _non_negative_int(
            data.get("completion_tokens", data.get("output_tokens"))
        )
        provider_cost = _non_negative_int_or_none(data.get("cost_micro_usd"))
        catalog_cost = _catalog_cost(
            input_tokens,
            output_tokens,
            self.config.input_micro_usd_per_1k_tokens,
            self.config.output_micro_usd_per_1k_tokens,
        )
        return Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            provider_cost_micro_usd=provider_cost,
            virtual_cost_micro_usd=catalog_cost,
            cost_provenance=_provenance(provider_cost, catalog_cost),
            latency_ms=latency_ms,
        )

    def _request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        url = urljoin(self.config.base_url.rstrip("/") + "/", path.lstrip("/"))
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
        }
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        try:
            with urlopen(
                Request(url, data=data, headers=headers, method=method),
                timeout=self.config.timeout_seconds,
            ) as response:
                decoded = json.loads(response.read().decode())
        except HTTPError as exc:
            code = (
                "provider_quota_exhausted" if exc.code == 429 else "provider_http_error"
            )
            raise AdapterError(
                code,
                f"{self.provider_kind} returned HTTP {exc.code}",
                retryable=exc.code in {408, 429, 500, 502, 503, 504},
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise AdapterError(
                "provider_unavailable",
                f"{self.provider_kind} is unavailable",
                retryable=True,
            ) from exc
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise AdapterError(
                "provider_invalid_response",
                f"{self.provider_kind} returned invalid JSON",
            ) from exc
        if not isinstance(decoded, dict):
            raise AdapterError(
                "provider_invalid_response",
                f"{self.provider_kind} returned a non-object response",
            )
        return decoded


class GeminiAdapter:
    """Official Gemini Generative Language API adapter."""

    provider_kind = "gemini"
    capabilities = CapabilitySet(True, True, True, True)

    def __init__(self, config: HostedProviderConfig) -> None:
        self.config = config

    def validate_profile(self, profile: dict[str, Any]) -> None:
        if profile.get("provider") != self.provider_kind:
            raise ValueError("Gemini adapter requires a gemini provider profile")
        if profile.get("privacy_class") != "hosted_consent_required":
            raise ValueError("Gemini requires hosted_consent_required privacy")
        if profile.get("requires_hosted_egress_ack") is not True:
            raise ValueError("Gemini requires hosted egress acknowledgement")

    def health(self) -> AdapterHealth:
        try:
            self._request("GET", f"/v1beta/models/{quote(self.config.model, safe='')}")
            return AdapterHealth(True, self.provider_kind)
        except AdapterError as exc:
            return AdapterHealth(False, self.provider_kind, exc.code)

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        if request.stream:
            raise AdapterError(
                "streaming_not_supported_by_sync_adapter",
                "use a streaming adapter method",
            )
        started = time.perf_counter()
        contents: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": request.prompt}]}]
        }
        if request.tools:
            contents["tools"] = [{"function_declarations": list(request.tools)}]
        payload = self._request(
            "POST",
            f"/v1beta/models/{quote(request.model or self.config.model, safe='')}:generateContent",
            contents,
        )
        candidates = payload.get("candidates")
        if (
            not isinstance(candidates, list)
            or not candidates
            or not isinstance(candidates[0], dict)
        ):
            raise AdapterError(
                "provider_invalid_response", "Gemini returned no candidates"
            )
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(
            str(part.get("text", "")) for part in parts if isinstance(part, dict)
        )
        metadata = payload.get("usageMetadata", {})
        input_tokens = _non_negative_int(metadata.get("promptTokenCount"))
        output_tokens = _non_negative_int(metadata.get("candidatesTokenCount"))
        provider_cost = _non_negative_int_or_none(payload.get("cost_micro_usd"))
        catalog_cost = _catalog_cost(
            input_tokens,
            output_tokens,
            self.config.input_micro_usd_per_1k_tokens,
            self.config.output_micro_usd_per_1k_tokens,
        )
        return ProviderResponse(
            content=text,
            usage=Usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                provider_cost_micro_usd=provider_cost,
                virtual_cost_micro_usd=catalog_cost,
                cost_provenance=_provenance(provider_cost, catalog_cost),
                latency_ms=round((time.perf_counter() - started) * 1000),
            ),
            finish_reason=str(candidates[0].get("finishReason"))
            if candidates[0].get("finishReason")
            else None,
            raw_metadata={"provider": self.provider_kind},
        )

    def _request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        url = (
            urljoin("https://generativelanguage.googleapis.com/", path.lstrip("/"))
            + ("&" if "?" in path else "?")
            + "key="
            + quote(self.config.api_key, safe="")
        )
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            with urlopen(
                Request(url, data=data, headers=headers, method=method),
                timeout=self.config.timeout_seconds,
            ) as response:
                decoded = json.loads(response.read().decode())
        except HTTPError as exc:
            code = (
                "provider_quota_exhausted" if exc.code == 429 else "provider_http_error"
            )
            raise AdapterError(
                code,
                f"Gemini returned HTTP {exc.code}",
                retryable=exc.code in {408, 429, 500, 502, 503, 504},
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise AdapterError(
                "provider_unavailable", "Gemini is unavailable", retryable=True
            ) from exc
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise AdapterError(
                "provider_invalid_response", "Gemini returned invalid JSON"
            ) from exc
        if not isinstance(decoded, dict):
            raise AdapterError(
                "provider_invalid_response", "Gemini returned a non-object response"
            )
        return decoded


class GroqAdapter(OpenAICompatibleAdapter):
    """Groq free-tier profile using its OpenAI-compatible endpoint."""

    def __init__(self, config: HostedProviderConfig) -> None:
        super().__init__(
            "groq",
            config,
            capabilities=CapabilitySet(True, True, False, True),
        )


class HuggingFaceAdapter(OpenAICompatibleAdapter):
    """Hugging Face Router profile using its OpenAI-compatible endpoint."""

    def __init__(self, config: HostedProviderConfig) -> None:
        super().__init__(
            "huggingface",
            config,
            capabilities=CapabilitySet(True, False, False, False),
        )


class OpenAIAdapter(OpenAICompatibleAdapter):
    """Optional OpenAI profile; no key is required by the Cusp runtime."""

    def __init__(self, config: HostedProviderConfig) -> None:
        super().__init__("openai", config)


class AnthropicAdapter(OpenAICompatibleAdapter):
    """Anthropic Messages API adapter with normalized response semantics."""

    def __init__(self, config: HostedProviderConfig) -> None:
        super().__init__(
            "anthropic",
            config,
            capabilities=CapabilitySet(True, False, False, True),
        )

    def health(self) -> AdapterHealth:
        # Anthropic has no unauthenticated models-list endpoint. Configuration
        # validation is the safe health check; generation still reports API
        # errors through the normalized AdapterError contract.
        return AdapterHealth(True, self.provider_kind, "configured")

    def _request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        url = urljoin(self.config.base_url.rstrip("/") + "/", path.lstrip("/"))
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "x-api-key": self.config.api_key,
            "anthropic-version": "2023-06-01",
        }
        data = json.dumps(body).encode() if body is not None else None
        try:
            with urlopen(
                Request(url, data=data, headers=headers, method=method),
                timeout=self.config.timeout_seconds,
            ) as response:
                decoded = json.loads(response.read().decode())
        except HTTPError as exc:
            code = (
                "provider_quota_exhausted" if exc.code == 429 else "provider_http_error"
            )
            raise AdapterError(
                code,
                f"Anthropic returned HTTP {exc.code}",
                retryable=exc.code in {408, 429, 500, 502, 503, 504},
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise AdapterError(
                "provider_unavailable", "Anthropic is unavailable", retryable=True
            ) from exc
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise AdapterError(
                "provider_invalid_response", "Anthropic returned invalid JSON"
            ) from exc
        if not isinstance(decoded, dict):
            raise AdapterError(
                "provider_invalid_response", "Anthropic returned a non-object response"
            )
        return decoded

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        if request.stream:
            raise AdapterError(
                "streaming_not_supported_by_sync_adapter",
                "use a streaming adapter method",
            )
        started = time.perf_counter()
        payload = self._request(
            "POST",
            "/v1/messages",
            {
                "model": request.model or self.config.model,
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": request.prompt}],
            },
        )
        content_blocks = payload.get("content", [])
        content = "".join(
            str(item.get("text", ""))
            for item in content_blocks
            if isinstance(item, dict)
        )
        usage = payload.get("usage", {})
        input_tokens = _non_negative_int(usage.get("input_tokens"))
        output_tokens = _non_negative_int(usage.get("output_tokens"))
        provider_cost = _non_negative_int_or_none(payload.get("cost_micro_usd"))
        catalog_cost = _catalog_cost(
            input_tokens,
            output_tokens,
            self.config.input_micro_usd_per_1k_tokens,
            self.config.output_micro_usd_per_1k_tokens,
        )
        return ProviderResponse(
            content=content,
            usage=Usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                provider_cost_micro_usd=provider_cost,
                virtual_cost_micro_usd=catalog_cost,
                cost_provenance=_provenance(provider_cost, catalog_cost),
                latency_ms=round((time.perf_counter() - started) * 1000),
            ),
            finish_reason=str(payload.get("stop_reason"))
            if payload.get("stop_reason")
            else None,
            raw_metadata={"provider": self.provider_kind},
        )


def _non_negative_int(value: Any) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def _non_negative_int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None


def _catalog_cost(
    input_tokens: int,
    output_tokens: int,
    input_rate: int | None,
    output_rate: int | None,
) -> int:
    if input_rate is None or output_rate is None:
        return 0
    return (input_tokens * input_rate + output_tokens * output_rate + 999) // 1000


def _provenance(provider_cost: int | None, catalog_cost: int) -> CostProvenance:
    if provider_cost is not None:
        return CostProvenance.PROVIDER_REPORTED
    if catalog_cost:
        return CostProvenance.CATALOG_ESTIMATE
    return CostProvenance.UNKNOWN
