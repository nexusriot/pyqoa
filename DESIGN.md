# PyQOA — Design

This document explains how PyQOA is structured, the key design decisions, and the
threading/data-flow model. For usage, see [`README.md`](README.md).

---

## 1. Goals & non-goals

**Goals**

- A responsive, native-feeling desktop chat client for any OpenAI-compatible API.
- Never block the UI thread on network or embedding I/O.
- Local-first persistence — conversations live in a plain SQLite file the user owns.
- Provider-agnostic: cloud (OpenAI) and local (Ollama) are first-class.
- Degrade gracefully when optional dependencies (`markdown`, `Pygments`, `chromadb`)
  are missing.

**Non-goals**

- Multi-user / sync / cloud accounts.
- Tool-calling, function-calling, or image/audio modalities (text chat only today).
- Encryption-at-rest of settings or history (the profile dir is trusted).

---

## 2. High-level architecture

PyQOA follows a loose **Model–View** split with Qt signals as the glue. There is no
global state singleton; three plain objects — `Settings`, `Database`, `ChatMemory` —
are constructed once in `main()` and injected into the widgets that need them.

```
                         ┌──────────────┐
                         │   main.py    │  builds Settings, Database, ChatMemory
                         └──────┬───────┘
                                │ injects
                         ┌──────▼───────┐
                         │  MainWindow  │  menus, shortcuts, splitter
                         └──┬────────┬──┘
              chat_selected │        │ chat_updated / status_updated
                 ┌──────────▼──┐  ┌──▼─────────────┐
                 │  ChatList   │  │   ChatView     │
                 │ (left pane) │  │  (right pane)  │
                 └──────┬──────┘  └───┬────────┬───┘
                        │             │        │ spawns
                        │             │   ┌────▼─────────┐
                        │             │   │ StreamWorker │ (QThread)
                        │             │   └────┬─────────┘
                        ▼             ▼        │ uses
                   ┌─────────┐   ┌──────────┐  │
                   │Database │   │ChatMemory│◄─┘
                   └─────────┘   └────┬─────┘
                                      │ (optional) embeddings + Chroma
                                      ▼
                                 vector_memory/
```

**Data flow for one turn**

1. User types in `ChatView` and presses Enter.
2. `ChatView` writes the user message to `Database`, adds a user bubble, and adds an
   empty streaming assistant bubble.
3. `ChatView` starts a `StreamWorker` (a `QThread`).
4. The worker builds the message list (via `ChatMemory` if enabled), calls the API,
   and emits `chunk_received` for each token.
5. `ChatView` appends each chunk to the assistant bubble (fast plain-text insert).
6. On `finished`, the worker emits the full text; `ChatView` re-renders the bubble as
   Markdown, persists the assistant message + token usage, and (for the first turn)
   derives the chat title from the first user message.

---

## 3. Module responsibilities

| Module | Responsibility |
| --- | --- |
| `main.py` | Process entry, Qt app/palette/font, dependency wiring, clean DB shutdown. |
| `settings.py` | `Settings`: JSON-backed config with defaults, per-OS profile dir, `db_path`. |
| `database.py` | `Database`: SQLite schema + CRUD for chats and messages, token totals. |
| `api_client.py` | `StreamWorker`: off-thread request/stream, usage tracking, cancellation, per-chat overrides, reasoning-model params. |
| `memory.py` | `ChatMemory`: builds the per-turn context (window + optional vector recall). |
| `chat_io.py` | Export a chat to Markdown/JSON and import a JSON export into a new chat. |
| `utils.py` | `render_markdown`: Markdown → inline-styled HTML; code highlighting + copy links. |
| `theme.py` | Design tokens (palette, radii, fonts) and shared stylesheet builders. |
| `ui/main_window.py` | Top-level window, menu, shortcuts, panel wiring. |
| `ui/chat_list.py` | Chat list, search filter, rename/delete, custom list items. |
| `ui/chat_view.py` | Message history rendering, input box, stream lifecycle. |
| `ui/message_widget.py` | One auto-sizing chat bubble + token/cost footer. |
| `ui/settings_dialog.py` | Provider presets, model fetcher, validation, persistence. |

---

## 4. Threading model

Qt requires all widget interaction to happen on the GUI thread, while network calls
must not. PyQOA uses Qt's worker-thread pattern:

- **`StreamWorker(QThread)`** owns all blocking work: building the prompt
  (embeddings can block), opening the OpenAI client, and iterating the stream.
- It communicates back to the UI **only** through signals
  (`chunk_received`, `finished`, `error`, `usage_received`, `context_built`).
  Qt delivers these as queued connections across the thread boundary, so the UI
  thread is never touched from the worker.
