from PyQt6.QtCore import QThread, pyqtSignal


class StreamWorker(QThread):
    chunk_received = pyqtSignal(str)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, settings, messages: list):
        super().__init__()
        self._settings = settings
        self._messages = messages
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            from openai import OpenAI

            client = OpenAI(
                api_key=self._settings.get("api_key") or "none",
                base_url=self._settings.get("api_url"),
                timeout=float(self._settings.get("timeout", 60)),
            )

            params: dict = {
                "model": self._settings.get("model", "gpt-4o"),
                "messages": self._messages,
                "stream": True,
            }
            max_tokens = self._settings.get("max_tokens")
            if max_tokens:
                params["max_tokens"] = int(max_tokens)
            temperature = self._settings.get("temperature")
            if temperature is not None:
                params["temperature"] = float(temperature)

            full_text = ""
            with client.chat.completions.create(**params) as stream:
                for chunk in stream:
                    if self._cancelled:
                        break
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        text = delta.content
                        full_text += text
                        self.chunk_received.emit(text)

            self.finished.emit(full_text)
        except Exception as exc:
            self.error.emit(str(exc))
