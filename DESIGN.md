# PyQOA — Design

This document explains how PyQOA is structured, the key design decisions, and the
threading/data-flow model. For usage, see [`README.md`](README.md).

---

## 1. Goals & non-goals

**Goals**

- A responsive, native-feeling desktop chat client for any OpenAI-compatible API.
- Never block the UI thread on network, embedding or tool I/O.
- Local-first persistence — conversations live in a plain SQLite file the user owns.
- Provider-agnostic: cloud (OpenAI), gateways (OpenRouter) and local (Ollama) are all
  first-class, and several can be configured side by side.
- Degrade gracefully when optional dependencies (`markdown`, `Pygments`, `chromadb`,
  `tiktoken`, `keyring`) are missing.
- Non-destructive editing: nothing the user generated should be silently thrown away.

**Non-goals**

- Multi-user / sync / cloud accounts.
- Audio or video modalities (text and images only).
- Encryption-at-rest of the message database (the profile dir is trusted; API keys
  can optionally live in the OS keyring).

---

## 2. High-level architecture

PyQOA follows a loose **Model–View** split with Qt signals as the glue. There is no
global state singleton; four plain objects — `Settings`, `Database`, `ChatMemory`,
`ToolRegistry` — are constructed once and injected into the widgets that need them.

```
                    ┌──────────────┐
                    │   main.py    │  CLI args → Settings, Database, ChatMemory
                    └──────┬───────┘
                           │ injects
                    ┌──────▼───────┐
                    │  MainWindow  │  menus, shortcuts, splitter, ToolRegistry
                    └──┬────────┬──┘
      chat_selected /  │        │  chat_updated / status_updated /
      message_selected │        │  chat_branched / settings_requested
           ┌───────────▼─┐  ┌───▼────────────┐
           │  ChatList   │  │   ChatView     │
           │ (left pane) │  │  (right pane)  │
           └──────┬──────┘  └───┬────────┬───┘
                  │             │        │ spawns
                  │             │   ┌────▼──────────────────┐
                  │             │   │ StreamWorker (QThread)│
                  │             │   │ TitleWorker  (QThread)│
                  │             │   └────┬─────────┬────────┘
                  ▼             ▼        │ uses    │ uses
             ┌─────────┐   ┌──────────┐  │    ┌────▼────────┐
             │Database │   │ChatMemory│◄─┘    │ToolRegistry │
             └─────────┘   └────┬─────┘       └────┬────────┘
                                │ (optional)       │ (optional)
                                ▼                  ▼
                          vector_memory/     MCP servers (stdio)
```

**Data flow for one turn**

1. User types in `ChatView` and presses Enter.
2. `ChatView` runs `api_client.preflight()`. A configuration problem shows an inline
   banner and the turn stops there — no request, no orphaned message.
3. The user message is written to `Database`, its attachments are stored, and a user
   bubble plus an empty streaming assistant bubble are added.
4. `ChatView` starts a `StreamWorker` (a `QThread`).
5. The worker builds the message list (via `ChatMemory`), calls the API, and emits
   `chunk_received` for each token.
6. `ChatView` appends each chunk to the assistant bubble, which live-renders settled
   Markdown paragraphs.
7. If the model asked for tools, the worker runs them and loops (§11).
8. On `completed`, the worker emits the full text; `ChatView` re-renders the bubble,
   persists the assistant message with its token usage and model, and titles the chat
   if it is still unnamed (§9).

---

## 3. Module responsibilities

