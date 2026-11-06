# foreman web API (v1 contract)

Served by `foreman web` / `foreman serve`. All `/api/*` requests need the device token: cookie `fm_token`
(set by `GET /pair?code=<code>`) or `Authorization: Bearer <token>`. 401 → the UI shows a "pair this device"
screen (`foreman web pair` on the machine prints a code; the user types it into the screen, which calls
`GET /pair?code=...&json=1` → `{"ok":true}` and sets the cookie).

Times are UTC ISO-8601 strings. `null` context means **unknown** (render "unknown" / "?", never 0).

## GET /api/state
```json
{
  "version": 42,
  "machine": {"name": "ghome", "kind": "home"},
  "limits": {
    "claude": {"label": "Claude", "level": "ok|warn|stop|unknown",
      "windows": [
        {"key": "weekly", "label": "неделя", "used_pct": 61, "resets_at": "...", "observed_at": "...", "age": "4m", "level": "ok"},
        {"key": "five_hour", "label": "5 ч", "used_pct": 42, "resets_at": "...", "observed_at": "...", "age": "4m", "level": "ok"}]},
    "antigravity": {"label": "Antigravity", "level": "warn", "windows": [
        {"key": "weekly", ...}, {"key": "five_hour", ...}, {"key": "3p_five_hour", "label": "3p 5 ч", ...}]}
  },
  "thresholds": {"limit_warn": 70, "limit_stop": 90, "context_soft": 150000},
  "projects": [
    {"slug": "shopping-list", "name": "shopping-list", "group": "needs_you|stuck|working|quiet",
     "line": "Нужно решение: мерджить PR #12?", "dot": "accent|red|green|grey",
     "updated_at": "...", "unanswered": 1}
  ],
  "findings_count": 2
}
```
The weekly window is always first in `windows`. A window with `used_pct: null` is unknown.

## GET /api/projects/<slug>
```json
{
  "slug": "...", "name": "...", "path": "~/projects/x",
  "strip": {"stage": "Ревью PR #12", "sessions": 4, "heaviest": {"title": "fm-api", "context_tokens": 220000, "level": "warn"}},
  "chat": [
    {"id": "m-1a2b3c", "ts": "...", "from": "orchestrator|user|foreman", "channel": "web|telegram|cli|system",
     "text": "...", "question": true, "options": ["Да", "Нет", "Ответить…"], "answered_by": null}
  ],
  "board": {"tasks": [{"id": "t1", "title": "...", "stage": "...", "owner": "fm-api (s-1a2b3c)", "next": "...",
            "links": [{"label": "PR #12", "url": "https://..."}], "status": "open|blocked|waiting|done"}]},
  "sessions": [{"sid": "s-1a2b3c", "title": "fm-api", "agent": "claude", "model": "sonnet-5-5", "role": "worker",
                "owner": "orchestrator|user", "state": "busy", "lifecycle": "active|rotated", "live": true, "state_age": "3m", "context_tokens": 152000,
                "context_level": "ok|warn|unknown", "replaces": null, "replaced_by": null}],
  "watches": [{"what": "watch loop", "status": "running, last pass 23s ago"}]
}
```
Chat is the last 200 messages, oldest first. Only `question: true` messages carry `options`; the option
"Ответить…" means "focus the reply box" (in Telegram its button only answers the callback "reply in the topic").
Triage (`group`), `strip.sessions` and `strip.heaviest` consider only current sessions: closed and rotated-out ones
are history. The `sessions` list holds the open sessions and the rotated ones (the old → new chain needs both
ends); records with `lifecycle: closed` are left out. `live` marks the current ones; the UI counts those, so the
drawer count equals `strip.sessions`, and shows the rest as history.

Board tasks are exactly the records of `board.json`: ids are `t1`, `t2`, … (never reused). `status` is one of
`open` (default; the UI treats it as active), `blocked`, `waiting`, `done`. `done` is only reachable through
`foreman board done`, which runs the task's stored objective check first (the check can be set once and not replaced).
Each task may also carry `check` (`{cmd, exit_code, tail, ran_at}`) and `updated_at`.

## POST /api/projects/<slug>/messages
Body `{"text": "...", "reply_to": "m-1a2b3c"?, "option": "Да"?}` → `{"ok": true, "id": "m-...", "delivery": "delivered|queued|terminal_gone"}`.
With `option` and no `text`, the option is the text. UI actions ("open a session", "close", "rotate") are sent
as ordinary messages to the orchestrator, e.g. `{"text": "Закрой сессию fm-api"}`.
The body must be a JSON object of at most 64 KiB (`Content-Length` 0..65536; otherwise 400 / 413). `reply_to`, when
given, must be a string id of a message in this project's chat (else 400). Cookie-authenticated requests need an
`Origin` equal to `scheme://host:port` of this server (the request's `Host`, plain http) or of `web.base_url` —
the port counts; a bearer-token request without `Origin` is allowed. The `Host` allowlist does not include the
wildcard addresses `0.0.0.0` / `::` (unless configured as a bind address). Handler sockets time out after 30 s.
The posted message is also mirrored to the project's Telegram topic as `user (web): <text>` (architecture §10).

## GET /api/findings
`{"findings": [{"id": "f-...", "ts": "...", "title": "...", "status": "draft|sent|failed", "url": null}]}`

## GET /api/events (Server-Sent Events)
`data: {"type": "changed", "version": 43}` whenever any state changes; `: ping` every 15 s. The UI refetches
`/api/state` and the open project on `changed`, and reconnects with backoff when the stream drops.

## Deep link
`/#/p/<slug>` opens the project chat; `/#/p/<slug>/m/<msgid>` also scrolls to and highlights that message.
On a phone the route opens the chat screen directly (not the triage list).
