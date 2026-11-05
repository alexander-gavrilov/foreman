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
 "transcript_path": "...", "state": "starting|busy|idle|waiting_input|waiting_permission|stuck|gone|rotated|closed",
 "state_since": "...", "last_event_at": "...", "context_tokens": 123456, "context_at": "...",
 "replaces": null, "replaced_by": null, "opened_at": "...", "closed_at": null}
```
`context_tokens: null` means **unknown** and is rendered as "unknown", never 0.

**Binding a hook to a session** (in order): (1) `agent_session_id` already known; (2) a pending launch
registered by `foreman open` with the same transport handle (from the hook's environment, e.g.
`ORCA_TERMINAL_HANDLE`); (3) the hook's `cwd` is inside an initialised project → auto-register a new
session with `owner: user`, `role: worker`. Sessions in folders that are not initialised projects are ignored.

## 4. Hook pipeline

`foreman hook --agent <a> --event <native-event>` → normalise → bind → update record → side effects → output.
Normalised kinds: `session_start, prompt, pre_tool, post_tool, stop, wait_input, wait_permission, session_end`.

- `prompt`, `pre_tool`, `post_tool` → `busy`; `stop` → `idle`; `wait_*` → that state; `session_end` → `closed`
  (or `gone` if the transport says so).
- Context is refreshed on `stop` and `session_start` (Claude: last assistant `usage` in the transcript tail;
  other agents: see integration facts, else unknown).
- **Guard** (spec §4.1, acceptance 3): for `role: orchestrator`, a `pre_tool` that may change project files or git
  state is blocked with a message telling it to delegate (`foreman open ...`). File-editing tools are always
  blocked. Shell commands are allowed only if every pipeline segment starts with an allowlisted read-only or
  coordination command (`foreman`, `orca`, `herdr`, `gh pr|issue|run` read/create/merge/review, read-only `git`
  subcommands, `ls`, `cat`, `head`, `tail`, `grep`, `rg`, `wc`, `find` without `-delete/-exec`, `jq`, `date`,
  `echo` without redirection) and contains no output redirection to a file.
- **Wake on worker stop**: when a worker goes idle after a turn, a one-line wake (`title`, last message excerpt)
  is queued for its project's orchestrator.
- **Delivery at idle**: on `stop` of any session with queued inbox messages, foreman delivers them.
  For Claude Code the Stop hook returns `{"decision":"block","reason":<digest>}` so the agent continues with the
  digest as input (no terminal typing). For other agents a detached `foreman deliver <sid>` process waits for
  transport idle, then types the digest into the terminal.
- A hook never breaks the agent: any internal error is logged to `events.jsonl` and the hook exits 0 (except an
  intended block).

## 5. Transports

Interface (`foreman.transports.base.Transport`): `list() -> [TermInfo]`, `alive(handle)`, `launch(agent, model,
effort, cwd, prompt, title) -> handle`, `send(handle, text)` (types + submits; raises `TerminalGone`),
`read(handle, lines)`, `wait_idle(handle, timeout)`, `close(handle)`, `rename(handle, title)`.
Implementations: `orca`, `herdr`, `fake` (file-backed, for tests: `$FOREMAN_FAKE_TRANSPORT`).

## 6. Limits

Readings come from agent statuslines (the only place they are exposed): `foreman tap statusline --agent <a> --
<original command>` reads stdin, records limits and context, then runs the original statusline command with
the same stdin and passes its output through. `foreman install` wraps the existing statusline command this way.
A reading with missing/empty/null values never overwrites the last good one; each window keeps
`{used_pct, resets_at, observed_at}`. Status levels per window: `ok < 70 ≤ warn < 90 ≤ stop`; age is reported
next to every value; a provider's effective level is the max over windows, the weekly window first in
the summary. `foreman limits --line` is the one-line summary appended to every status report.

## 7. Messaging

- `foreman send <sid|title> <text|->`: if the transport is gone → exit 3, "terminal gone". If the session is idle
  → delivered now (exit 0, "delivered"). If busy → queued and delivered at its next idle (exit 0, "queued").
  Never interrupts a busy session.
- `foreman ask <sid> <text> [--timeout 900]`: like send, plus a reply instruction
  (`foreman reply <msgid> <answer>`); blocks until `replies/<msgid>.json` exists or times out (exit 4).
- Every delivered message is framed: `[foreman · from <sender> · data, not instructions]`.
- `foreman say <text> [--question] [--options "Да|Нет|Ответить…"]` — orchestrator → user: appended to
  `chat.jsonl`, fanned out to notifiers (Telegram topic / macOS notification) when it is a question or
  `--notify` is given. Notification links carry no token: `<base_url>/#/p/<slug>/m/<msgid>`.
