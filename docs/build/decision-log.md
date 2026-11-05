# foreman — build decision log

The autonomous build lead (Claude Opus 5.5) records here every significant decision taken while building v1,
with the reason. The user reviews these after delivery. Format: `D<n> · date · decision — reason`.
"Spec §x" points to `docs/superpowers/specs/2026-11-05-foreman-design.md`.

## Process

- D1 · 2026-11-05 · Order of work: research of agent/transport integration facts → architecture contract →
  core skeleton → parallel work packages (sessions+hooks, transports+messaging, watch+limits, web API,
  web frontend, Telegram+notify, findings+init+install) → end-to-end tests → independent review → delivery
  report. — Spec §10.2 proposed core → web+Telegram → self-improvement → permissions; the brief asks for every
  §8 item, which needs all of them except permissions (§4.12, explicitly not in v1).
- D2 · 2026-11-05 · Permission policy (spec §4.12) is **not** built in v1. — Spec says "not in the first release
  unless the user says otherwise"; the user has not said otherwise.
- D3 · 2026-11-05 · Worker routing for the build: Antigravity Gemini 3.8 Flash for reading/research and
  mechanical code, Claude Sonnet 5.5 for core code, Gemini 4 Argon for the web frontend, Opus (a different
  instance from the author) for reviews. — The user's routing guidance in the build brief.

## Architecture (see `docs/build/architecture.md`)

- D10 · Stdlib-only Python 3 (≥3.9), shipped inside the skill folder. — Copy-only `npx skills` distribution must
  not need a second install mechanism; runs unchanged on Linux and macOS.
- D11 · One daemon `foreman serve` (web + Telegram poll + 60 s watch loop) under systemd user unit / launchd,
  instead of a separate timer unit. — One unit per machine on both OSes; the loop is pure Python, so it costs no
  tokens (spec §4.6). `watch/heartbeat.json` makes "is it really running" checkable (no phantom monitoring).
  `foreman watch run` stays available as a one-shot pass for timers or tests.
- D12 · State lives in `$FOREMAN_HOME` (default `~/.local/state/foreman`), including `agent-work/`. — Spec §4.8/4.9:
  board, journal and work files live outside the repository; one root makes isolation in tests trivial.
- D13 · Session ↔ terminal binding through hooks: pending launch matched by the transport handle in the hook's
  environment; unknown sessions in an initialised project folder are auto-registered as `owner: user`.
  — Answers spec §9 "how sessions the user already had open get registered": on their next hook, plus a scan of
  live transport terminals at `foreman init`.
- D14 · Orchestrator guard is an allowlist for shell commands plus a hard block on file-editing tools.
  — A denylist cannot be complete; spec §4.1 requires that the product, not prose, prevents drift.
- D15 · Delivery to an idle Claude Code session uses the Stop hook's `decision: block` with the digest as reason;
  other agents get the digest typed into their terminal by a detached deliverer after `wait idle`.
  — No typing into a TUI that may be mid-render; never interrupts a busy session (spec §4.2/§4.7).
- D16 · Every worker turn end wakes the orchestrator with a one-line digest (batched with other pending wakes).
  — Spec §4.6 "a session finished"; this is what the user did by hand ("wait tui-idle, read") in past orchestrations.
- D17 · Thresholds (spec §9): stuck = busy with no hook event for 20 min; waiting too long = waiting for input or
  permission for 15 min; quiet = no unanswered question, no stuck/gone session and nothing busy.
  All are config overrides.
- D18 · Web UI actions (open/close/rotate) are sent to the orchestrator as structured chat messages, not executed
  by the web server directly. — Spec §5.2 "ask it to open, close or rotate"; one decision maker per project.
- D19 · Web access: bind only to configured addresses, reject non-private client addresses, per-device token set
  by a one-time pairing code; notification links carry no token. — Spec §5.2 access rules.
- D20 · Work-folder janitor announces at TTL−1 day (journal + General notification) and deletes at TTL only if
  announced. — Spec §4.9 "the list is shown before deletion" with no user action needed.
- D21 · Project gates (spec §9 syntax): `project.json` `gates` is a list of named gates
  (`spec-approval`, `pr-merge`, `close-user-session`, `expensive-model`, or free text); the orchestrator briefing
  lists them and `foreman gate <name>` answers "ask the user" or "proceed".