- **Cancellation** is cooperative: `cancel()` sets a flag the read loop checks each
  iteration. When the user switches chats mid-stream, `ChatView` *blocks the worker's
  signals before cancelling* — otherwise the worker's final `finished` emission would
  race in after `current_chat_id` has changed and save a partial reply into the wrong
  chat.
- On window close, `MainWindow.closeEvent` cancels and `wait()`s (bounded) so no
  thread outlives the app.

Why `QThread` and not `asyncio`? The OpenAI SDK's streaming iterator is synchronous
and blocking, and Qt's event loop is the natural scheduler here. A `QThread` keeps the
code simple and avoids bridging two event loops.

---

## 5. Persistence

SQLite via the stdlib `sqlite3`, opened with `check_same_thread=False` because the
worker thread reads messages while building context.

**Schema**

```sql
chats(id, title, created_at, updated_at)
messages(id, chat_id→chats.id ON DELETE CASCADE,
         role, content, prompt_tokens, completion_tokens, created_at)
```

Design notes:

- **`id` is the source of truth for ordering**, not `created_at`. SQLite's
  `datetime('now')` has only one-second resolution, so two messages saved in the same
  second are not ordered reliably by timestamp. Messages are therefore read with
  `ORDER BY id ASC` (autoincrement = insertion order); chats use
  `ORDER BY updated_at DESC, id DESC` for a stable tiebreak.
- **Foreign keys are enabled** (`PRAGMA foreign_keys = ON`) so deleting a chat cascades
  to its messages.
- **Forward-compatible migration**: the token columns are added with idempotent
  `ALTER TABLE … ADD COLUMN` guarded by a caught `OperationalError`, so databases
  created by older versions upgrade in place.
- The system prompt is **not** stored per message — it is prepended at request time
  from the current settings, so changing it applies to existing chats.

---

## 6. Chat memory (context construction)

Implemented in `memory.py` as two layers, both off the UI thread.

**Layer 1 — sliding window (always on).**
The last `memory_window_size` messages are sent verbatim. If the whole chat fits in the
window, that is the entire context and no vector work happens.

**Layer 2 — vector retrieval (optional, needs `chromadb`).**
When a chat grows beyond the window, the *older* messages are embedded (lazily, only
the ones not yet indexed) into a per-chat Chroma collection. Before each request the
new user query is embedded and the top-K most similar older messages are retrieved and
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
  plain sliding window. Vector memory is an enhancement, never a hard dependency.
- **Indexing is incremental** — already-embedded message ids are skipped, so cost grows
  with new messages, not total history.
- Deleting a chat calls `reset_chat`, which drops its Chroma collection.

---

## 7. Rendering pipeline

`utils.text_to_html` turns assistant Markdown into inline-styled HTML for `QTextEdit`
(`QTextEdit` supports only a CSS subset, so styles are inlined per element):

1. `markdown` renders Markdown → HTML (`fenced_code`, `tables`, `nl2br`, `sane_lists`).
2. `_post_process_code_blocks` rewrites `<pre><code class="language-X">` blocks into
   styled containers, with `Pygments` (Monokai, `noclasses=True`) for highlighting.
3. If `markdown` is unavailable, `_simple_md` provides a regex-based fallback covering
   code fences, inline code, bold/italic, and headers.

During streaming, `MessageWidget.append_chunk` inserts **plain text** for speed; only on
`finalize()` is the full Markdown re-rendered. `_AutoTextEdit` resizes each bubble to its
content height so bubbles grow naturally inside the scroll area instead of showing inner
scrollbars.

**Visual design.** All colours, radii and fonts live as tokens in `theme.py`; widgets
reference them instead of hard-coding hex values, which is what keeps the UI cohesive.
`theme.global_qss()` is applied once on the `QApplication` to style scrollbars, menus and
tooltips app-wide. The message list is a single column centred to `CONTENT_MAX_WIDTH` for
readability on wide windows, each message is a content-hugging bubble with a circular role
avatar, and a trailing layout stretch keeps bubbles top-aligned (without it, the
`Minimum`-policy bubbles would stretch to fill the viewport). The composer (`_InputEdit`)
auto-grows from one line up to a cap as the user types.

