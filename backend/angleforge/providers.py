"""Backend-only provider adapters; public discovery never sends a runtime key."""
from __future__ import annotations
import asyncio
import json
import os
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import httpx


class ProviderError(ValueError):
    pass


BASES = {"openrouter": "https://openrouter.ai/api/v1", "zen": "https://opencode.ai/zen/v1"}
KEYS = {"openrouter": "OPENROUTER_API_KEY", "zen": "OPENCODE_API_KEY"}
# Protocol evidence: official Zen endpoint table inspected 2026-10-11.
ZEN_RESPONSES = {"gpt-6-astra", "gpt-6-sol", "gpt-6.1-sol", "gpt-6-luna", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5", "gpt-5.5-pro", "gpt-5.4", "gpt-5.4-pro", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-5.3-codex", "gpt-5.3-codex-spark", "gpt-5.2", "gpt-5.2-codex", "gpt-5.1", "gpt-5.1-codex", "gpt-5.1-codex-max", "gpt-5.1-codex-mini", "gpt-5", "gpt-5-codex", "gpt-5-nano", "grok-4.7", "grok-4.6", "grok-4.5", "grok-build-0.1", "muse-spark-1.3", "muse-spark-1.2", "muse-spark-1.3-contributor-free"}
ZEN_CHAT = {"qwen3.8-max", "deepseek-v4.1-flash", "deepseek-v4-pro", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp", "minimax-m3", "minimax-m2.7", "minimax-m2.5", "glm-5.3-flash", "glm-5.3", "glm-5.2", "glm-5.1", "glm-5", "kimi-k2.5", "kimi-k2.6", "kimi-k2.7-code", "kimi-k3", "mistral-large-4", "big-pickle", "space-bunny-free", "longcat-2.5-preview-free", "step-5-preview-free", "exo-free", "mimo-v2.6-flash-free", "mimo-v2.5-free", "ling-3.1-flash-free", "ling-3.0-flash-fin-free", "nemotron-3-ultra-free", "nemotron-3.5-lightning-free"}
_CACHE = {}


def _free_only():
    return os.getenv("ANGLEFORGE_AI_FREE_ONLY", "0").lower() in {"1", "true", "yes"}


def _zero_pricing(pricing):
    if not isinstance(pricing, dict) or not {"prompt", "completion"} <= pricing.keys():
        return False
    try:
        return all(Decimal(str(value)) == 0 for value in pricing.values())
    except (InvalidOperation, ValueError, TypeError):
        return False


def _bounded_env(name, default, low, high):
    try:
        return max(low, min(high, int(os.getenv(name, str(default)))))
    except ValueError:
        return default


@dataclass(frozen=True)
class Budget:
    max_output_tokens: int
    retries: int
    timeout_seconds: int
    total_tokens: int

    @classmethod
    def configured(cls):
        return cls(_bounded_env("ANGLEFORGE_AI_MAX_TOKENS", 1600, 128, 4096),
                   _bounded_env("ANGLEFORGE_AI_RETRIES", 1, 0, 2),
                   _bounded_env("ANGLEFORGE_AI_TIMEOUT_SECONDS", 45, 5, 90),
                   _bounded_env("ANGLEFORGE_AI_TOTAL_OUTPUT_TOKENS", 4800, 128, 12288))


def provider_status():
    provider = os.getenv("ANGLEFORGE_AI_PROVIDER", "openrouter")
    if provider not in BASES:
        provider = "openrouter"
    budget = Budget.configured()
    return {"default_provider": provider, "default_model": os.getenv("ANGLEFORGE_AI_MODEL", ""),
            "free_only": _free_only(),
            "providers": [{"id": name, "configured": bool(os.getenv(key)), "key_env": key} for name, key in KEYS.items()],
            "configured": bool(os.getenv(KEYS[provider])), "local_commands_available": True,
            "max_tokens": budget.max_output_tokens, "retries": budget.retries,
            "timeout_seconds": budget.timeout_seconds, "total_output_token_budget": budget.total_tokens,
            "external_frames_supported": True,
            "external_frame_providers": ["openrouter"],
            "disclosure": "External planning sends your instruction and project metadata. With separate consent, an OpenRouter image-capable model can receive up to 12 timestamped sampled JPEG frames from up to three selected assets. Full video and audio are not sent."}


def _normalize(provider, model):
    identifier = model.get("id", "")
    if provider == "openrouter":
        parameters = model.get("supported_parameters", [])
        architecture = model.get("architecture") or {}
        inputs = architecture.get("input_modalities") or ["text"]
        return {"id": identifier, "name": model.get("name", identifier), "endpoint": "chat/completions",
                "input_modalities": inputs, "structured_outputs": "structured_outputs" in parameters,
                "tool_calling": "tools" in parameters, "capability_evidence": "provider public catalog",
                "planning_supported": "text" in inputs, "pricing": model.get("pricing") or {},
                "free_only_eligible": _zero_pricing(model.get("pricing"))}
    endpoint = "responses" if identifier in ZEN_RESPONSES else "chat/completions" if identifier in ZEN_CHAT else None
    return {"id": identifier, "name": identifier, "endpoint": endpoint, "input_modalities": ["text"],
            "structured_outputs": None, "tool_calling": None,
            "capability_evidence": "Zen catalog confirms availability only; protocol from official endpoint table; other capabilities unverified",
            "planning_supported": endpoint is not None, "pricing": {}, "free_only_eligible": False}


async def list_models(provider):
    if provider not in BASES:
        raise ProviderError("Choose openrouter or zen.")
    cached = _CACHE.get(provider)
    if cached and time.monotonic() - cached[0] < 300:
        return cached[1]
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.get(BASES[provider] + "/models")
        if response.status_code != 200:
            raise ProviderError(f"Public model catalog unavailable (HTTP {response.status_code}).")
        raw = response.json()
        models = [_normalize(provider, m) for m in raw.get("data", []) if isinstance(m, dict) and isinstance(m.get("id"), str)]
        result = {"models": models, "provider": provider, "source": BASES[provider] + "/models"}
        _CACHE[provider] = time.monotonic(), result
        return result
    except (httpx.HTTPError, json.JSONDecodeError, TypeError, AttributeError):
        raise ProviderError("Public model catalog could not be read; check the connection.") from None


def _check(ctx):
    if ctx is not None:
        ctx.check_cancelled()


async def _cancelable(awaitable, ctx):
    task = asyncio.ensure_future(awaitable)
    try:
        while not task.done():
            _check(ctx)
            await asyncio.wait({task}, timeout=.2)
        _check(ctx)
        return task.result()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def generate_plan(provider, model_id, messages, schema, max_tokens=1200, ctx=None):
    """One bounded inference. No tools are executed; JSON text is an untrusted proposal."""
    if provider not in BASES:
        raise ProviderError("Choose openrouter or zen.")
    key = os.getenv(KEYS[provider])
    if not key:
        raise ProviderError(f"{provider} is not configured. Set {KEYS[provider]} in the backend environment; manual editing and offline commands remain available.")
    free_only = _free_only()
    if free_only and provider != "openrouter":
        raise ProviderError("Free-only mode requires an OpenRouter model with verified zero pricing.")
    budget = Budget.configured()
    if type(max_tokens) is not int or not 128 <= max_tokens <= budget.max_output_tokens:
        raise ProviderError(f"max_tokens must be an integer from 128 to {budget.max_output_tokens}.")
    _check(ctx)
    catalog = await _cancelable(list_models(provider), ctx)
    model = next((item for item in catalog["models"] if item["id"] == model_id), None)
    if model is None:
        raise ProviderError("Model ID is absent from the current public catalog. Refresh and choose an available model.")
    if free_only and not _zero_pricing(model.get("pricing")):
        raise ProviderError("Free-only mode blocks paid or unverified model pricing. Select a model with $0 input and output pricing and no additional charges.")
    image_inputs = [part for message in messages if isinstance(message.get("content"), list) for part in message["content"] if isinstance(part, dict) and part.get("type") == "image_url"]
    if image_inputs and (provider != "openrouter" or "image" not in model["input_modalities"]):
        raise ProviderError("Selected model has no verified image-input capability. Use an OpenRouter image-capable model or turn off sampled frames.")
    if image_inputs and (len(image_inputs) > 12 or any(not isinstance(part.get("image_url"), dict) or not str(part["image_url"].get("url", "")).startswith("data:image/jpeg;base64,") for part in image_inputs)):
        raise ProviderError("Sampled frames must be at most twelve internally prepared JPEG data URLs.")
    if not model["planning_supported"]:
        raise ProviderError("This model requires an endpoint or modality this adapter has not verified. Choose a documented Responses/chat text model.")
    payload = {"model": model_id, "stream": False}
    if model["endpoint"] == "responses":
        payload.update(input=messages, max_output_tokens=max_tokens, store=False)
    else:
        payload.update(messages=messages, max_tokens=max_tokens)
    if provider == "openrouter" and model["structured_outputs"]:
        payload["response_format"] = {"type": "json_schema", "json_schema": {"name": "angleforge_edit", "strict": True, "schema": schema}}
        payload["provider"] = {"require_parameters": True}
    if free_only:
        payload.setdefault("provider", {})["max_price"] = {"prompt": 0, "completion": 0, "request": 0, "image": 0}
    # Console service-account keys use the current documented inference gateway.
    # Both endpoints are fixed official hosts; never accept a caller-supplied URL.
    inference_base = "https://opencode.ai/inference/openai/v1" if provider == "zen" and key.startswith("oc_sk_") else BASES[provider]
    attempts = min(budget.retries + 1, budget.total_tokens // max_tokens)
    if attempts < 1:
        raise ProviderError("The output token budget is below this request's limit.")
    for attempt in range(attempts):
        _check(ctx)
        if ctx is not None:
            ctx.progress(None, "Requesting an external edit proposal")
        try:
            async with httpx.AsyncClient(timeout=budget.timeout_seconds, follow_redirects=False) as client:
                response = await _cancelable(client.post(inference_base + "/" + model["endpoint"], json=payload, headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"}), ctx)
        except httpx.HTTPError:
            # A timeout can already have been billed: do not retry an uncertain submission.
            raise ProviderError("Provider connection failed or timed out. The request was not automatically resubmitted; check provider usage before retrying.") from None
        if response.status_code in {429, 503} and attempt + 1 < attempts:
            await _cancelable(asyncio.sleep(min(2 ** attempt, 4)), ctx)
            continue
        if response.status_code != 200:
            if response.status_code == 403 and len(response.content) <= 16_384:
                try:
                    error_data = response.json().get("error", {})
                    error_type = error_data.get("type")
                    limit_source = error_data.get("metadata", {}).get("limit_source")
                except (ValueError, AttributeError):
                    error_type = limit_source = None
                if provider == "zen" and error_type == "FreeTierError":
                    # Map a known error code to local copy; never echo provider text.
                    raise ProviderError("Zen's free tier is restricted to the OpenCode app and cannot be used by AngleForge. Select a paid Zen model with available credits or another provider; no edits were applied.")
                if provider == "openrouter" and limit_source == "openrouter_key_limit":
                    raise ProviderError("OpenRouter's key spending cap blocked this request. A $0 key cap can also block models with zero token prices. Review the key limit in OpenRouter; no edits were applied.")
            raise ProviderError(f"Provider rejected the request (HTTP {response.status_code}); check the model, credentials and provider account. Response bodies are withheld to protect secrets.")
        if key.encode() in response.content:
            raise ProviderError("Provider response contained credential material; no edits were applied.")
        if len(response.content) > 1_000_000:
            raise ProviderError("Provider response exceeded the local response size limit.")
        try:
            raw = response.json()
            if model["endpoint"] == "responses":
                if raw.get("status") in {"incomplete", "failed", "canceled"}:
                    raise ProviderError("Provider returned an incomplete edit; raise the token budget or simplify the request.")
                parts = [part.get("text", "") for item in raw.get("output", []) if item.get("type") == "message" for part in item.get("content", []) if part.get("type") == "output_text"]
                content = "".join(parts) or raw.get("output_text", "")
            else:
                choice = raw["choices"][0]
                if choice.get("finish_reason") not in {None, "stop"}:
                    raise ProviderError("Provider returned an incomplete edit proposal.")
                content = choice["message"]["content"]
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise ValueError()
            usage = raw.get("usage") or {}
            # Returned usage is data. Keep numeric counts/cost only, never echoed request strings.
            usage = {k: v for k, v in usage.items() if k in {"input_tokens", "output_tokens", "total_tokens", "prompt_tokens", "completion_tokens", "cost"} and type(v) in {int, float}}
            return {"proposal": parsed, "usage": usage, "provider": provider, "model": model_id,
                    "structured_output_enforced": bool(provider == "openrouter" and model["structured_outputs"]),
                    "attempts": attempt + 1}
        except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as exc:
            if isinstance(exc, ProviderError):
                raise
            raise ProviderError("Provider did not return a valid JSON edit proposal; no edits were applied.") from None
    raise ProviderError("Provider retry budget exhausted.")
