"""Provider-agnostic LLM client: JSON output, response cache, token/call accounting."""
import contextvars
import hashlib
import json
import re
import socket
from pathlib import Path
from urllib.parse import urlparse

from app.core.config import settings

DEFAULT_MODELS = {
    "groq": "llama-3.3-70b-versatile",
    "gemini": "gemini-2.5-flash",
    "ollama": "qwen2.5-coder:7b",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-5-sonnet-20241022",
}

_usage = contextvars.ContextVar("llm_usage", default=None)


def new_usage() -> dict:
    """Start accounting for one review. The dict is shared with every graph node of that review."""
    d = {"llm_calls": 0, "cache_hits": 0, "input_tokens": 0, "output_tokens": 0}
    _usage.set(d)
    return d


def _add(**kw):
    d = _usage.get()
    if d is not None:
        for k, v in kw.items():
            d[k] += v


def model_name() -> str:
    return settings.llm_model or DEFAULT_MODELS.get(settings.llm_provider, "")


def extract_json(text: str):
    """Parse the first complete JSON value in a model reply.

    Handles code fences, prose before/after the JSON, several JSON blocks in one reply, nested objects and
    braces inside strings: json.JSONDecoder.raw_decode reads exactly one value from each candidate start
    position, so `{"a": 1} text {"b": 2}` yields {"a": 1} instead of an invalid greedy match.
    An object is preferred over an array."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE).strip()
    decoder = json.JSONDecoder()
    last = json.JSONDecodeError("no JSON found", text, 0)
    for variant in dict.fromkeys((text, _repair_escapes(text))):
        first_array = None
        for i, ch in enumerate(variant):
            if ch not in "{[":
                continue
            try:
                value, _ = decoder.raw_decode(variant, i)
            except json.JSONDecodeError as e:
                last = e
                continue
            if isinstance(value, dict):
                return value
            if first_array is None:
                first_array = value
        if first_array is not None:
            return first_array
    raise last


def _repair_escapes(text: str) -> str:
    """Models often put a bare backslash in code strings (for example in a regex).
    JSON only allows \\" \\\\ \\/ \\b \\f \\n \\r \\t \\uXXXX, so any other backslash is doubled."""
    return re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", text)


_KEY_FIELDS = {"groq": "groq_api_key", "gemini": "gemini_api_key",
               "openai": "openai_api_key", "anthropic": "anthropic_api_key"}


def _ollama_reachable() -> bool:
    parsed = urlparse(settings.ollama_base_url)
    host = parsed.hostname or "127.0.0.1"
    if host == "localhost":
        host = "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError:
        return False


def provider_problem():
    """Return a human-readable configuration problem with the LLM provider, or None if it looks usable."""
    provider = settings.llm_provider
    if provider not in DEFAULT_MODELS:
        return f"Unknown LLM_PROVIDER '{provider}'. Use one of: {', '.join(DEFAULT_MODELS)}."
    field = _KEY_FIELDS.get(provider)
    if field and not getattr(settings, field):
        return f"LLM_PROVIDER={provider} needs {field.upper()} to be set in .env."
    if provider == "ollama" and not _ollama_reachable():
        return _connection_hint("ollama")
    return None


def _connection_hint(provider: str) -> str:
    if provider == "ollama":
        return (f"Could not reach Ollama at {settings.ollama_base_url}. Start Ollama and run "
                f"`ollama pull {model_name()}`, or switch LLM_PROVIDER in .env (e.g. groq or gemini).")
    return f"Could not reach the {provider} API. Check your internet connection."


async def _complete(system: str, user: str) -> tuple:
    """Return (text, input_tokens, output_tokens)."""
    provider = settings.llm_provider
    problem = provider_problem()
    if problem:
        raise ValueError(problem)

    if provider == "anthropic":
        from anthropic import APIConnectionError as AnthropicConnectionError
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        try:
            resp = await client.messages.create(
                model=model_name(), max_tokens=4096, system=system,
                messages=[{"role": "user", "content": user}],
            )
        except AnthropicConnectionError as e:
            raise ConnectionError(_connection_hint(provider)) from e
        text = "".join(b.text for b in resp.content if b.type == "text")
        return text, resp.usage.input_tokens, resp.usage.output_tokens

    from openai import APIConnectionError, AsyncOpenAI, BadRequestError

    if provider == "groq":
        client = AsyncOpenAI(api_key=settings.groq_api_key, base_url="https://api.groq.com/openai/v1")
    elif provider == "gemini":
        client = AsyncOpenAI(api_key=settings.gemini_api_key,
                             base_url="https://generativelanguage.googleapis.com/v1beta/openai/")
    elif provider == "ollama":
        client = AsyncOpenAI(api_key="ollama", base_url=settings.ollama_base_url, max_retries=0)
    else:  # openai
        client = AsyncOpenAI(api_key=settings.openai_api_key)
    request = {"model": model_name(), "temperature": 0.1,
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    try:
        try:  # ask for native JSON mode; a provider/model that does not support it gets a plain request
            resp = await client.chat.completions.create(**request, response_format={"type": "json_object"})
        except BadRequestError:
            resp = await client.chat.completions.create(**request)
    except APIConnectionError as e:
        raise ConnectionError(_connection_hint(provider)) from e
    usage = getattr(resp, "usage", None)
    return (resp.choices[0].message.content or "",
            getattr(usage, "prompt_tokens", 0) or 0, getattr(usage, "completion_tokens", 0) or 0)


def _cache_path(system: str, user: str) -> Path:
    key = hashlib.sha256(f"{settings.llm_provider}|{model_name()}|{system}|{user}".encode()).hexdigest()
    return Path(settings.cache_dir) / f"{key}.json"


async def complete_json(system: str, user: str, retries: int = 2) -> dict:
    """Call the LLM and parse JSON. Identical prompts are served from the on-disk cache."""
    path = _cache_path(system, user)
    if settings.llm_cache and path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            _add(cache_hits=1)
            return data
        except (OSError, json.JSONDecodeError):
            pass

    last_err = None
    for attempt in range(retries + 1):
        prompt = user if attempt == 0 else user + "\n\nYour previous reply was not valid JSON. Return ONLY one valid JSON object."
        raw, tin, tout = await _complete(system, prompt)
        _add(llm_calls=1, input_tokens=tin, output_tokens=tout)
        try:
            data = extract_json(raw)
        except Exception as e:  # noqa: BLE001
            last_err = e
            continue
        if not isinstance(data, dict):
            last_err = ValueError("expected a JSON object, got a list")
            continue
        if settings.llm_cache:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(data), encoding="utf-8")
            except OSError:
                pass
        return data
    raise ValueError(f"LLM returned invalid JSON after {retries + 1} attempts: {last_err}")
