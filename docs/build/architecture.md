# foreman — architecture (build contract)

Status: v1 build contract, written by the build lead on 2026-11-05.
Source of truth for behaviour: `docs/superpowers/specs/2026-11-05-foreman-design.md` (the spec).
This document fixes *how*; every significant choice is also recorded in `docs/build/decision-log.md`.
Agent-specific facts (hook payloads, statusline JSON, transport CLIs) are in `docs/build/integration-facts.md`.

## 1. Shape

- **One stdlib-only Python 3 package** (`python3 >= 3.9`, no third-party dependencies), shipped inside the
  skill folder so that `npx skills add alexander-gavrilov/foreman` installs it for every agent at once.
- **One CLI**, `foreman`, deterministic, short output, `--json` everywhere it makes sense. It never calls a model.
- **One long-running daemon per machine**, `foreman serve`: web UI + SSE, Telegram long-poll, and the
  watch loop (every 60 s). It runs under a systemd user unit (Linux) or a launchd agent (macOS) with restart.
  The watch loop is pure Python: it costs zero model tokens.
- **Hooks**: each agent calls `foreman hook --agent <a> --event <e>` with its hook JSON on stdin. Hooks are the
  only way sessions report; there is no polling of models.
- **Skills** tell agents *when and why*; the CLI does the *how*.

```
repo/
  skills/foreman/                 # installed for all agents (npx skills)
    SKILL.md                      # every agent: work-file rule, `foreman report`, `foreman stage`, install steps
    bin/foreman                   # launcher: adds ../lib to sys.path, runs foreman.cli:main
    lib/foreman/                  # the package
    web/index.html                # the web UI, single file, vanilla JS, no build step
    templates/                    # systemd/launchd units, rule block, orchestrator briefing
  skills/foreman-orchestrator/SKILL.md   # the orchestrator role
  .claude-plugin/{plugin.json,marketplace.json}   # bonus Claude Code plugin path
  tests/                          # python -m unittest discover -s tests (stdlib only)
  tests/fakes/                    # fake transport, fake Telegram Bot API server, fake agents
```

## 2. State layout

Root: `$FOREMAN_HOME` (default `${XDG_STATE_HOME:-~/.local/state}/foreman`). Every path below is relative to it.
Tests and development always set `FOREMAN_HOME` (and `HOME`) to temporary directories.

| Path | Content |
|---|---|
| `config.json` | machine config (§2.1) |
| `secrets.json` (0600) | Telegram bot token, hashed web device tokens, pending pairing codes |
| `projects/<slug>/project.json` | `{slug, name, path, created_at, orchestrator, telegram_topic_id, gates[], language}` |
| `projects/<slug>/board.json` | `{tasks:[{id, title, stage, owner, next, links:[{label,url}], status, check, updated_at}], backlog:[...]}` |
| `projects/<slug>/journal.jsonl` | `{ts, actor, kind, text, data}` one line per event, append-only |
| `projects/<slug>/chat.jsonl` | `{id, ts, from: orchestrator|user|foreman, channel, text, question, options[], reply_to, answered_by}` |
| `projects/<slug>/lessons.md` | project-rule findings (stay with the project, outside its repo) |
| `sessions/<sid>.json` | session record (§3) |
| `inbox/<sid>.jsonl` | messages queued for a session, `{id, ts, from, text, kind, delivered_at}` |
| `limits/<provider>.json` | per-window last good readings (§6) |
| `events.jsonl` | normalised hook events, size-rotated (debug / evidence) |
| `watch/state.json` | dedupe keys of conditions already reported, janitor state, weekly-lessons state |
| `watch/heartbeat.json` | `{pid, last_run_at, interval_s}` — the proof that the watch loop really runs |
| `findings/<id>.json` | foreman findings inbox: drafts and failed sends |
| `agent-work/<slug>/<YYYY-MM-DD>-<topic>/` | work artifacts (§4.9 of the spec) |
| `replies/<msgid>.json` | answers to `foreman ask` |

All JSON writes are atomic (`tmp` + `os.replace`) under an `fcntl.flock` on `<file>.lock`. JSONL appends take the
same lock. Timestamps are UTC ISO-8601 with `Z`.