| Module | Responsibility |
| --- | --- |
| `main.py` | CLI parsing, Qt app/palette/font, dependency wiring, clean DB shutdown. |
| `version.py` | The version string, used by the window title, `--version` and the User-Agent. |
| `selftest.py` | Headless check of the bundled feature surface, behind `--selftest`. |
| `settings.py` | `Settings`: atomic JSON config, defaults, provider profiles, per-OS profile dir. |
| `keystore.py` | Optional OS-keyring storage for API keys; every call fails soft. |
| `database.py` | `Database`: schema, migrations, CRUD, FTS5 search, variants, branching, usage. |
| `api_client.py` | `StreamWorker`/`TitleWorker`, retries, tool loop, preflight, error mapping. |
| `tools.py` | Built-in tools + `ToolRegistry` (specs, dispatch, MCP lifecycle). |
| `mcp_client.py` | `MCPClient`: JSON-RPC over an MCP server's stdio transport. |
| `memory.py` | `ChatMemory`: window + token budget + optional vector recall. |
| `attachments.py` | Reading files, storing them, rendering multi-part API content. |
| `tokens.py` | Token counting via `tiktoken`, with a character heuristic fallback. |
| `pricing.py` | Model price table and `estimate_cost`. |
| `chat_io.py` | Export (Markdown/JSON/HTML/PDF), import, export-all, backup. |
| `utils.py` | `render_markdown`, code highlighting, streaming split helpers, `asset_path`. |
| `theme.py` | Colour and font-size tokens, palettes, font scale, shared stylesheets. |
| `ui/main_window.py` | Top-level window, menu, shortcuts, panel wiring, registry lifetime. |
| `ui/chat_list.py` | Chat list with pins/folders/archive, search results, multi-select. |
| `ui/chat_view.py` | Message history, composer, attachments, find bar, stream lifecycle. |
| `ui/message_widget.py` | One chat bubble: markdown, attachments, variants, token footer. |
| `ui/settings_dialog.py` | Settings tabs, chat options, MCP server and prompt-library dialogs. |
| `ui/usage_dialog.py` | Token/cost dashboard rendered as themed HTML. |

---

## 4. Threading model

Qt requires all widget interaction to happen on the GUI thread, while network calls
must not. PyQOA uses Qt's worker-thread pattern:

- **`StreamWorker(QThread)`** owns all blocking work: building the prompt
  (embeddings can block), opening the client, iterating the stream, and running tools.
- It communicates back to the UI **only** through signals (`chunk_received`,
  `completed`, `error`, `usage_received`, `context_built`, `tool_activity`,
  `retrying`). Qt delivers these as queued connections across the thread boundary, so
  the UI thread is never touched from the worker.
- The completion signal is named **`completed`, not `finished`** — `QThread` already
  defines `finished`, and shadowing it means nothing fires when the thread ends
  without reaching the emit. That is exactly what used to happen on **Stop**: the
  composer stayed disabled forever. `run()` now emits `completed` on every path,
  including cancellation, and the partial text comes with it so a stopped reply is
  saved rather than discarded.
- **Cancellation** is cooperative: `cancel()` sets a flag checked by the read loop and
  by the retry backoff sleep (which sleeps in 50 ms slices so Stop stays responsive).
  When the user switches chats mid-stream, `ChatView` *blocks the worker's signals
  before cancelling* — otherwise the final `completed` emission would race in after
  `current_chat_id` changed and save the reply into the wrong chat.
- **`TitleWorker(QThread)`** does the same for the one-shot title request; it fails
  silently, because a generated title is a nicety on top of the fallback title that
  is already in place.
- On window close, `MainWindow.closeEvent` cancels, `wait()`s (bounded), and shuts
  down any MCP subprocesses, so nothing outlives the app.

Why `QThread` and not `asyncio`? The OpenAI SDK's streaming iterator is synchronous
and blocking, and Qt's event loop is the natural scheduler here. A `QThread` keeps the
code simple and avoids bridging two event loops.

---

## 5. Persistence

SQLite via the stdlib `sqlite3`, opened with `check_same_thread=False` because the
worker thread reads messages while building context.

**Schema**

```sql
chats(id, title, created_at, updated_at,
      model, system_prompt, temperature,       -- per-chat overrides (NULL = inherit)
      pinned, archived, folder)
messages(id, chat_id→chats.id ON DELETE CASCADE,
         role, content, prompt_tokens, completion_tokens, created_at,
         model,                                -- what actually produced this reply
         variant_group, active)                -- alternate replies (§9)
attachments(id, message_id→messages.id ON DELETE CASCADE,
            kind, name, mime, data, created_at)
messages_fts                                   -- FTS5 external-content index
```

Design notes:

- **`id` is the source of truth for ordering**, not `created_at`. SQLite's
  `datetime('now')` has only one-second resolution, so two messages saved in the same
  second are not ordered reliably by timestamp. Messages are read with
  `ORDER BY id ASC`; chats sort by `pinned DESC, updated_at DESC, id DESC`.
