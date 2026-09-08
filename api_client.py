"""Talking to the API: request construction, streaming, retries and tool calls.

Everything here runs on a worker thread; the UI only ever sees signals.
"""

from __future__ import annotations

import re
import time
from urllib.parse import urlparse

from PyQt6.QtCore import QThread, pyqtSignal
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    DefaultHttpxClient,
    NotFoundError,
    OpenAI,
    RateLimitError,
)

from version import __version__

LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "0.0.0.0")

# Failures worth trying again: transient transport and server-side conditions.
RETRYABLE = (APIConnectionError, APITimeoutError, RateLimitError)
RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


def is_local_url(url: str) -> bool:
    """True for endpoints that run on this machine (no API key required)."""
    host = (urlparse(url or "").hostname or "").lower()
    return host in LOCAL_HOSTS or ":11434" in (url or "")


def is_reasoning_model(model: str) -> bool:
    """True for OpenAI o-series reasoning models (o1, o1-mini, o3, o3-mini, o4-mini…).

    These reject `temperature` and require `max_completion_tokens` instead of
    `max_tokens`. Any provider prefix (e.g. "openai/o3-mini") is stripped first.
    """
    name = (model or "").rsplit("/", 1)[-1].strip().lower()
    return bool(re.match(r"o[0-9]", name))


def preflight(settings, overrides: dict | None = None) -> str | None:
    """Check the configuration before a request. Returns a problem, or None.

    Catching this here is what turns "raw SDK traceback in a modal" into a
    sentence the user can act on.
    """
    overrides = overrides or {}
    url = (settings.get("api_url") or "").strip()
    if not url:
        return "No API URL is configured. Open Settings and choose a provider."
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return f"The API URL '{url}' is not a valid http(s) address."
    model = (overrides.get("model") or settings.get("model") or "").strip()
    if not model:
        return "No model is selected. Open Settings and pick a model."
    if not is_local_url(url) and not (settings.get("api_key") or "").strip():
        return (
            "This endpoint needs an API key, but none is set. "
            "Open Settings and paste your key."
        )
    return None


def friendly_error(exc: Exception) -> str:
    """Map an SDK exception to an explanation plus what to do about it."""
    if isinstance(exc, AuthenticationError):
        return (
            "The API rejected your key (401 Unauthorized).\n\n"
            "Check the key in Settings, and make sure it belongs to the "
            "provider you selected."
        )
    if isinstance(exc, NotFoundError):
        return (
            "The endpoint or model was not found (404).\n\n"
            "Verify the model name with 'Fetch Models', and check that the "
            "base URL ends in /v1 for OpenAI-compatible servers."
        )
    if isinstance(exc, RateLimitError):
        return (
            "Rate limit or quota exceeded (429).\n\n"
            "Wait a moment and retry, or check your plan's usage limits."
        )
    if isinstance(exc, APITimeoutError):
        return (
            "The request timed out.\n\n"
            "Raise the timeout in Settings, or try a smaller request."
        )
    if isinstance(exc, APIConnectionError):
        return (
            f"Could not reach the API endpoint.\n\n{exc}\n\n"
            "Check that the server is running and the base URL is correct "
            "(for Ollama: http://localhost:11434/v1)."
        )
    if isinstance(exc, BadRequestError):
        return (
            f"The server rejected the request (400).\n\n{exc}\n\n"
            "This often means a parameter the endpoint does not support — "
            "try turning off streaming or tools in Settings."
        )
    if isinstance(exc, APIStatusError):
        return f"The API returned an error (HTTP {exc.status_code}).\n\n{exc}"
    return str(exc)


def request_headers(settings) -> dict:
    """The extra HTTP headers to send, with our User-Agent as a default.

    Header names are case-insensitive, so the default is applied that way: a
    user who configures "user-agent" must override ours rather than be sent
    alongside it.
    """
    headers = settings.get("request_headers") or {}
    if not isinstance(headers, dict):
        headers = {}
    headers = {str(k): str(v) for k, v in headers.items() if str(k).strip()}
    if not any(name.lower() == "user-agent" for name in headers):
        headers["User-Agent"] = f"PyQOA/{__version__}"
    return headers


