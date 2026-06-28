import re

from PyQt6.QtCore import QThread, pyqtSignal
from openai import OpenAI


def is_reasoning_model(model: str) -> bool:
    """True for OpenAI o-series reasoning models (o1, o1-mini, o3, o3-mini, o4-mini…).

    These reject `temperature` and require `max_completion_tokens` instead of
    `max_tokens`. Any provider prefix (e.g. "openai/o3-mini") is stripped first.
    """
    name = (model or "").rsplit("/", 1)[-1].strip().lower()
    return bool(re.match(r"o[0-9]", name))


class StreamWorker(QThread):
    chunk_received = pyqtSignal(str)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    usage_received = pyqtSignal(int, int)  # prompt_tokens, completion_tokens
    context_built = pyqtSignal(int)        # number of vector-retrieved messages

    def __init__(
        self,
        settings,
        messages: list | None = None,
        memory=None,
        chat_id: int | None = None,
        current_query: str = "",
        system_prompt: str = "",
        overrides: dict | None = None,
    ):
        super().__init__()
        self._settings = settings
        self._messages = messages
        self._memory = memory
        self._chat_id = chat_id
        self._current_query = current_query
        self._system_prompt = system_prompt
        self._overrides = overrides or {}
        self._cancelled = False
        self._emitted_any = False  # whether any content chunk has been emitted

    def _cfg(self, key: str, default=None):
        """Per-chat override (if present) falls back to the global setting."""
        if key in self._overrides:
            return self._overrides[key]
        return self._settings.get(key, default)

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            # Build the context here (off the UI thread) — embeddings can block
            messages = self._build_messages()
            if self._cancelled:
                return

            client = OpenAI(
                api_key=self._settings.get("api_key") or "none",
                base_url=self._settings.get("api_url"),
                timeout=float(self._settings.get("timeout", 60)),
            )

            model = self._cfg("model", "gpt-4o")
            reasoning = is_reasoning_model(model)
            params: dict = {
                "model": model,
                "messages": messages,
            }
            max_tokens = self._settings.get("max_tokens")
            if max_tokens:
                # Reasoning models use max_completion_tokens instead of max_tokens.
                key = "max_completion_tokens" if reasoning else "max_tokens"
                params[key] = int(max_tokens)
            temperature = self._cfg("temperature")
            # Reasoning models only support the default temperature — omit it.
            if temperature is not None and not reasoning:
                params["temperature"] = float(temperature)

            if self._settings.get("stream", True):
                full_text, prompt_tokens, completion_tokens = self._do_stream(
                    client, {**params, "stream": True}
                )
            else:
                full_text, prompt_tokens, completion_tokens = self._do_request(
                    client, params
                )

            if self._cancelled:
                return
            if prompt_tokens or completion_tokens:
                self.usage_received.emit(prompt_tokens, completion_tokens)
            self.finished.emit(full_text)
        except Exception as exc:
            self.error.emit(str(exc))

    def _build_messages(self) -> list:
        """Build the message list, optionally using ChatMemory for retrieval."""
        if self._messages is not None:
            return self._messages
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

    def _do_request(self, client, params: dict) -> tuple[str, int, int]:
        """Non-streaming request: fetch the whole reply in one call."""
        resp = client.chat.completions.create(**params)
        if self._cancelled:
            return "", 0, 0
        text = ""
        if resp.choices and resp.choices[0].message:
            text = resp.choices[0].message.content or ""
        prompt_tokens = completion_tokens = 0
        if resp.usage:
            prompt_tokens = resp.usage.prompt_tokens or 0
            completion_tokens = resp.usage.completion_tokens or 0
        if text:
            self._emitted_any = True
            self.chunk_received.emit(text)
        return text, prompt_tokens, completion_tokens

    def _do_stream(self, client, params: dict) -> tuple[str, int, int]:
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

    def _run_stream(self, client, params: dict) -> tuple[str, int, int]:
        full_text = ""
        prompt_tokens = 0
        completion_tokens = 0
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
        return full_text, prompt_tokens, completion_tokens
