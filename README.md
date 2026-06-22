# PyQOA

**Py**Qt6 **O**penAI-compatible **A**ssistant — a cross-platform desktop chat client
for any OpenAI-compatible API: the OpenAI cloud, a local [Ollama](https://ollama.com)
server, or any custom endpoint that speaks the `/v1/chat/completions` protocol.

PyQOA stores your conversations locally in SQLite, streams responses token-by-token,
renders Markdown with syntax-highlighted code blocks, tracks token usage and estimated
cost, and offers an optional vector-memory layer for long conversations.

> Despite the name, PyQOA is **not** OpenAI-only — point it at Ollama or any
> compatible gateway in **Settings → Provider**.

---

## Features

- **Multiple providers** — one-click presets for OpenAI and Ollama, plus a Custom
  option for any OpenAI-compatible base URL.
- **Live model discovery** — *Fetch Models* queries the endpoint (`/v1/models`, or
  Ollama's `/api/tags`) and populates the model dropdown.
- **Streaming responses** — replies appear token-by-token, with a **Stop** button to
  cancel mid-generation. Streaming can be turned off in Settings for endpoints that
  don't support it.
- **Rich rendering** — Markdown → HTML with tables, lists, blockquotes, and
  Pygments-highlighted fenced code blocks (graceful fallback if `markdown`/`Pygments`
  are absent). Each code block has a one-click **Copy** link.
- **Message actions** — copy any message, **regenerate** an assistant reply, or
  **edit & resend** one of your earlier messages (truncating the conversation from
  that point).
- **Per-chat overrides** — give an individual chat its own model, system prompt, or
  temperature via **⚙ Chat options**, while the rest keep the global defaults.
- **Reasoning-model aware** — for OpenAI o-series models (`o1`, `o3`, `o4-mini`, …)
  PyQOA automatically sends `max_completion_tokens` and omits the unsupported
  `temperature`.
- **Export / import** — export any chat to **Markdown** or **JSON**, and import a JSON
  export back into a new chat (overrides and messages preserved).
- **Persistent history** — every chat and message is saved to a local SQLite database.
  Rename, delete, and full-text search your chats.
- **Token & cost tracking** — per-message and per-chat token counts, with a built-in
  price table that estimates USD cost for common OpenAI models.
- **Two-layer chat memory**
  - *Sliding window* (always on): the last *N* messages are sent verbatim each turn.
  - *Vector retrieval* (optional, needs `chromadb`): older messages are embedded and
    the most relevant ones are recalled and prepended to the prompt.
- **Light, dark & system themes** — a cohesive token-based design system. Pick
  **View → Theme → System / Light / Dark**; *System* follows your OS preference and
  updates live when you switch it. `Ctrl+Shift+L` quickly flips light/dark. The choice
  is remembered between launches.
- **Modern, native-feeling UI** built on Qt's Fusion style: avatars, a centered reading
  column, an auto-growing composer, and syntax-highlighted code with per-block copy.

---

## Requirements

- Python **3.10+** (uses `X | None` type syntax and `tuple[int, int]` generics)
- See [`requirements.txt`](requirements.txt):
  - `PyQt6` — GUI toolkit (**required**)
  - `openai` — API client (**required**)
  - `markdown`, `Pygments` — Markdown rendering & syntax highlighting (recommended;
    there is a minimal fallback if missing)
  - `chromadb` — vector memory (**optional**; everything else works without it)

---

## Installation

```bash
git clone <this-repo> pyqoa
cd pyqoa

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

To skip the optional vector-memory dependency, install only the core packages:

```bash
pip install "PyQt6>=6.4" "openai>=1.0" "markdown>=3.4" "Pygments>=2.15"
```

---

## Running

```bash
python main.py
```

On first launch PyQOA creates a profile directory and opens an empty chat. Open
**File → Settings…** (`Ctrl+,`) to configure your provider.

### Quick start with OpenAI

1. **Settings → Provider → OpenAI**.
2. Paste your API key (`sk-…`).
3. Click **Fetch Models** and pick a model (e.g. `gpt-4o`).
4. **Save**, type a message, press **Enter**.

### Quick start with Ollama (fully local)

1. Install and start Ollama, then pull a model: `ollama pull llama3`.
2. **Settings → Provider → Ollama** (base URL defaults to `http://localhost:11434/v1`).
   No API key is required. The model list is fetched automatically.
3. **Save** and chat — nothing leaves your machine.

---

## Keyboard shortcuts

| Shortcut       | Action            |
| -------------- | ----------------- |
| `Enter`        | Send message      |
| `Shift+Enter`  | Insert newline    |
| `Ctrl+N`       | New chat          |
| `Ctrl+,`       | Open Settings     |
| `Ctrl+Shift+L` | Quick-flip light/dark |
| `Ctrl+Q`       | Quit              |

### Message & chat actions

- Hover a message to reveal **Copy** (both roles), **Edit** (your messages), and
  **↻ Regenerate** (assistant replies).
- **Edit** moves a message's text back into the input box and removes that message
  and everything after it, so you can rephrase and resend.
- **↻ Regenerate** discards an assistant reply (and anything after it) and generates
  a fresh one.
- **⧉ Copy** in a code block's header copies just that snippet.
- **⚙ Chat options** (chat header) sets per-chat model / system prompt / temperature.
- Right-click a chat in the sidebar to **Rename**, **Export as Markdown/JSON**, or
  **Delete**. Use **File → Import Chat (JSON)…** to load an exported chat.

---

## Where your data lives

PyQOA keeps a per-user profile directory:

| OS       | Path                                            |
| -------- | ----------------------------------------------- |
| Linux    | `~/.config/pyqoa/`                              |
| macOS    | `~/Library/Application Support/pyqoa/`          |
| Windows  | `%APPDATA%\pyqoa\`                              |

Inside it:

- `settings.json` — your configuration (**includes the API key in plain text**).
- `chats.db` — SQLite database of all chats and messages.
- `vector_memory/` — Chroma vector store (only if vector memory is enabled).

> **Security note:** the API key is stored unencrypted in `settings.json`. Protect
> that directory accordingly, and prefer Ollama / keyless local endpoints where you can.

---

## Configuration reference

All settings are editable from the Settings dialog and persisted to `settings.json`.

| Key | Default | Meaning |
| --- | --- | --- |
| `api_url` | `https://api.openai.com/v1` | Base URL of the OpenAI-compatible endpoint |
| `api_key` | `""` | Bearer token (not required for local endpoints) |
| `model` | `gpt-4o` | Model name sent with each request |
| `timeout` | `60` | Per-request timeout (seconds) |
| `max_tokens` | `4096` | Max tokens to generate |
| `temperature` | `0.7` | Sampling temperature |
| `system_prompt` | `"You are a helpful assistant."` | Prepended to every conversation |
| `stream` | `true` | Stream tokens as they arrive |
| `theme` | `dark` | UI theme preference: `system`, `dark`, or `light` |
| `window_geometry` | `""` | Auto-saved window size/position (base64) |
| `splitter_state` | `""` | Auto-saved sidebar/chat split position (base64) |
| `memory_enabled` | `true` | Enable sliding-window memory |
| `memory_window_size` | `20` | Number of recent messages kept verbatim |
| `memory_use_vector` | `false` | Enable Chroma vector retrieval (needs `chromadb`) |
| `memory_top_k` | `4` | Older messages recalled per turn |
| `memory_embed_model` | `text-embedding-3-small` | Embedding model for vector memory |
| `memory_embed_url` | `""` | Embedding endpoint (blank = reuse `api_url`) |
| `memory_embed_key` | `""` | Embedding key (blank = reuse `api_key`) |

Individual chats may override `model`, `system_prompt`, and `temperature` via
**⚙ Chat options**; those overrides are stored on the chat row in `chats.db`, not in
`settings.json`. A blank field inherits the global value above.

The window size/position and the sidebar split are remembered automatically (saved on
exit to `window_geometry` / `splitter_state`) and restored on the next launch.

---

## Project layout

```
main.py              App entry point, dark palette, wiring
settings.py          JSON-backed settings + per-OS profile directory
database.py          SQLite persistence (chats + messages)
api_client.py        StreamWorker: background thread that talks to the API
memory.py            Sliding-window + optional vector (Chroma) context builder
chat_io.py           Export (Markdown/JSON) and import (JSON) of chats
utils.py             Markdown → styled HTML + code highlighting
theme.py             Central design tokens (palette, radii, fonts) + shared QSS
ui/
  main_window.py     Main window, menus, splitter layout
  chat_list.py       Left panel: chat list, search, rename/delete
  chat_view.py       Right panel: message history + input box
  message_widget.py  A single chat bubble (token/cost footer)
  settings_dialog.py Settings dialog + model fetcher
```

See [`DESIGN.md`](DESIGN.md) for architecture details and rationale.

---

## License

No license file is currently included. Add one before distributing.