### 2.1 config.json
```json
{
  "machine": {"name": "ghome", "kind": "home"},
  "web": {"bind": ["127.0.0.1"], "port": 8765, "base_url": "http://ghome.lan:8765"},
  "notify": {"channels": ["telegram"]},
  "telegram": {"chat_id": null, "api_base": "https://api.telegram.org"},
  "upstream": {"mode": "auto", "repo": "alexander-gavrilov/foreman"},
  "thresholds": {"context_soft": 150000, "context_hard": 250000, "stuck_after_s": 1200,
                  "waiting_after_s": 900, "limit_warn": 70, "limit_stop": 90, "work_ttl_days": 14},
  "routing": {"routine": [...], "design": [...], "review": [...], "research": [...], "orchestrator": [...]}
}
```
Defaults live in code; the file stores only overrides. `machine.kind` defaults to `home` on Linux and `work` on
macOS; `upstream.mode` defaults to `auto` for home and `draft` for work; `notify.channels` defaults to
`telegram` for home and `macos` for work.

## 3. Sessions

Session id (`sid`): `s-` + 6 hex chars, assigned by foreman. Record:
```json
{"sid": "s-1a2b3c", "project": "foreman", "agent": "claude|antigravity|codex", "model": "...", "effort": null,
 "role": "orchestrator|worker", "owner": "orchestrator|user", "purpose": "one line", "title": "fm-api",
 "transport": {"kind": "orca|herdr|fake", "handle": "term_..."}, "agent_session_id": "...", "cwd": "...",
 "transcript_path": "...", "state": "starting|busy|idle|waiting_input|waiting_permission|stuck|gone",
 "lifecycle": "active|rotated|closed",
 "state_since": "...", "last_event_at": "...", "context_tokens": 123456, "context_at": "...",
 "replaces": null, "replaced_by": null, "opened_at": "...", "closed_at": null}
```
`state` is activity (written by hooks and the watch loop); `lifecycle` is set only by `open`/`rotate`/`close`, so a
rotated session keeps showing "old → new" while its activity is still tracked (it must not be interrupted).
`context_tokens: null` means **unknown** and is rendered as "unknown", never 0.

**Binding a hook to a session** (in order): (1) `agent_session_id` already known; (1b) a live record with the same
transport handle (e.g. after `/clear` a Claude session gets a new `session_id`): re-bound, role and owner kept,
**but only for a genuine successor** (D59): a SessionStart with source `clear`/`resume`/`compact`, or a record that has
no agent session id yet. Any other event with a different session id is a **child session** of that terminal (a
`claude -p` started by an unrelated hook inherits the terminal handle): it changes nothing in the record (id, state,
transcript, context, lifecycle), claims no inbox, gets no digest or briefing, its SessionEnd is ignored; only its
PreToolUse is answered, guarded with the record's role (an orchestrator's child is denied what the orchestrator would
be). One `child_session` event is logged per child session id. Agents whose hooks carry no `source` (agy; Codex is
unverified) keep the plain handle rebind;
(2) a pending launch
registered by `foreman open` with the same transport handle (from the hook's environment, e.g.
`ORCA_TERMINAL_HANDLE`); (3) the hook's `cwd` is inside an initialised project → auto-register a new
session with `owner: user`, `role: worker`. Sessions in folders that are not initialised projects are ignored.
The orchestrator is registered explicitly: `foreman orchestrator start` (opens it) or `foreman orchestrator adopt
<handle|sid>` (an existing session becomes the orchestrator). A project has at most one live orchestrator.
Hook commands written by `init` use the **absolute** launcher path (agy runs hooks via `sh -c`; launchd PATH lacks
`~/.local/bin`); `watch list` shows the age of the last hook per agent so a silent hook is visible.

## 4. Hook pipeline

`foreman hook --agent <a> --event <native-event>` → normalise → bind → update record → side effects → output.
Normalised kinds: `session_start, prompt, pre_tool, post_tool, stop, wait_input, wait_permission, session_end`.

- `prompt`, `pre_tool`, `post_tool` → `busy`; `stop` → `idle`; `wait_*` → that state; `session_end` → `closed`
  (or `gone` if the transport says so).
- Context is refreshed on `stop` and `session_start` (Claude: last assistant `usage` in the transcript tail;
  other agents: see integration facts, else unknown).