def build_client(settings, overrides: dict | None = None) -> OpenAI:
    """Construct an OpenAI client honouring custom headers and a proxy.

    Retries are handled by `StreamWorker` (so they can be cancelled and shown in
    the UI), hence `max_retries=0` on the SDK client itself.

    The proxy client comes from `openai.DefaultHttpxClient` rather than from an
    `httpx` import of our own: openai 2.x is built on httpx and 3.x on httpx2, so
    importing either directly makes PyQOA depend on a package the installed SDK
    may not pull in.
    """
    overrides = overrides or {}
    timeout = float(overrides.get("timeout") or settings.get("timeout", 60))
    headers = request_headers(settings)

    proxy = (settings.get("proxy") or "").strip()
    http_client = None
    if proxy:
        http_client = DefaultHttpxClient(proxy=proxy, timeout=timeout)

    kwargs: dict = {
        "api_key": settings.get("api_key") or "none",
        "base_url": settings.get("api_url"),
        "timeout": timeout,
        "max_retries": 0,
        "default_headers": headers,
    }
    if http_client is not None:
        kwargs["http_client"] = http_client
    return OpenAI(**kwargs)


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, RETRYABLE):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code in RETRYABLE_STATUS
    return False


def _merge_tool_call_deltas(acc: dict, deltas) -> None:
    """Accumulate streamed `tool_calls` fragments into whole calls by index."""
    for delta in deltas or []:
        idx = getattr(delta, "index", 0) or 0
        entry = acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
        if getattr(delta, "id", None):
            entry["id"] = delta.id
        fn = getattr(delta, "function", None)
        if fn is not None:
            if getattr(fn, "name", None):
                entry["name"] += fn.name
            if getattr(fn, "arguments", None):
                entry["arguments"] += fn.arguments


def _tool_calls_payload(calls: list[dict]) -> list[dict]:
    out = []
    for i, call in enumerate(calls):
        out.append({
            "id": call.get("id") or f"call_{i}",
            "type": "function",
            "function": {
                "name": call.get("name", ""),
                "arguments": call.get("arguments") or "{}",
            },
        })
    return out