- **Foreign keys are enabled** so deleting a chat cascades to its messages, and
  deleting a message cascades to its attachments.
- **Forward-compatible migration**: new columns are added with idempotent
  `ALTER TABLE … ADD COLUMN` guarded by a caught `OperationalError`. **Indexes are
  created after that loop**, because several of them cover columns an old database
  only gains inside it.
- **`messages.model`** records which model produced each reply. Without it, historical
  cost estimates would be priced with whatever model happens to be selected today, and
  the usage dashboard could not break usage down per model.
- **Search** uses an FTS5 *external content* index, so message text is stored once and
  kept in sync by three triggers. `COUNT(*)` on an external-content table reads through
  to `messages` and therefore cannot detect a stale index — a stamped `PRAGMA
  user_version` (`SCHEMA_VERSION`) drives the one-off `'rebuild'` instead. If this
  build of SQLite has no FTS5, `fts_enabled` stays false and `search_messages` falls
  back to a `LIKE` scan, so search always works.
- **Query text is escaped, not passed through**: every token is quoted (punctuation
  can't be read as FTS syntax) and the last one gets a `*` so search feels live while
  typing. A malformed `MATCH` still falls back to `LIKE` rather than raising.
- The system prompt is **not** stored per message — it is prepended at request time
  from the current settings, so changing it applies to existing chats.

---

## 6. Chat memory (context construction)

Implemented in `memory.py` as three layers, all off the UI thread.

**Layer 1 — sliding window (always on).**
The last `memory_window_size` messages are sent verbatim. If the whole chat fits in
the window, that is the entire context and no vector work happens.

**Layer 2 — token budget (optional).**
When `memory_max_tokens` is set, `_fit_budget` walks the window newest-first and stops
before the budget is exceeded. A message-count window is a poor proxy for context
size: twenty long messages can blow a context window that twenty short ones would not
fill. The most recent message is always kept — a budget too small for even one message
should still send something.

**Layer 3 — vector retrieval (optional, needs `chromadb`).**
When a chat grows beyond the window, *older* messages are embedded (lazily, only the
ones not yet indexed) into a per-chat Chroma collection. Before each request the new
user query is embedded and the top-K most similar older messages are retrieved and
inserted between two system markers, ahead of the sliding window:

```
[system] Relevant earlier context retrieved …
[recalled messages …]
[system] End of retrieved context. Continue …
[sliding window of recent messages …]
```

Design notes:

- **Embeddings are pluggable.** They use the chat endpoint by default, or a separate
  `memory_embed_url`/`memory_embed_key` — so chat can run on Ollama while embeddings
  run on OpenAI, or vice versa.
- **Everything fails soft.** If Chroma is missing, the embedder is unconfigured, or any
  embedding/query call throws, the methods return empty and the app falls back to the
  plain sliding window.
- **Indexing is incremental** — already-embedded ids are skipped, and the "what is
  already indexed" probe asks Chroma for `include=[]` so it doesn't drag every stored
  document back over the wire on each turn.
- **Retraction is honoured.** Editing or regenerating deletes messages, and
  `ChatView` calls `ChatMemory.forget_messages()` with the ids the delete returned.
  Without that step a retracted message stays in the vector store and keeps being
  recalled into later prompts even though it is no longer part of the conversation.
- The retrieved block is charged against the token budget but never trimmed — trimming
  it would leave the two system markers wrapping nothing.
- Deleting a chat calls `reset_chat`, which drops its Chroma collection.

---

## 7. Rendering pipeline

`utils.text_to_html` turns assistant Markdown into inline-styled HTML for Qt's rich
text engine (which supports only a CSS subset, so styles are inlined per element):

1. `markdown` renders Markdown → HTML (`fenced_code`, `tables`, `nl2br`, `sane_lists`).
2. `_post_process_code_blocks` rewrites `<pre><code class="language-X">` blocks into
   styled containers, with `Pygments` for highlighting.
3. If `markdown` is unavailable, `_simple_md` provides a regex-based fallback covering
   code fences, inline code, bold/italic, and headers.
4. `render_markdown(..., for_export=True)` swaps the Qt-specific inline styles and the
   `pyqoacopy:` links for plain, theme-independent blocks — the HTML and PDF exports
   share the display pipeline without inheriting a dark bubble's colours.

**Streaming.** `MessageWidget.append_chunk` accumulates raw text and schedules a
throttled (220 ms) re-render. Each tick splits the text with `utils.stable_prefix`:
everything up to the last blank line *outside an open code fence* is rendered as
Markdown, and the tail stays plain text. Rendering the tail too would re-flow a
half-written table or code block on every token. Past `LIVE_RENDER_MAX_CHARS` the
widget gives up on live rendering, resyncs once as plain text, and switches to cheap
cursor appends — re-laying out a very long document per tick costs more than it gains.
`finalize()` always does one last full render.

`_AutoTextEdit` resizes each bubble to its content height so bubbles grow naturally
inside the scroll area instead of showing inner scrollbars.

**Visual design.** All colours, radii, fonts *and font sizes* live as tokens in
`theme.py`; widgets reference them instead of hard-coding values. `theme.global_qss()`
is applied once on the `QApplication` to style scrollbars, menus and tooltips app-wide.
The message list is a single column centred to `CONTENT_MAX_WIDTH`, each message is a
content-hugging bubble with a circular role avatar, and a trailing layout stretch keeps
bubbles top-aligned. The composer auto-grows from one line up to a cap.

**Theming (light/dark).** `theme.py` holds two palettes (`_DARK`, `_LIGHT`) with
identical token names; `theme.apply(name)` rebinds the module-level token attributes.
Because most widget stylesheets are baked into f-strings at construction time, a switch
re-applies the app palette + global QSS and then *rebuilds the central widget*
(`MainWindow._rebuild_ui`) so every widget restyles (the current chat is reloaded
afterwards). Two details make this robust: stylesheet strings that must follow the
theme are produced by *functions* called per use rather than import-time constants; and
message-bubble colours are resolved per instance in `_setup_ui`.

**Font scale.** The `FS_*` size tokens are derived from `_FONT_BASE` and the user's
`font_scale`; `theme.set_font_scale()` rebinds them exactly like `apply()` rebinds the
colours, and the same rebuild path repaints the UI. That is why the scale change lives
next to the theme switch rather than in a stylesheet of its own — one mechanism, two
kinds of token. `apply()` only replaces palette keys, so switching theme never resets
the font scale.

**System theme.** The persisted `theme` value is a *preference* — `system`, `light`, or
`dark` — kept distinct from the *resolved* palette. `theme.resolve(pref)` queries the OS
via `QStyleHints.colorScheme()` (Qt 6.5+, guarded) when the preference is `system`, and
`MainWindow` re-resolves live on `colorSchemeChanged`.

---

## 8. Cost estimation

`pricing.estimate_cost` maps a model name to an entry in a small static price table.
Because model names are matched by substring, the **longest** matching key wins —
otherwise `gpt-4o-mini` would be mispriced as `gpt-4o`, and `o1-mini` as `o1`. Prices
are approximate and may drift; an unknown model simply shows no cost. It lives in its
own module because both a message bubble and the usage dashboard need it, and neither
should import the other.

The usage dashboard reads only what is already stored — per-message token counts,
timestamps and model — so it needs no extra bookkeeping and works retroactively for
every message recorded since the `model` column existed.

---

## 9. Non-destructive editing: variants, branching, edit, export

The old edit/regenerate primitive simply deleted messages. Two features soften that.

**Variants.** Regenerating no longer discards the previous reply.
`Database.begin_variant(id)` marks the reply inactive and stamps a `variant_group`
(seeded with the first variant's own id, so every alternate answer to one prompt shares
a group). The new reply is inserted with the same group. `get_messages` returns only
`active=1` rows, so the conversation stays linear, while the bubble shows a `‹ n/N ›`
switcher backed by `get_variants` / `set_active_variant`. Regeneration still truncates
what came *after* the reply (`truncate_after`), because those turns answered a reply
that is being replaced; branching is the tool for keeping them.

**Branching.** `Database.branch_chat(chat_id, message_id)` copies the chat's overrides,
folder, messages (`active=1 AND id<=message_id`) and their attachments into a brand-new
chat, leaving the original untouched. This is the non-destructive counterpart to edit.

**Edit.** Still expressed as `delete_messages_from(chat_id, message_id)` — `id >=
message_id`, relying on the id-as-order invariant from §5, plus any inactive siblings of
the deleted variants so no orphaned alternates are left behind. The method **returns the
deleted ids**, which is what lets `ChatView` tell `ChatMemory` to forget them (§6).

**Export / import.** `chat_io` serialises a chat to Markdown, HTML or PDF
(human-readable, one-way) or JSON (round-trippable: roles, token counts, timestamps,
per-chat overrides, model and base64 attachments — `JSON_VERSION` is 2). PDF reuses the
HTML export through `QPdfWriter`, so there is one rendering path rather than two.
`export_all` writes one file per chat (archived ones included — a full export means
full), and `backup` zips the database beside a copy of the settings with every API key
blanked. The database is copied before zipping: zipping a live SQLite file can capture a
torn page.

---

## 10. Settings, profiles and transport

**Atomic writes.** `Settings.save()` writes to a temp file in the same directory,
fsyncs, then `os.replace`s it. An interrupted save used to be able to truncate
`settings.json` — including the API key.

**Provider profiles.** `profiles` holds one dict per named provider; the active
profile's fields (`PROFILE_KEYS`) are *mirrored to the top level* of the settings dict.
That mirroring is deliberate: every call site keeps using `settings.get("api_url")` and
knows nothing about profiles. `activate_profile` writes the outgoing profile's live
values back before switching, so unsaved edits are not lost, and `save()` syncs the
active profile first. Anything read on a per-request basis — URL, key, model, timeout,
generation parameters, headers, proxy — is a profile field; anything about the app
itself (theme, memory, tools, prompts) is global.

**Keyring.** With `use_keyring` on, `save()` writes each key into the OS credential
store and blanks it in the JSON payload; `_restore_secrets` reads them back on load.
`keystore.available()` probes the backend once and treats the `fail` backend as absent,
so a headless Linux box silently keeps using plaintext rather than losing the key.

**Transport.** `build_client` attaches `default_headers` (always including a versioned
User-Agent, applied case-insensitively so a user-configured `user-agent` replaces it
rather than joining it) and, when a proxy is configured, a dedicated
`openai.DefaultHttpxClient`. That class rather than an `httpx.Client` of our own is
deliberate: openai 2.x is built on `httpx` and 3.x on `httpx2`, so importing either
directly makes PyQOA depend on a package the installed SDK may not pull in — which is
exactly how CI broke once (§12). `DefaultHttpxClient` subclasses whichever the SDK
uses. It sets the
SDK's own `max_retries=0` on purpose: retries belong to `StreamWorker`, where they can
be cancelled, backed off with the user's own delay, and surfaced through the `retrying`
signal. `_is_retryable` retries connection errors, timeouts, 429 and 5xx; it does not
retry 401/404/400, where retrying only wastes the user's time.

**Preflight and error mapping.** `preflight()` catches the three configuration mistakes
that produce the most confusing SDK exceptions (no URL, no model, no key on a remote
endpoint). `friendly_error()` maps the SDK's exception classes to an explanation plus
the next action. `ChatView` shows both in an inline banner with an **Open Settings**
button instead of a modal stack trace.

**Provider presets and validation** live in `settings_dialog.py`; the "is this endpoint
local (so no key is needed)" rule is `api_client.is_local_url`, used by both the dialog
and preflight so the two can never disagree.

**Window state.** Geometry and splitter position are persisted as base64 Qt save blobs
and restored on startup (and carried across a theme or font rebuild). Decoding tolerates
missing/corrupt values.

---

## 11. Tools and MCP

`ToolRegistry` resolves the tool set for a request and dispatches calls by name.

- **Built-ins** are declared in one `BUILTINS` table (callable, description, JSON
  schema, and whether it touches the network). `calculate` evaluates through a
  restricted AST walker — literals, arithmetic operators, a fixed function whitelist —
  rather than `eval`, so an expression cannot reach the Python runtime. `fetch_url`
  is capped in both time and bytes and is flagged as network-touching in the UI so
  enabling it is a deliberate act.
- **MCP tools** come from servers spawned on demand. `MCPClient` speaks
  newline-delimited JSON-RPC 2.0 over the server's stdio: `initialize`, the
  `notifications/initialized` handshake, `tools/list`, `tools/call`. Reads skip
  notifications and non-JSON log lines until the reply with the matching id arrives, and
  every failure becomes an `MCPError`. Tool names are namespaced and sanitised to
  `mcp__<server>__<tool>` so they satisfy the API's function-name rules and can't
  collide with a built-in.
- **Dispatch never raises.** `ToolRegistry.call` turns bad JSON arguments, unknown
  names and server errors into a string the model reads as tool output, because a
  tool failure should let the model recover rather than abort the turn.

**The loop** lives in `StreamWorker.run`. Each round sends the accumulated messages
with the tool specs attached; if the reply contains tool calls, the assistant message
(with its `tool_calls`) and one `role: "tool"` message per result are appended and the
next round runs. Streaming complicates this: tool calls arrive as fragments, so
`_merge_tool_call_deltas` reassembles them by index, concatenating name and argument
fragments. `tool_max_rounds` bounds the loop, and hitting the bound is reported through
`tool_activity` rather than passing silently. Text produced in earlier rounds is kept
and joined, so a model that narrates before calling a tool doesn't lose that narration.

Servers are started lazily on the first request that needs them and stopped by
`MainWindow` on close or whenever settings change, so an edited configuration never
keeps talking to a stale subprocess.

---

## 12. Testing

`tests/` holds 257 tests that need no network and no display.

- **Pure logic** (settings, database, memory, tokens, pricing, utils, theme, chat_io,
  attachments, tools) is tested directly.
- **The API is faked, not mocked.** `tests/fake_api.py` runs a threaded
  `ThreadingHTTPServer` that speaks the OpenAI wire protocol — SSE chunks, usage
  chunks, tool-call deltas, error statuses — from a per-test script. That exercises the
  real `openai` SDK, the real streaming parser, the real retry path and the real tool
  loop, which a mocked client would not.
- **MCP is faked the same way**: a small scripted Python script acts as a real stdio
  MCP server, so the client is tested against actual subprocess framing.
- **Widgets** are tested with `pytest-qt` under `QT_QPA_PLATFORM=offscreen`
  (set in `conftest.py`), including a full send round-trip through `ChatView` against
  the fake API.
- **The packaging is tested too** (`test_packaging.py`): the shell scripts parse and
  fail fast, every Makefile target is declared and `.PHONY`, the desktop entry has
  exactly one main category, the man page documents every argparse flag, the version
  in the man page matches `version.py`, and the CI workflow parses, gates packaging
  behind the test jobs, bounds every job in time and passes `--fail-on` to lintian
  (without it, lintian reports tags and still exits 0). These are the parts that rot
  quietly because nobody runs them until release day.
- **Nothing asserts a wire detail the SDK owns.** Request headers are compared through
  a case-insensitive mapping in `fake_api.Headers`, because HTTP field names are
  case-insensitive (RFC 9110) and the SDK's casing is not ours to depend on: openai
  2.x forwards `X-Title` verbatim while 3.x lowercases it, which is exactly the kind
  of test that fails in CI having proved nothing about PyQOA. For the same reason
  `_sdk_http()` asks the SDK which httpx flavour it is built on instead of importing
  one, since its exception types want a matching response object.
- **Unpinned dependencies are checked by CI, not by hope.** `requirements.txt` has no
  upper bounds, so CI resolves the newest `openai` every run. That has already caught
  two breaking changes across a major bump: the header casing above, and openai 3.x
  moving from `httpx` to `httpx2`, which exposed `api_client` importing `httpx` as an
  undeclared dependency it had only ever received transitively. A test now asserts
  that no module imports `httpx` directly.
- **The optional dependencies are tested by their absence too.** `requirements.txt`
  splits into `requirements-core.txt` plus the three optional packages, and a CI job
  installs only the core set. Guarded imports are easy to write and easy to break, and
  the frozen build ships without `chromadb` anyway, so that configuration deserves to
  be a tested one rather than an assumed one.

`selftest.py` is the runtime counterpart: `pytest` proves the *source* works, and
`pyqoa --selftest` proves a *build* works. PyInstaller can silently drop a Markdown
extension, a Pygments lexer, the Qt SVG image plugin or a keyring backend, and none
of those failures surface until a user hits them, so `make verify-binary` runs the
same checks inside the frozen bundle.

---

## 13. Packaging

`make` is the single entry point for tests and builds; the interesting decisions are
in `packaging/`.

**Frozen bundle over a native package.** `packaging/pyqoa.spec` builds a PyInstaller
bundle that carries its own Python and Qt. A Debian-native package would have to
depend on `python3-pyqt6`, `python3-openai`, `python3-tiktoken` and friends at
versions no two distribution releases agree on; vendoring trades ~60 MB of download
for installing anywhere. `chromadb` is the one dependency left out — it drags in
`onnxruntime` and a model download, which would multiply the size for an optional
feature, so the spec excludes it (along with the `numpy`/`uvloop`/`yaml` stack that
comes with it) and vector memory reports itself unavailable.

**Both bundle shapes coexist.** One-directory output lands in `dist/pyqoa/` and
one-file in `dist/onefile/pyqoa`: they cannot share a path, because one is a
directory and the other is a file with the same name. `utils.asset_path` resolves
bundled data through `sys._MEIPASS` when frozen and `packaging/` from a checkout, so
the icon loads in every shape.

**One layout definition.** `packaging/install-tree.sh` stages the tree —
`$PREFIX/lib/pyqoa` for the bundle, a two-line wrapper in `$PREFIX/bin` (rather than
a symlink, so PyInstaller's own path resolution stays unambiguous), plus the desktop
entry, icon, man page and docs. Both `make install` and `build-deb.sh` call it, so
the deb and a manual install cannot drift apart. It also normalises modes, since a
build tree otherwise inherits the developer's umask and `dpkg` ships whatever it is
handed.

**The deb needs only `dpkg-deb`** — no debhelper, and no `fakeroot` thanks to
`--root-owner-group`. Its `Depends` were derived by running `ldd` across the bundle
and mapping the unresolved sonames to packages: `libc6`, `libgl1`, `libxcb1` and
`libglib2.0-0 | libglib2.0-0t64` (the alternation covers Ubuntu's `t64` rename).
Everything else is vendored. The package is lintian-clean: the executable bit is
cleared on shared objects, doc file modes are fixed, a man page and machine-readable
copyright are shipped, and `usr/share/lintian/overrides/pyqoa` acknowledges the tags
that are inherent to vendoring (`embedded-library`, `unstripped-binary-or-object`,
`hardening-no-pie`) with the reason written next to them.

**No wheel.** The modules live at the top level of the repository, so installing them
into `site-packages` would claim names like `utils`, `tools`, `settings` and `memory`.
`make sdist` produces a source tarball instead; a real wheel needs the modules moved
into a `pyqoa/` package first.

---

## 14. Known limitations / future work

- The `stream_options.include_usage` probe falls back to a plain stream if the endpoint
  rejects it, but only when no content has streamed yet (to avoid duplicate output);
  some non-OpenAI endpoints therefore won't report token usage.
- Reasoning-model detection is name-based (`o<digit>` after any provider prefix); an
  endpoint that exposes o-series models under unusual names won't be auto-detected.
- A per-chat **empty** system prompt is treated as "inherit", so a chat can't force the
  model to run with *no* system prompt while a global one is set.
- The price table is static and will drift; unknown models report no cost rather than
  a wrong one.
- Attachments are stored as BLOBs in the same SQLite file, so a chat history heavy with
  images grows the database rather than a side folder.
- Tool results are not persisted — the reply text and the list of tools used are
  stored, but the intermediate tool messages are rebuilt only within a single turn.
- MCP support covers tools only (no resources, prompts, or sampling), and only the
  stdio transport.
- Vector memory embeds message text alone; attachments are not indexed.
- The `.deb` and the tarball are built for the host architecture only; there is no
  cross-build or arm64 CI job yet, and no signed repository to install from.
- The frozen build cannot offer vector memory (see §13), so that feature is
  source-install only.
- No pip-installable wheel until the modules move into a package (see §13).

See the README's feature list for the full set of implemented capabilities.