- **Guard** (spec §4.1, acceptance 3): for `role: orchestrator`, every `pre_tool` is checked against **allowlists**
  (deny by default) and blocked with a message telling it to delegate (`foreman open ...`):
  - *tools*: only read-only tools (Claude `Read`, `Grep`, `Glob`, `LS`, `TodoWrite`, `WebFetch`, `WebSearch`,
    `SendMessage`, `Agent`/`Task` (without `isolation`), `ToolSearch`, `Skill`; agy `view_file`, `list_dir`, `grep_search`, `find_by_name`,
    `read_url_content`, `search_web`; Codex read tools) and the shell tool; every other tool, including any `mcp__*`
    tool, is denied;
  - *shell*: parsed with `shlex`; any command substitution (`$(`, backticks), process substitution, heredoc, or
    output redirection other than to `/dev/null` is denied; each segment of `|`, `;`, `&&`, `||` must start with an
    allowlisted command: `foreman` with an orchestrator-safe subcommand (`status limits send ask reply say open close
    rotate compact board(show/add/set without `check`) log stage watch(list) clean(--dry-run) work report gate orchestrator`; `board done`
    only without `--check`; `open` without `--role`/`--owner`; `close` without `--user-consent`; not `orchestrator adopt`, which is user-only), `orca`/`herdr` read-only subcommands (`terminal list|show|read|wait`, `worktree ps|list`,
    `pane list|read`, `agent list|read|wait`), `gh pr|issue|run|repo` (view, list, create, merge, review, checks,
    comment, diff), `git` read-only subcommands (`status log diff show branch(no args)/--list remote(-v) fetch
    rev-parse ls-files blame`) with `--output`, global `-c`/`--config-env`/`--exec-path` and `--upload-pack` denied,
    and `ls cat head tail grep rg wc jq date echo pwd which tree stat file` (`find` only without `-delete -exec
    -execdir -ok -fprint* -fls`; `rg` without `--pre`/`--pre-glob`; `tree` without `-o`/`-R`/`-H`; `file` without `-C`).
  - The guard applies to every tool call in the orchestrator's session, including calls made by its subagents
    (same `session_id`, with `agent_id` in the payload).
  - Sending text to terminals is possible only through `foreman send/ask/compact`, which accept only registered
    sessions whose transport reports an agent (Orca `agentIdentity`, Herdr `agent`), never a plain shell.
  - **Fail closed**: if classification raises for an orchestrator `pre_tool`, or the role cannot be determined for a
    hook whose handle or session id matches a recorded orchestrator, the tool is denied. Every other hook path
    fails open (exit 0, logged).
- Objective checks (`board done`) run only the task's stored check. A check is set by a worker or the user
  (`board set check=` when none exists); the orchestrator can neither set nor replace one, so it cannot use a check
  to run commands.
- **Wake on worker stop**: when a worker goes idle after a turn, a one-line wake (`title`, last message excerpt)
  is queued for its project's orchestrator and delivered at once if the orchestrator is idle (no wait for the watch
  loop); if the watch heartbeat is stale, a "watch loop not running" event is logged and shown in `watch list`.
- **The project rule** (spec §4.9) reaches every agent through the SessionStart hook (Claude/Codex
  `additionalContext`, agy `injectSteps`), so `init` does not edit any committed file.
- **Delivery at idle**: on `stop` of any session with queued inbox messages, foreman delivers them.
  For Claude Code the Stop hook returns `{"decision":"block","reason":<digest>}` so the agent continues with the
  digest as input (no terminal typing); Codex uses the same block, agy `{"decision":"continue"}`. agy ignores a
  continuation once its consecutive-continuation cap is hit, so its claim is *provisional*
  (`claimed_by: stop-hook:provisional`): the next agent activity (PreInvocation / tool event) confirms it; if none
  arrives within 45 s the watch loop gives the messages back and types the digest at idle. `foreman deliver <sid>
  [--wait S]` (wait = Orca `worktree ps` not `working`, `tui-idle` only as the secondary signal) is the manual
  form; hooks and wakes spawn it without `--wait` because they only deliver to a session whose record says idle.
- A hook never breaks the agent: any internal error is logged to `events.jsonl` and the hook exits 0 (except an
  intended block).

## 4a. Agent adapters (facts: `integration-facts.md`)

