"""
Chat memory: two-layer context management.

Layer 1 (always on): a sliding window of the last N messages, sent verbatim
to the model each turn. Older messages fall out of the window.

Layer 2 (optional, requires `chromadb`): when older messages exist, they are
embedded and stored in a per-chat Chroma collection. Before each request,
the new user query is embedded and the top-K most similar older messages are
retrieved and prepended (between two system markers) to the sliding window.

Embeddings are obtained from any OpenAI-compatible /embeddings endpoint —
the same `api_url`/`api_key` used for chat by default, or a separate
`memory_embed_url`/`memory_embed_key` override (useful when chat runs on
Ollama but embeddings run on OpenAI, or vice versa).
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

from openai import OpenAI

import attachments
import tokens as token_counter

try:
    import chromadb
    _CHROMA_AVAILABLE = True
except Exception:  # ImportError or downstream import failure
    _CHROMA_AVAILABLE = False


def chroma_available() -> bool:
    return _CHROMA_AVAILABLE


class _Embedder:
    """Thin wrapper around an OpenAI-compatible /embeddings endpoint."""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 30.0):
        self._base_url = base_url
        self._api_key = api_key or "none"
        self._model = model
        self._timeout = timeout

    def embed(self, texts: list[str]) -> list[list[float]]:
        client = OpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=self._timeout,
        )
        resp = client.embeddings.create(model=self._model, input=texts)
        return [d.embedding for d in resp.data]


class ChatMemory:
    """Sliding window + optional Chroma-backed vector retrieval over older messages."""

    _RETRIEVED_PREFIX = (
        "Relevant earlier context retrieved from this conversation "
        "(use only if helpful):"
    )
    _RETRIEVED_SUFFIX = "End of retrieved context. Continue the recent conversation below."

    def __init__(self, settings, db):
        self._settings = settings
        self._db = db
        self._lock = threading.Lock()
        self._client = None
        self._collections: dict[int, object] = {}

    @property
    def enabled(self) -> bool:
        return bool(self._settings.get("memory_enabled", True))

    @property
    def window_size(self) -> int:
        return max(1, int(self._settings.get("memory_window_size", 20)))

    @property
    def vector_enabled(self) -> bool:
        return (
            _CHROMA_AVAILABLE
            and self.enabled
            and bool(self._settings.get("memory_use_vector", False))
        )

    @property
    def top_k(self) -> int:
        return max(1, int(self._settings.get("memory_top_k", 4)))

    @property
    def max_tokens(self) -> int:
        """Token budget for the assembled context (0 = message count only)."""
        try:
            return max(0, int(self._settings.get("memory_max_tokens", 0) or 0))
        except (TypeError, ValueError):
            return 0

    def _model(self) -> str:
        return self._settings.get("model", "") or ""

    def _fit_budget(self, msgs: list[dict], reserve: int = 0) -> list[dict]:
        """Drop the oldest messages until the context fits `memory_max_tokens`.

        The most recent message is always kept — a budget too small for even one
        message should still send something rather than nothing.
        """
        budget = self.max_tokens
        if budget <= 0 or not msgs:
            return msgs
        budget = max(0, budget - reserve)
        model = self._model()
        kept: list[dict] = []
        used = 0
        for msg in reversed(msgs):
            cost = token_counter.count_message(msg, model)
            if kept and used + cost > budget:
                break
            kept.append(msg)
            used += cost
        kept.reverse()
        return kept

    def estimate_tokens(
        self, chat_id: int, draft: str = "", system_prompt: str = ""
    ) -> int:
        """Approximate size of the next request — powers the composer meter."""
        msgs, _ = self.build_context(chat_id, "")
        if system_prompt:
            msgs = [{"role": "system", "content": system_prompt}] + msgs
        if draft:
            msgs = msgs + [{"role": "user", "content": draft}]
        return token_counter.count_messages(msgs, self._model())

    def _embedder(self) -> Optional[_Embedder]:
        model = (self._settings.get("memory_embed_model") or "").strip()
        if not model:
            return None
        base_url = (
            self._settings.get("memory_embed_url")
            or self._settings.get("api_url")
            or ""
        ).strip()
        api_key = (
            self._settings.get("memory_embed_key")
            or self._settings.get("api_key", "")
        )
        if not base_url:
            return None
        return _Embedder(base_url, api_key, model)

    def _get_client(self):
        if not _CHROMA_AVAILABLE:
            return None
        if self._client is not None:
            return self._client
        try:
            path = Path(self._settings.profile_dir) / "vector_memory"
            path.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(path))
        except Exception:
            self._client = None
        return self._client

    def _get_collection(self, chat_id: int):
        client = self._get_client()
        if client is None:
            return None
        if chat_id in self._collections:
            return self._collections[chat_id]
        try:
            coll = client.get_or_create_collection(
                name=f"chat_{chat_id}",
                metadata={"hnsw:space": "cosine"},
            )
            self._collections[chat_id] = coll
            return coll
        except Exception:
            return None

    def reset_chat(self, chat_id: int) -> None:
        """Delete the vector collection for a chat (called when chat is deleted)."""
        if not _CHROMA_AVAILABLE:
            return
        client = self._get_client()
        if client is None:
            return
        try:
            client.delete_collection(name=f"chat_{chat_id}")
        except Exception:
            pass
        self._collections.pop(chat_id, None)

    def _existing_ids(self, coll) -> set[str]:
        # `include=[]` asks Chroma for ids only — documents and embeddings would
        # otherwise be pulled over for every message on every turn.
        try:
            return set(coll.get(include=[]).get("ids", []) or [])
        except Exception:
            try:
                return set(coll.get().get("ids", []) or [])
            except Exception:
                return set()

    def forget_messages(self, chat_id: int, message_ids: list[int]) -> int:
        """Drop messages from the vector index (they were edited away or deleted).

        Without this, a retracted message keeps being recalled into later
        prompts even though it is no longer part of the conversation.
        """
        if not message_ids or not _CHROMA_AVAILABLE:
            return 0
        coll = self._get_collection(chat_id)
        if coll is None:
            return 0
        try:
            with self._lock:
                coll.delete(ids=[str(m) for m in message_ids])
            return len(message_ids)
        except Exception:
            return 0

    def _index_messages(self, chat_id: int, messages: list) -> int:
        """Embed and index any messages not yet in the collection. Returns count added."""
        if not self.vector_enabled:
            return 0
        coll = self._get_collection(chat_id)
        if coll is None:
            return 0
        existing = self._existing_ids(coll)
        pending = [
            m for m in messages
            if m["role"] != "system"
            and (m["content"] or "").strip()
            and str(m["id"]) not in existing
        ]
        if not pending:
            return 0
        embedder = self._embedder()
        if embedder is None:
            return 0
        try:
            with self._lock:
                texts = [m["content"] for m in pending]
                vecs = embedder.embed(texts)
                coll.add(
                    ids=[str(m["id"]) for m in pending],
                    embeddings=vecs,
                    documents=texts,
                    metadatas=[{"role": m["role"]} for m in pending],
                )
                return len(pending)
        except Exception:
            return 0

    def _retrieve(
        self, chat_id: int, query: str, exclude_ids: set[int]
    ) -> list[dict]:
        coll = self._get_collection(chat_id)
        if coll is None:
            return []
        embedder = self._embedder()
        if embedder is None:
            return []
        try:
            qvec = embedder.embed([query])[0]
            n = max(self.top_k * 3, self.top_k + len(exclude_ids))
            res = coll.query(query_embeddings=[qvec], n_results=n)
        except Exception:
            return []

        ids = (res.get("ids") or [[]])[0]
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        out: list[dict] = []
        for mid, doc, meta in zip(ids, docs, metas):
            try:
                if int(mid) in exclude_ids:
                    continue
            except (TypeError, ValueError):
                continue
            out.append({"role": (meta or {}).get("role", "user"), "content": doc})
            if len(out) >= self.top_k:
                break
        return out

    def build_context(
        self, chat_id: int, current_query: str = ""
    ) -> tuple[list[dict], int]:
        """
        Build the message list for the next API call (excluding the user's
        system prompt — caller prepends that).

        Returns (messages, retrieved_count).
        """
        all_msgs = self._db.get_messages(chat_id)
        if not self.enabled or len(all_msgs) <= self.window_size:
            payloads = [attachments.message_payload(self._db, m) for m in all_msgs]
            return self._fit_budget(payloads), 0

        window = all_msgs[-self.window_size:]
        older = all_msgs[:-self.window_size]

        retrieved: list[dict] = []
        if older and self.vector_enabled and current_query.strip():
            self._index_messages(chat_id, older)
            window_ids = {int(m["id"]) for m in window}
            retrieved = self._retrieve(chat_id, current_query, window_ids)

        head: list[dict] = []
        if retrieved:
            head.append({"role": "system", "content": self._RETRIEVED_PREFIX})
            head.extend(retrieved)
            head.append({"role": "system", "content": self._RETRIEVED_SUFFIX})
        body = [attachments.message_payload(self._db, m) for m in window]
        # The retrieved block is charged against the budget but never trimmed —
        # trimming it would leave the two system markers wrapping nothing.
        reserve = (
            token_counter.count_messages(head, self._model()) if head else 0
        )
        return head + self._fit_budget(body, reserve=reserve), len(retrieved)