**Theming (light/dark).** `theme.py` holds two palettes (`_DARK`, `_LIGHT`) with identical
token names; `theme.apply(name)` rebinds the module-level token attributes to the chosen
palette. Because most widget stylesheets are baked into f-strings at construction time, a
switch (`MainWindow.apply_theme`) re-applies the app palette + global QSS and then *rebuilds
the central widget* so every widget restyles with the new tokens (the current chat is
reloaded afterwards). Two details make this robust: stylesheet strings that must follow the
theme are produced by *functions* called per use (`_content_css()`, dialog `_style()`) rather
than import-time constants; and message-bubble colours are resolved per instance in
`_setup_ui` (a class-level colour dict would freeze whatever theme was active at import).
Code blocks adapt too — a dark surface + Pygments `monokai` in dark mode, a light surface +
the light `default` style in light mode — so un-tokenised code text inherits the matching
body colour and stays legible.

**System theme.** The persisted `theme` value is a *preference* — `system`, `light`, or
`dark` — kept distinct from the *resolved* palette (`dark`/`light`). `theme.resolve(pref)`
maps a preference to a palette, querying the OS via `QStyleHints.colorScheme()` (Qt 6.5+,
guarded for older versions) when the preference is `system`. The View ▸ Theme submenu is an
exclusive radio group; `Ctrl+Shift+L` quick-flips light/dark (which sets an explicit, non-
system preference). While `system` is active, `MainWindow` listens for
`QStyleHints.colorSchemeChanged` and re-resolves + rebuilds only when the effective palette
actually changes, so the app tracks the OS light/dark setting live.

---

## 8. Cost estimation

`message_widget._estimate_cost` maps a model name to an entry in a small static price
table. Because model names are matched by substring, the **longest** matching key wins —
otherwise `gpt-4o-mini` would be mispriced as `gpt-4o`, and `o1-mini` as `o1`. Prices are
approximate and may drift; an unknown model simply shows no cost.

---

## 9. Per-chat overrides, edit/regenerate, and export

**Per-chat overrides.** The `chats` table carries nullable `model`, `system_prompt`,
and `temperature` columns; `NULL` means "inherit the global setting". `ChatView` loads
them on `load_chat` and resolves an effective value via `_effective(key)`
(override → global). The model/temperature overrides are forwarded to `StreamWorker`
as an `overrides` dict (resolved there by `_cfg`), while the system prompt is resolved
in `ChatView` before the prompt is assembled. This keeps a single source of truth — the
worker never second-guesses what the view decided.

**Edit & regenerate.** Both are expressed as a single primitive:
`Database.delete_messages_from(chat_id, message_id)` removes a message and everything
after it (`id >= message_id`, relying on the id-as-order invariant from §5).
*Regenerate* truncates from the assistant reply and re-streams using the now-last user
message as the query; *edit* truncates from a user message and drops its text back into
the input box for the user to revise and resend. After truncation the view is force-
reloaded (`load_chat(..., force=True)`) so the widgets exactly match the database.

**Export / import.** `chat_io` serialises a chat to Markdown (human-readable, one-way)
or JSON (round-trippable: roles, token counts, timestamps, and per-chat overrides).
Import validates the JSON shape, creates a new chat, restores overrides, and re-inserts
messages in order. The UI lives in the chat-list context menu (export) and the File menu
(import).

## 10. Settings & providers

`settings_dialog.py` provides three provider presets (OpenAI, Ollama, Custom). The model
fetcher (`_ModelFetcher`, also a `QThread`) tries Ollama's `/api/tags` for local URLs and
falls back to the OpenAI-style `/v1/models`. Save-time validation enforces a well-formed
URL, a model name, and an API key for non-local endpoints; local endpoints
(`localhost`/`127.0.0.1`/`::1`/`:11434`) are exempt from the key requirement.

**Window state.** The main window persists its geometry and the sidebar/chat splitter
position into `settings.json` (`window_geometry`, `splitter_state`). These are Qt
`saveGeometry()`/`QSplitter.saveState()` blobs, base64-encoded for JSON; `MainWindow`
writes them in `closeEvent` and restores them on startup (and carries the splitter
position across a theme rebuild). Decoding tolerates missing/corrupt values and falls
back to defaults.

---

## 11. Known limitations / future work

- The `stream_options.include_usage` probe falls back to a plain stream if the endpoint
  rejects it, but only when no content has streamed yet (to avoid duplicate output);
  some non-OpenAI endpoints therefore won't report token usage.
- Reasoning-model detection is name-based (`o<digit>` after any provider prefix); an
  endpoint that exposes o-series models under unusual names won't be auto-detected.
- A per-chat **empty** system prompt is treated as "inherit", so a chat can't force the
  model to run with *no* system prompt while a global one is set.
- API key is stored in plaintext (see README security note).
- No attachments/images, no tool-calling, no message search within a conversation.

See the README's feature list for the full set of implemented capabilities.