| | Claude Code | Antigravity (agy) | Codex |
|---|---|---|---|
| project hook file written by `init` | `.claude/settings.local.json` (`hooks` key, Claude layout) | `.agents/hooks.json` (top key = hook name `foreman`, then events; flat lists except Pre/PostToolUse) + workspace must be trusted | `.codex/hooks.json` (Claude layout); the user approves the hook once in Codex `/hooks` (foreman never trusts itself) |
| events used | SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop, Notification (permission_prompt / idle_prompt), PermissionRequest, SessionEnd | SessionStart, PreInvocation, PreToolUse, PostToolUse, Stop (`fullyIdle`) — event name passed as argument | SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, PermissionRequest, Stop, SessionEnd |
| session id | `session_id` | `conversationId` | `session_id` |
| block an edit | stdout `hookSpecificOutput.permissionDecision: deny` | stdout `{"decision":"deny","reason":…}` | `hookSpecificOutput.permissionDecision: deny` (or exit 2) |
| continue with a digest at Stop | `{"decision":"block","reason":digest}` (skip if `stop_hook_active`) | `{"decision":"continue","reason":digest}` | `{"decision":"block","reason":digest}` |
| waiting for permission | Notification `permission_prompt`, PermissionRequest | not visible to hooks (Herdr `blocked` status when on Herdr) | PermissionRequest |
| context | transcript tail `usage` (sum of input + cache_creation + cache_read + output) or statusline `context_window` | statusline `context_window` only | unknown in v1 (no statusline command) |
| limits | statusline `rate_limits.five_hour/seven_day.{used_percentage, resets_at epoch s}` | statusline `quota.<key>.{remaining_fraction, reset_time}`; keys `gemini-5h`, `gemini-weekly`, `3p-5h`, `3p-weekly` | not read in v1 (spec §10.1: shown only if exposed) |
| orchestrator-capable | yes (verified) | yes (verified live E4, interactive TUI on Orca: `deny` works next to the user's Orca hook, a worker Stop is typed at idle, an event during a busy turn is delivered by Stop `continue`, user replies arrive); when the continuation cap is hit, delivery falls back to typing at idle. A hook that blocks nothing must answer a PreToolUse with `{"decision":"ask"}`: `{}` makes the interactive TUI deny the call with an empty reason | worker only in v1: hooks could not be run here (not logged in) |

Hook outputs always print valid JSON (`{}` when nothing) because agy requires it. Legacy snapshots in
`~/.cache/agent-limits/` (written by the user's existing statusline scripts) are also imported as a limits
source, with `quota: null` / missing values ignored.

## 5. Transports

Interface (`foreman.transports.base.Transport`): `list() -> [TermInfo]`, `alive(handle)`, `launch(agent, model,
effort, cwd, prompt, title) -> handle`, `send(handle, text)` (types + submits; raises `TerminalGone`),
`read(handle, lines)`, `wait_idle(handle, timeout)`, `close(handle)`, `rename(handle, title)`.
Implementations: `orca`, `herdr`, `claude-msg`, `fake` (file-backed, for tests: `$FOREMAN_FAKE_TRANSPORT`).

| | Orca | Herdr | claude-msg |
|---|---|---|---|
| list | `orca terminal list --json` | `herdr pane list` | `~/.claude/sessions/<pid>.json` (+ pid alive) |
| launch | `orca agent launch --agent A --worktree path:P --model M [--effort E] --surface terminal --prompt T --json` | `herdr tab create --cwd P --label T --no-focus` → `herdr agent start N --kind A --pane ID -- --model M` → `herdr agent prompt` | — (not a launcher) |
| send+submit | `orca terminal send --terminal H --text T --enter --json` | `herdr agent prompt H T` (pane: `pane send-text` + `send-keys enter`) | unix socket `messagingSocketPath`: `{"type":"auth","token":peerToken}` then `{"type":"user","message":{"role":"user","content":T}}` |
| idle | `orca terminal wait --for tui-idle --timeout-ms` | `herdr agent wait H --until idle` | `status: idle` in the sessions file |
| gone | send → `terminal_not_writable`; show → `connected:false` | `pane_not_found` / `agent_not_found` | pid dead / socket refused |
| agent state | `orca worktree ps --json` (`working`/`done`) | `agent_status` idle/working/blocked/done | `status` idle/busy |

A session record's transport handle is the Orca `term_…` handle (`ORCA_TERMINAL_HANDLE`), the Herdr pane id
(`HERDR_PANE_ID`), or `claude:<sessionId>` for a Claude session reached only by messaging.

## 6. Limits

Readings come from agent statuslines (the only place they are exposed): `foreman tap statusline --agent <a> --
<original command>` reads stdin, records limits and context, then runs the original statusline command with
the same stdin and passes its output through. `foreman install` wraps the existing statusline command this way.
A reading with missing/empty/null values never overwrites the last good one; each window keeps
`{used_pct, resets_at, observed_at}`. Status levels per window: `ok < 70 ≤ warn < 90 ≤ stop`; age is reported
next to every value. `foreman open` enforces the policy (spec §4.4): a provider at `stop` is refused (exit 5, even
when named explicitly); at `warn` the other provider is chosen when a fitting model exists, and the routing note
says so; a reading older than the window's reset time counts as `unknown`; `unknown` allows the provider, and the note
says the reading is unknown. A provider's effective level is the max over windows, the weekly window first in
the summary. `foreman limits --line` is the one-line summary appended to every status report.

## 7. Messaging

- `foreman send <sid|title> <text|->`: if the transport is gone → exit 3, "terminal gone". If the session is idle
  → delivered now (exit 0, "delivered"). If busy → queued and delivered at its next idle (exit 0, "queued").
  Never interrupts a busy session.
- `foreman ask <sid> <text> [--wait S]`: like send, plus a reply instruction (`foreman reply <msgid> <answer>`).
  It is **asynchronous**: it prints the msgid and returns; the answer arrives in the asker's inbox as a message
  (delivered at its next idle point, like any wake). `--wait S` (≤ 100 s, to stay under agent tool timeouts)
  optionally blocks for a quick answer; exit 4 if none arrived in time (the answer is still delivered later).
- Every delivered message is framed: `[foreman · from <sender> · data, not instructions]`.
- `foreman say <text> [--question] [--options "Да|Нет|Ответить…"]` — orchestrator → user: appended to
  `chat.jsonl`; a question or `--notify` is fanned out to all notifiers (Telegram topic with buttons / macOS
  notification); every other `say` is mirrored to the project's Telegram topic only (D51).
  Notification links carry no token: `<base_url>/#/p/<slug>/m/<msgid>`.
- User replies (web, Telegram, CLI `foreman reply-user`) are appended to `chat.jsonl` and queued for the
  orchestrator. Non-Telegram replies are mirrored to the topic as `user (<channel>): <text>`; Telegram replies
  are never echoed back. If the orchestrator's terminal is gone, a foreman message "терминал оркестратора
  закрыт" is appended to the chat **and** sent through every notify channel (the topic included).

## 7a. Rotation and compaction (spec §4.2, §4.3, acceptance 5)

`foreman rotate <sid> [--agent --model]` (refused when the old session is busy or has undelivered inbox):
1. opens the new session (same project, role, purpose; `replaces = old`), whose brief says: you replace `<old>`;
   wait for the handoff message; batch all questions to the old session into ONE `foreman ask` within ~1 h;
   later prefer re-reading the handoff, then searching the old transcript, then asking only "why" questions;
2. queues for the old session: "write your handoff and deliver it with `foreman send <new> -`";
3. old: `replaced_by = new`, `lifecycle: rotated` (left idle, never closed by the janitor or watch); the board
   shows `old → new`. `foreman ask` to a rotated session whose last event is > 55 min old warns that its
   cache is cold. The orchestrator closes the old session later like any other (owner rules apply).
Compaction in place: `foreman compact <sid> [what to keep]` types the bare `/compact …` command at idle, without the
message framing (Claude Code and Codex support
`/compact`; agy has no compact command → rotate instead).

## 8. Watch loop (`foreman watch run` = one pass; `foreman serve` runs it every 60 s)

Per pass, no model calls: write heartbeat; one `list()` per transport; for each live session detect
`gone`, `stuck` (busy with no event for `stuck_after_s`), `waiting too long`, context crossing soft/hard;
limits crossing 70/90 (at ≥ 90 % every running worker session of that provider also gets a queued "wind down and
hand off at your next idle point" message; the orchestrator itself is not wound down — it is told, and keeps
running, because no orchestrator-capable alternative may exist); each condition is reported **once** per crossing (dedupe key in `watch/state.json`) as a
wake for the orchestrator. Then: deliver queued inbox messages to idle sessions; janitor (daily); weekly lessons
reminder. `foreman watch list` prints the active watches: the loop (from the heartbeat; "NOT RUNNING" if stale),
each watched session with its conditions, and the limit watches.

## 9. Web UI (`foreman web` / part of `foreman serve`)

`http.server.ThreadingHTTPServer`. Binds only to the configured addresses; rejects clients whose address is not
loopback/private/CGNAT; rejects requests whose `Host` is not a configured name/address of this machine (DNS
rebinding) and state-changing requests whose `Origin` is not the UI's own origin (CSRF); cookie is
`HttpOnly; SameSite=Strict`. Pairing codes: 8 characters from a 32-letter alphabet, single use, 10 min, at most
5 failed attempts per minute per client. Every request needs a device token (cookie `fm_token` or `Authorization: Bearer`).
Pairing: `foreman web pair` prints a one-time code (10 min); `GET /pair?code=` sets the cookie.
API (JSON): `GET /api/state` (machine, limits, projects with triage group), `GET /api/projects/<slug>` (chat,
board, sessions, watches), `POST /api/projects/<slug>/messages` `{text, reply_to?, option?}`,
`GET /api/findings`, `GET /api/events` (SSE: `{"type":"changed","version":n}` whenever state changes).
Triage: *needs you* = an unanswered question in the chat; *stuck* = a stuck/gone session or no live
orchestrator; *working* = any busy session; *quiet* = otherwise.

## 10. Notifications

`foreman.notify` channels: `telegram` (Bot API; `api_base` configurable → fake server in tests), `macos`
(`terminal-notifier -open <url>`; `foreman install` on macOS installs it with Homebrew when missing and reports
it as a user step when Homebrew is absent; the `osascript` fallback cannot open a link, so its text carries the URL), `file` (tests).
Telegram: one supergroup per machine with topics; `init` creates the project's topic (`createForumTopic`);
General topic = foreman itself (limits, findings, errors). The poller accepts updates only from the configured chat **and** from user ids in
`telegram.allowed_user_ids` (set by `foreman telegram set-chat`, which records the user who sent the setup message);
everything else is ignored and logged. It routes a message in a topic to that project's orchestrator (media
without a caption arrives as `[non-text message: <type>]`; service messages are ignored); a press of a question
button (callback) is a reply with that option, except «Ответить…» (answered with "reply in the topic") and an
unknown/expired callback (answered "expired", nothing delivered). `foreman serve` always runs the poller and
re-reads the Telegram configuration on every pass, so `telegram set-token` / `set-chat` need no restart.
**Mirror (D51):** the project's topic mirrors the project chat (see §7). Mirrored messages are cut at 4000
characters with a web-chat link, and limited to 10 per project per minute (the first one over the limit posts a
single "more messages in the web chat" pointer; the rest of the window is dropped). `telegram check` fails while
`allowed_user_ids` is empty.

## 11. Findings, clean, init, install

- `foreman report <text> [--kind foreman|project]`: project → `lessons.md`; foreman → scrubbed (home paths,
  project paths/names/slugs, session ids, hex ids, emails, hostnames, fenced code) → `upstream.mode`:
  `auto` → `gh issue create` (fallback: inbox), `draft` → inbox, `off` → kept locally only.
- `foreman clean [--dry-run]`: folders under `agent-work/` idle ≥ `work_ttl_days`. The janitor announces a
  folder (journal + General notification) when it reaches TTL−1 days and deletes it at TTL if it was announced.
- `foreman work new <topic>` prints a fresh work folder; `foreman work close <dir> [--promote <summary>]`.
- `foreman init`: project state + board; hooks per agent present (project-local files, each added to
  `.git/info/exclude`, so the working tree stays clean: Claude `.claude/settings.local.json`, agy `.agents/hooks.json`,
  Codex `.codex/hooks.json`); no committed file is edited (the rule arrives by the SessionStart hook); agy workspace
  trust and Codex hook trust are one-time user steps;
  Telegram topic; registers already-open sessions in the project folder as `owner: user`. Idempotent.
- `foreman install`: `~/.local/bin/foreman` symlink, `FOREMAN_HOME`, config defaults for this OS, service unit,
  statusline taps, prints the pairing code. Idempotent; `foreman uninstall` reverts.