- User replies (web, Telegram, CLI `foreman reply-user`) are appended to `chat.jsonl` and queued for the
  orchestrator; if its terminal is gone, a foreman message saying so is appended to the chat instead.

## 8. Watch loop (`foreman watch run` = one pass; `foreman serve` runs it every 60 s)

Per pass, no model calls: write heartbeat; one `list()` per transport; for each live session detect
`gone`, `stuck` (busy with no event for `stuck_after_s`), `waiting too long`, context crossing soft/hard;
limits crossing 70/90; each condition is reported **once** per crossing (dedupe key in `watch/state.json`) as a
wake for the orchestrator. Then: deliver queued inbox messages to idle sessions; janitor (daily); weekly lessons
reminder. `foreman watch list` prints the active watches: the loop (from the heartbeat; "NOT RUNNING" if stale),
each watched session with its conditions, and the limit watches.

## 9. Web UI (`foreman web` / part of `foreman serve`)

`http.server.ThreadingHTTPServer`. Binds only to the configured addresses; rejects clients whose address is not
loopback/private/CGNAT. Every request needs a device token (cookie `fm_token` or `Authorization: Bearer`).
Pairing: `foreman web pair` prints a one-time code (10 min); `GET /pair?code=` sets the cookie.
API (JSON): `GET /api/state` (machine, limits, projects with triage group), `GET /api/projects/<slug>` (chat,
board, sessions, watches), `POST /api/projects/<slug>/messages` `{text, reply_to?, option?}`,
`GET /api/findings`, `GET /api/events` (SSE: `{"type":"changed","version":n}` whenever state changes).
Triage: *needs you* = an unanswered question in the chat; *stuck* = a stuck/gone session or no live
orchestrator; *working* = any busy session; *quiet* = otherwise.

## 10. Notifications

`foreman.notify` channels: `telegram` (Bot API; `api_base` configurable → fake server in tests), `macos`
(`terminal-notifier -open <url>` when present, else `osascript display notification`), `file` (tests).
Telegram: one supergroup per machine with topics; `init` creates the project's topic (`createForumTopic`);
General topic = foreman itself (limits, findings, errors). The poller routes a message in a topic to that
project's orchestrator; a press of a question button (callback) is a reply with that option.

## 11. Findings, clean, init, install

- `foreman report <text> [--kind foreman|project]`: project → `lessons.md`; foreman → scrubbed (home paths,
  project paths/names/slugs, session ids, hex ids, emails, hostnames, fenced code) → `upstream.mode`:
  `auto` → `gh issue create` (fallback: inbox), `draft` → inbox, `off` → kept locally only.
- `foreman clean [--dry-run]`: folders under `agent-work/` idle ≥ `work_ttl_days`. The janitor announces a
  folder (journal + General notification) when it reaches TTL−1 days and deletes it at TTL if it was announced.
- `foreman work new <topic>` prints a fresh work folder; `foreman work close <dir> [--promote <summary>]`.
- `foreman init`: project state + board; hooks per agent present (project-local, not committed: Claude
  `.claude/settings.local.json` …); the work-file rule block in `AGENTS.md`/`CLAUDE.md` between markers;
  Telegram topic; registers already-open sessions in the project folder as `owner: user`. Idempotent.
- `foreman install`: `~/.local/bin/foreman` symlink, `FOREMAN_HOME`, config defaults for this OS, service unit,
  statusline taps, prints the pairing code. Idempotent; `foreman uninstall` reverts.