class StreamWorker(QThread):
    chunk_received = pyqtSignal(str)
    completed = pyqtSignal(str)           # full reply text (partial if cancelled)
    error = pyqtSignal(str)
    usage_received = pyqtSignal(int, int)  # prompt_tokens, completion_tokens
    context_built = pyqtSignal(int)        # number of vector-retrieved messages
    tool_activity = pyqtSignal(str)        # human-readable tool progress line
    retrying = pyqtSignal(int, int)        # attempt, max_attempts

    def __init__(
        self,
        settings,
        messages: list | None = None,
        memory=None,
        chat_id: int | None = None,
        current_query: str = "",
        system_prompt: str = "",
        overrides: dict | None = None,
        registry=None,
    ):
        super().__init__()
        self._settings = settings
        self._messages = messages
        self._memory = memory
        self._chat_id = chat_id
        self._current_query = current_query
        self._system_prompt = system_prompt
        self._overrides = overrides or {}
        self._registry = registry
        self._cancelled = False
        self._emitted_any = False  # whether any content chunk has been emitted
        self.tool_log: list[str] = []

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def _cfg(self, key: str, default=None):
        """Per-chat override (if present) falls back to the global setting."""
        if key in self._overrides:
            return self._overrides[key]
        return self._settings.get(key, default)

    def cancel(self):
        self._cancelled = True

    def run(self):
        text = ""
        try:
            messages = self._build_messages()
            if self._cancelled:
                self.completed.emit("")
                return

            client = build_client(self._settings, self._overrides)
            base_params = self._request_params()
            specs = self._tool_specs()

            prompt_tokens = completion_tokens = 0
            rounds = int(self._settings.get("tool_max_rounds", 5) or 5) if specs else 0
            for _round in range(rounds + 1):
                params = dict(base_params)
                params["messages"] = messages
                if specs:
                    params["tools"] = specs
                    params["tool_choice"] = "auto"

                chunk_text, calls, p_tok, c_tok = self._with_retry(client, params)
                prompt_tokens += p_tok
                completion_tokens += c_tok
                if chunk_text:
                    text = f"{text}\n\n{chunk_text}" if text else chunk_text
                if self._cancelled:
                    break
                if not calls:
                    break
                messages = messages + [{
                    "role": "assistant",
                    "content": chunk_text or None,
                    "tool_calls": _tool_calls_payload(calls),
                }]
                messages.extend(self._run_tools(calls))
            else:
                self.tool_activity.emit(
                    f"Stopped after {rounds} tool rounds (tool_max_rounds)."
                )

            if prompt_tokens or completion_tokens:
                self.usage_received.emit(prompt_tokens, completion_tokens)
            self.completed.emit(text)
        except Exception as exc:
            if text:
                # Keep whatever streamed before the failure.
                self.completed.emit(text)
            self.error.emit(friendly_error(exc))

    def _request_params(self) -> dict:
        model = self._cfg("model", "gpt-4o")
        reasoning = is_reasoning_model(model)
        params: dict = {"model": model}
        max_tokens = self._settings.get("max_tokens")
        if max_tokens:
            # Reasoning models use max_completion_tokens instead of max_tokens.
            key = "max_completion_tokens" if reasoning else "max_tokens"
            params[key] = int(max_tokens)
        temperature = self._cfg("temperature")
        # Reasoning models only support the default temperature — omit it.
        if temperature is not None and not reasoning:
            params["temperature"] = float(temperature)
        return params

    def _tool_specs(self) -> list:
        if self._registry is None or not self._registry.enabled:
            return []
        specs = self._registry.specs()
        for err in self._registry.errors:
            self.tool_activity.emit(err)
        self._registry.errors.clear()
        return specs

    def _run_tools(self, calls: list[dict]) -> list[dict]:
        results = []
        for i, call in enumerate(calls):
            name = call.get("name", "")
            if self._cancelled:
                output = "Error: cancelled by user"
            else:
                self.tool_activity.emit(f"Running tool: {name}")
                output = self._registry.call(name, call.get("arguments") or "{}")
            self.tool_log.append(name)
            results.append({
                "role": "tool",
                "tool_call_id": call.get("id") or f"call_{i}",
                "content": output,
            })
        return results

    def _build_messages(self) -> list:
        """Build the message list, optionally using ChatMemory for retrieval."""
        if self._messages is not None:
            return list(self._messages)
        msgs: list = []
        if self._system_prompt:
            msgs.append({"role": "system", "content": self._system_prompt})
        if self._memory and self._chat_id is not None:
            ctx, retrieved = self._memory.build_context(
                self._chat_id, self._current_query
            )
            msgs.extend(ctx)
            self.context_built.emit(retrieved)
        return msgs

    def _with_retry(self, client, params: dict):
        """Run one model call, retrying transient failures with backoff."""
        attempts = max(1, int(self._settings.get("max_retries", 3) or 1))
        base_delay = float(self._settings.get("retry_base_delay", 1.0) or 1.0)
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                if self._settings.get("stream", True):
                    return self._do_stream(client, {**params, "stream": True})
                return self._do_request(client, params)
            except Exception as exc:
                # Retrying after partial output would duplicate it in the reply.
                if self._emitted_any or self._cancelled or not _is_retryable(exc):
                    raise
                last_exc = exc
                if attempt >= attempts:
                    break
                self.retrying.emit(attempt, attempts)
                if not self._sleep_cancellable(base_delay * (2 ** (attempt - 1))):
                    return "", [], 0, 0
        raise last_exc if last_exc else RuntimeError("request failed")

    def _sleep_cancellable(self, seconds: float) -> bool:
        """Sleep in slices so Stop stays responsive. False if cancelled."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self._cancelled:
                return False
            time.sleep(0.05)
        return not self._cancelled

    def _do_request(self, client, params: dict):
        """Non-streaming request: fetch the whole reply in one call."""
        resp = client.chat.completions.create(**params)
        if self._cancelled:
            return "", [], 0, 0
        text = ""
        calls: list[dict] = []
        if resp.choices and resp.choices[0].message:
            msg = resp.choices[0].message
            text = msg.content or ""
            for call in getattr(msg, "tool_calls", None) or []:
                calls.append({
                    "id": call.id,
                    "name": call.function.name,
                    "arguments": call.function.arguments,
                })
        prompt_tokens = completion_tokens = 0
        if resp.usage:
            prompt_tokens = resp.usage.prompt_tokens or 0
            completion_tokens = resp.usage.completion_tokens or 0
        if text:
            self._emitted_any = True
            self.chunk_received.emit(text)
        return text, calls, prompt_tokens, completion_tokens

    def _do_stream(self, client, params: dict):
        """Try streaming with usage tracking; fall back silently if unsupported."""
        try:
            return self._run_stream(
                client, {**params, "stream_options": {"include_usage": True}}
            )
        except Exception as first_exc:
            # If we already streamed content, retrying would duplicate it — re-raise.
            if self._emitted_any:
                raise
            exc_str = str(first_exc).lower()
            if any(k in exc_str for k in (
                "stream_options", "unknown field", "extra field",
                "unrecognized", "unexpected", "invalid",
            )):
                return self._run_stream(client, params)
            raise

    def _run_stream(self, client, params: dict):
        full_text = ""
        prompt_tokens = 0
        completion_tokens = 0
        tool_acc: dict = {}
        with client.chat.completions.create(**params) as stream:
            for chunk in stream:
                if self._cancelled:
                    break
                if chunk.usage:
                    prompt_tokens = chunk.usage.prompt_tokens or 0
                    completion_tokens = chunk.usage.completion_tokens or 0
                if chunk.choices:
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        text = delta.content
                        full_text += text
                        self._emitted_any = True
                        self.chunk_received.emit(text)
                    if delta and getattr(delta, "tool_calls", None):
                        _merge_tool_call_deltas(tool_acc, delta.tool_calls)
        calls = [tool_acc[i] for i in sorted(tool_acc)] if tool_acc else []
        return full_text, calls, prompt_tokens, completion_tokens


class TitleWorker(QThread):
    """Ask the model for a short title for a freshly started conversation."""

    title_ready = pyqtSignal(int, str)  # chat_id, title

    PROMPT = (
        "Write a title of at most 6 words for the conversation that starts with "
        "the message below. Reply with the title only — no quotes, no trailing "
        "period.\n\n"
    )

    def __init__(self, settings, chat_id: int, first_message: str, model: str = ""):
        super().__init__()
        self._settings = settings
        self._chat_id = chat_id
        self._text = first_message
        self._model = model

    def run(self):
        try:
            client = build_client(self._settings)
            resp = client.chat.completions.create(
                model=self._model or self._settings.get("model", "gpt-4o"),
                messages=[{"role": "user", "content": self.PROMPT + self._text[:2000]}],
                max_tokens=24,
            )
            title = ""
            if resp.choices and resp.choices[0].message:
                title = (resp.choices[0].message.content or "").strip()
            title = clean_title(title)
            if title:
                self.title_ready.emit(self._chat_id, title)
        except Exception:
            pass  # a generated title is a nicety; the fallback title already exists


def clean_title(raw: str) -> str:
    """Normalise a model-written title: one line, unquoted, length-capped."""
    title = (raw or "").strip().splitlines()[0] if (raw or "").strip() else ""
    title = title.strip().strip('"“”\'').rstrip(".").strip()
    if len(title) > 60:
        title = title[:59].rstrip() + "…"
    return title


def fallback_title(text: str) -> str:
    """Title derived from the first user message (used when auto-title is off)."""
    first = " ".join((text or "").split())
    return (first[:47] + "…") if len(first) > 50 else first or "New Chat"
