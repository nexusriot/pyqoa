from PyQt6.QtCore import QThread, pyqtSignal
from openai import OpenAI


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
    ):
        super().__init__()
        self._settings = settings
        self._messages = messages
        self._memory = memory
        self._chat_id = chat_id
        self._current_query = current_query
        self._system_prompt = system_prompt
        self._cancelled = False

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

            params: dict = {
                "model": self._settings.get("model", "gpt-4o"),
                "messages": messages,
                "stream": True,
            }
            max_tokens = self._settings.get("max_tokens")
            if max_tokens:
                params["max_tokens"] = int(max_tokens)
            temperature = self._settings.get("temperature")
            if temperature is not None:
                params["temperature"] = float(temperature)

            full_text, prompt_tokens, completion_tokens = self._do_stream(client, params)

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

    def _do_stream(self, client, params: dict) -> tuple[str, int, int]:
        """Try streaming with usage tracking; fall back silently if unsupported."""
        try:
            return self._run_stream(
                client, {**params, "stream_options": {"include_usage": True}}
            )
        except Exception as first_exc:
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
                        self.chunk_received.emit(text)
        return full_text, prompt_tokens, completion_tokens
