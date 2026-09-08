# PyQOA

**Py**Qt6 **O**penAI-compatible **A**ssistant — a cross-platform desktop chat client
for any OpenAI-compatible API: the OpenAI cloud, a local [Ollama](https://ollama.com)
server, OpenRouter, or any custom endpoint that speaks `/v1/chat/completions`.

PyQOA stores your conversations locally in SQLite, streams responses token-by-token,
renders Markdown with syntax-highlighted code blocks, calls tools (built-in or from
any [MCP](https://modelcontextprotocol.io) server), accepts image and file
attachments, tracks token usage and estimated cost, and offers an optional
vector-memory layer for long conversations.

> Despite the name, PyQOA is **not** OpenAI-only — point it at Ollama or any
> compatible gateway in **Settings → Provider**, and keep one saved profile per
> provider.

Current version: **0.2.0**.

---

## Features

### Providers & transport

- **Named provider profiles** — keep OpenAI, Ollama, OpenRouter and a work gateway
  side by side, each with its own URL, key, model, timeout and parameters. Switch
  from **Tools → Provider Profile**, or start on one with `--profile NAME`.
- **One-click presets** for OpenAI and Ollama, plus Custom for anything else.
- **Live model discovery** — *Fetch Models* queries the endpoint (`/v1/models`, or
  Ollama's `/api/tags`) and populates the model dropdown.
- **Custom headers and proxy** — add `HTTP-Referer` / `X-Title` for OpenRouter, an
  auth header for a corporate gateway, or route everything through an HTTP proxy.
- **Retries with backoff** — transient failures (429, 5xx, connection drops, timeouts)
  are retried with exponential backoff and a visible "retrying (2/3)…" status. Errors
  that retrying cannot fix (401, 404, 400) fail immediately.
- **Readable errors** — a bad key, a missing model or an unreachable server produces
  a plain-English banner with an **Open Settings** button, not a stack trace. The
  configuration is also checked *before* a request is sent.
- **Reasoning-model aware** — for OpenAI o-series models (`o1`, `o3`, `o4-mini`, …)
  PyQOA sends `max_completion_tokens` and omits the unsupported `temperature`.

### Conversations

- **Streaming responses** with a **Stop** button. Stopping now **keeps** the partial
  reply instead of throwing it away.
- **Live Markdown** — the reply renders as Markdown *while* it streams (settled
  paragraphs only, so half-written code fences never flicker). Switchable in Settings.
- **Rich rendering** — tables, lists, blockquotes and Pygments-highlighted fenced code
  with a one-click **Copy** per block.
- **Branching** (`⑂`) — fork any message into a new chat that keeps everything up to
  that point. The original is untouched.
- **Regenerate keeps alternatives** — `↻` produces another answer beside the old one;
  a `‹ 2/3 ›` switcher on the bubble flips between them.
- **Edit & resend** a user message (truncating the conversation from that point).
- **Attachments** — attach images (sent as vision content parts) or text files (appended
  as fenced blocks). Thumbnails and file chips appear on the message.
- **Tool calling** — the model can call built-in tools (`current_time`, `calculate`,
  `fetch_url`) and any tool exposed by a configured **MCP server** over stdio. Tools
  are off by default and individually switchable.
- **Model-written chat titles** — a new chat gets an instant fallback title, then a
  short generated one (optionally from a cheaper model). Turn it off in Settings.
- **Per-chat overrides** — model, system prompt and temperature per conversation.

### Finding things

- **Full-text search** across every message body (SQLite FTS5, with highlighted
  snippets); sidebar results are grouped under a **Messages** heading and jump
  straight to the matching message.
- **Find in conversation** (`Ctrl+F`) with match counts and next/previous.
- **Pins, folders and archive** — pin chats to the top, group them into folders,
  archive the rest out of sight. Select several to delete at once.

### Everything else

- **Prompt library** (`Ctrl+P`) — save reusable prompts and insert them into the
  composer.
- **Usage dashboard** (`Ctrl+U`) — tokens and estimated cost, per model and per day.
- **Export** a chat to Markdown, JSON, HTML or PDF; **export all chats** at once; or
  take a **backup zip** of the database plus settings (API keys stripped).
- **Import** a JSON export back into a new chat, attachments and all.
- **Token meter** under the composer showing what the next request will cost in
  tokens (exact with `tiktoken`, estimated without) and warning when a token budget
  will drop older messages.
- **Two-layer chat memory**
  - *Sliding window* (always on): the last *N* messages are sent verbatim.
  - *Token budget* (optional): trims the oldest messages until the request fits.
  - *Vector retrieval* (optional, needs `chromadb`): older messages are embedded and
    the most relevant are recalled and prepended.
- **Light, dark & system themes** plus **adjustable font size** (`Ctrl+=` / `Ctrl+-`
  / `Ctrl+0`). Both persist between launches.
- **Optional OS keyring** storage for API keys instead of plaintext `settings.json`.
- **Local-first persistence** — a plain SQLite file you own, with automatic in-place
  schema upgrades from older versions.

---

## Requirements

- Python **3.10+** (uses `X | None` type syntax and `tuple[int, int]` generics)
- See [`requirements.txt`](requirements.txt):
  - `PyQt6` — GUI toolkit (**required**)
  - `openai` — API client (**required**)
  - `markdown`, `Pygments` — Markdown rendering & syntax highlighting (recommended;
    there is a minimal fallback if missing)
  - `chromadb` — vector memory (**optional**)
  - `tiktoken` — exact token counts for the meter and budget (**optional**; a
    character heuristic is used without it)
  - `keyring` — OS keyring storage for API keys (**optional**)

Every optional dependency fails soft: the feature it powers degrades or switches off,
and the rest of the app keeps working.

---

## Installation

### Debian / Ubuntu package

```bash
sudo apt install ./pyqoa_0.2.0-1_amd64.deb
```

The package is self-contained — it bundles its own Python and Qt, so it needs only
a handful of system libraries and installs on any current Debian or Ubuntu. It puts
the application in `/usr/lib/pyqoa`, a launcher at `/usr/bin/pyqoa`, a desktop entry,
an icon and a man page. Build it yourself with `make deb` (see
[Development](#development)).

The only feature not in the package is vector memory (`chromadb`), which is too
large to bundle for an optional extra; use a source install for that.

### Portable tarball

```bash
tar xf pyqoa-0.2.0-linux-x86_64.tar.gz
./pyqoa-0.2.0-linux-x86_64/pyqoa
```

No installation, no root, nothing written outside your profile directory.

### From source

```bash
git clone <this-repo> pyqoa
cd pyqoa

make venv deps          # or: python -m venv .venv && pip install -r requirements.txt
make run
```

To skip every optional dependency, install only the core packages:

```bash
pip install -r requirements-core.txt      # or: make deps-core
```

Each optional package switches a feature off rather than breaking the app:
without `tiktoken` the token meter estimates instead of counting exactly, without
`keyring` API keys stay in `settings.json`, and without `chromadb` vector recall is
unavailable. `pyqoa --selftest` reports which of them are present.

Verify an installation of any kind with:

```bash
pyqoa --selftest
```

---

## Running

```bash
python main.py
```

Command-line options:

| Flag | Meaning |
| --- | --- |
| `--version` | Print the version and exit |
| `--data-dir PATH` | Use an alternative profile directory (settings, database, vectors) |
| `--profile NAME` | Activate a provider profile on startup |
| `--new-chat` | Start in a fresh chat instead of the most recent one |
| `--prompt TEXT` | Open a new chat and send TEXT immediately |
| `--selftest` | Check the bundled feature surface headlessly and exit |

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

### Quick start with OpenRouter (or another gateway)

1. **Settings → Provider → Custom**, base URL `https://openrouter.ai/api/v1`, and your key.
2. **Settings → Advanced → Extra headers**:

   ```
   HTTP-Referer: https://your-site.example
   X-Title: PyQOA
   ```

3. **Fetch Models**, pick one, **Save**.

### Several providers at once

**Settings → Provider → Profile → New** creates another named profile; each stores its
own URL, key, model, parameters, headers and proxy. Switch between them from
**Tools → Provider Profile**, or launch straight into one:

```bash
python main.py --profile Ollama
```

---

## Tool calling and MCP

Tools are **off by default**. Enable them in **Settings → Tools**:

- **Built-ins** — `current_time` and `calculate` are local and offline; `fetch_url`
  makes outbound HTTP requests, so it is listed separately and off unless you tick it.
  `calculate` evaluates arithmetic through a restricted AST walker, not `eval`.
- **MCP servers** — *Add…* a server with its command and arguments, e.g.

  | Field | Value |
  | --- | --- |
  | Name | `filesystem` |
  | Command | `npx` |
  | Arguments | `-y @modelcontextprotocol/server-filesystem /home/you/notes` |

  PyQOA spawns the server over stdio, lists its tools, and offers them to the model as
  `mcp__filesystem__<tool>`. Servers start on first use and are shut down when you
  close the app or change settings.

**Max tool rounds** caps how many times one reply may call tools before PyQOA stops
the loop and says so. Tool activity appears in the status bar, and the finished
message footers show which tools ran.

A model must itself support tool calling for any of this to do anything.

---

## Keyboard shortcuts

| Shortcut | Action |
| -------------- | ----------------- |
| `Enter` | Send message |
| `Shift+Enter` | Insert newline |
| `Ctrl+N` | New chat |
| `Ctrl+F` | Find in conversation |
| `Esc` | Close the find bar |
| `Ctrl+P` | Prompt library |
| `Ctrl+U` | Usage dashboard |
| `Ctrl+,` | Open Settings |
| `Ctrl+Shift+L` | Quick-flip light/dark |
| `Ctrl+=` / `Ctrl+-` / `Ctrl+0` | Font size bigger / smaller / reset |
| `Ctrl+Q` | Quit |

### Message & chat actions

- Hover a message to reveal **Copy** (both roles), **Edit** (your messages),
  **↻ Regenerate** (assistant replies) and **⑂ Branch** (any message).
- **Edit** moves a message's text back into the input box and removes that message
  and everything after it, so you can rephrase and resend.
- **↻ Regenerate** hides the current reply, generates a new one, and shows a
  `‹ n/N ›` switcher so you can compare and pick.
- **⑂ Branch** copies the conversation up to that message into a new chat.
- **⧉ Copy** in a code block's header copies just that snippet.
- **📎** attaches an image or text file to the next message.
- **⚙ Chat options** (chat header) sets per-chat model / system prompt / temperature.
- Right-click a chat in the sidebar to **Rename**, **Pin**, **Move to folder…**,
  **Archive**, **Export** (Markdown/JSON/HTML/PDF) or **Delete**. Select several
  chats to delete them together. The 🗄 button in the sidebar header reveals
  archived chats.
- **File → Import Chat (JSON)…** loads an exported chat; **File → Export All Chats…**
  and **Backup Database…** cover everything at once.

---

## Where your data lives

PyQOA keeps a per-user profile directory (override it with `--data-dir`):

| OS       | Path                                            |
| -------- | ----------------------------------------------- |
| Linux    | `~/.config/pyqoa/`                              |
| macOS    | `~/Library/Application Support/pyqoa/`          |
| Windows  | `%APPDATA%\pyqoa\`                              |

Inside it:

- `settings.json` — your configuration (written atomically; **includes API keys in
  plain text unless the keyring option is on**).
- `chats.db` — SQLite database of all chats, messages and attachments.
- `vector_memory/` — Chroma vector store (only if vector memory is enabled).

> **Security note:** by default API keys are stored unencrypted in `settings.json`.
> Turn on **Settings → Advanced → Store API keys in the OS keyring** to move them into
> your platform's credential store (the option is disabled if no keyring backend is
> available). Backup zips never contain keys.

---

## Configuration reference

All settings are editable from the Settings dialog and persisted to `settings.json`.

### Provider fields

These belong to the **active profile** and are duplicated at the top level of
`settings.json` for the profile currently in use.

| Key | Default | Meaning |
| --- | --- | --- |
| `api_url` | `https://api.openai.com/v1` | Base URL of the OpenAI-compatible endpoint |
| `api_key` | `""` | Bearer token (not required for local endpoints) |
| `model` | `gpt-4o` | Model name sent with each request |
| `timeout` | `60` | Per-request timeout (seconds) |
| `max_tokens` | `4096` | Max tokens to generate |
| `temperature` | `0.7` | Sampling temperature |
| `stream` | `true` | Stream tokens as they arrive |
| `request_headers` | `{}` | Extra HTTP headers sent with every request |
| `proxy` | `""` | HTTP(S) proxy URL; blank uses the system default |

| Key | Default | Meaning |
| --- | --- | --- |
| `profiles` | one derived entry | All provider profiles |
| `active_profile` | `Default` | Which profile the fields above mirror |
| `use_keyring` | `false` | Store API keys in the OS keyring instead of the JSON file |

### Requests

| Key | Default | Meaning |
| --- | --- | --- |
| `max_retries` | `3` | Attempts for a transient failure (429/5xx/timeouts) |
| `retry_base_delay` | `1.0` | First backoff delay in seconds; doubles each retry |
| `system_prompt` | `"You are a helpful assistant."` | Prepended to every conversation |
| `auto_title` | `true` | Let the model name new chats |
| `auto_title_model` | `""` | Model used for titles (blank = the chat's own) |

### Memory

| Key | Default | Meaning |
| --- | --- | --- |
| `memory_enabled` | `true` | Enable sliding-window memory |
| `memory_window_size` | `20` | Number of recent messages kept verbatim |
| `memory_max_tokens` | `0` | Token budget for the request (`0` = message count only) |
| `memory_use_vector` | `false` | Enable Chroma vector retrieval (needs `chromadb`) |
| `memory_top_k` | `4` | Older messages recalled per turn |
| `memory_embed_model` | `text-embedding-3-small` | Embedding model for vector memory |
| `memory_embed_url` | `""` | Embedding endpoint (blank = reuse `api_url`) |
| `memory_embed_key` | `""` | Embedding key (blank = reuse `api_key`) |

### Tools

| Key | Default | Meaning |
| --- | --- | --- |
| `tools_enabled` | `false` | Offer tools to the model |
| `tool_builtins` | `["current_time", "calculate"]` | Which built-ins are exposed |
| `tool_max_rounds` | `5` | Tool round-trips allowed per reply |
| `mcp_servers` | `[]` | `{name, command, args, env, enabled}` entries |

### Interface

| Key | Default | Meaning |
| --- | --- | --- |
| `theme` | `dark` | `system`, `dark`, or `light` |
| `font_scale` | `1.0` | UI font multiplier (0.8 – 1.6) |
| `stream_render` | `true` | Render Markdown live while streaming |
| `attachments_enabled` | `true` | Allow image/file attachments |
| `prompts` | `[]` | Prompt library entries (`{name, text}`) |
| `show_archived` | `false` | Whether the sidebar lists archived chats |
| `window_geometry` | `""` | Auto-saved window size/position (base64) |
| `splitter_state` | `""` | Auto-saved sidebar/chat split position (base64) |

Individual chats may override `model`, `system_prompt` and `temperature` via
**⚙ Chat options**; those overrides are stored on the chat row in `chats.db`, not in
`settings.json`. A blank field inherits the global value above.

---

## Development

Everything goes through the [`Makefile`](Makefile); `make` on its own lists the
targets.

```bash
make venv deps deps-dev    # virtualenv with runtime + test dependencies
make check                 # lint, test and self-test
```

### Tests

257 tests, needing neither network access nor a display: the API is faked by a local
scripted HTTP server that speaks the real OpenAI wire protocol, MCP by a scripted
stdio subprocess, and Qt widget tests run headless via `QT_QPA_PLATFORM=offscreen`
(set by `tests/conftest.py`).

| Target | What it does |
| --- | --- |
| `make test` | Run the suite (`make test PYTEST_ARGS="-k search"` to narrow it) |
| `make deps-core` | Install only the required packages, to test the degraded paths |
| `make test-cov` | Run it with a coverage report |
| `make lint` | pyflakes over every module |
| `make selftest` | Exercise the feature surface (Qt, Markdown, PDF, FTS5, tokens) |
| `make check` | All three |

### Packaging

Building needs PyInstaller (`make deps-build`); the `.deb` additionally needs
`dpkg-deb`, which Debian and Ubuntu already have.

| Target | Output |
| --- | --- |
| `make binary` | `dist/pyqoa/` — one-directory bundle (~190 MB) |
| `make onefile` | `dist/onefile/pyqoa` — single executable (~76 MB) |
| `make tarball` | `dist/pyqoa-<version>-linux-<arch>.tar.gz` |
| `make deb` | `dist/pyqoa_<version>-<rev>_<arch>.deb` (~61 MB) |
| `make deb-lint` | The `.deb` checked with lintian |
| `make sdist` | Source tarball from the git tree |
| `make verify-binary` | Runs `--selftest` inside the built bundle |

`make install PREFIX=/usr/local` installs a built bundle without a package manager
(and `make uninstall` removes it); pass `DESTDIR=` to stage into a build root.
`make clean` drops intermediates, `make distclean` also drops `dist/`.

The build deliberately excludes `chromadb` (and the `numpy`/`onnxruntime` stack it
pulls in): bundling it would multiply the download size for an optional feature. The
frozen build reports it as unavailable in `--selftest`, and vector memory switches
itself off.

### CI

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) has three jobs:

- **Test** — `make check` on Python 3.10 and 3.12 with the full requirements.
- **Test (core dependencies only)** — the same, with `requirements-core.txt`, which
  is what exercises the optional-dependency fallbacks (and matches what the `.deb`
  ships). It fails if `chromadb`, `tiktoken` or `keyring` turn out to be installed
  after all, so the job cannot quietly stop testing what it is for.
- **Build .deb** — only after both test jobs pass: builds the package, runs the
  frozen bundle's `--selftest`, gates on `lintian --fail-on error,warning`, and
  uploads the `.deb` as an artifact.

Because `requirements.txt` gives no upper bounds, CI always resolves the newest
`openai` release; that is deliberate — it is how a breaking SDK change gets noticed
here rather than in a user's install. Pin a ceiling if you would rather not be told.

There is no pip-installable wheel: the modules sit at the top level of the
repository, and names like `utils`, `tools` and `settings` would collide with other
packages in `site-packages`. Moving them into a `pyqoa/` package would fix that and
is the obvious next step if a wheel is wanted.

---

## Project layout

```
Makefile             Development, test and packaging tasks (run `make` for a list)
main.py              App entry point, CLI arguments, dependency wiring
version.py           Single source of truth for the version
selftest.py          Headless feature-surface check behind --selftest
settings.py          JSON-backed settings, provider profiles, per-OS profile dir
keystore.py          Optional OS-keyring storage for API keys
database.py          SQLite persistence (chats, messages, attachments, FTS5)
api_client.py        StreamWorker/TitleWorker: requests, streaming, retries, tools
tools.py             Built-in tools + the registry that also exposes MCP tools
mcp_client.py        Minimal MCP client over the stdio transport
memory.py            Sliding window + token budget + optional vector recall
attachments.py       Reading files and turning them into API content parts
tokens.py            Token counting (tiktoken, with a heuristic fallback)
pricing.py           Model price table and cost estimation
chat_io.py           Export (Markdown/JSON/HTML/PDF), import, export-all, backup
utils.py             Markdown → styled HTML, code highlighting, streaming helpers
theme.py             Design tokens (palette, radii, fonts, font scale) + shared QSS
ui/
  main_window.py     Main window, menus, shortcuts, splitter layout
  chat_list.py       Left panel: pins/folders/archive, search results
  chat_view.py       Right panel: history, composer, find bar, stream lifecycle
  message_widget.py  A single chat bubble (variants, attachments, token footer)
  settings_dialog.py Settings tabs, chat options, MCP server and prompt dialogs
  usage_dialog.py    Token/cost dashboard
packaging/
  pyqoa.spec         PyInstaller spec (one-dir and one-file)
  build-binary.sh    Frozen-bundle build
  build-tarball.sh   Portable .tar.gz
  build-deb.sh       Self-contained .deb (control, overrides, md5sums)
  install-tree.sh    Shared filesystem layout for `make install` and the .deb
  pyqoa.desktop      Desktop entry
  pyqoa.1            Man page
  icons/pyqoa.svg    Application icon
tests/               pytest suite (unit + Qt widget + faked end-to-end + packaging)
.github/workflows/   CI: tests on 3.10/3.12, then a lintian-clean .deb
```

See [`DESIGN.md`](DESIGN.md) for architecture details and rationale.

---

## License

No license file is currently included. Add one before distributing.
