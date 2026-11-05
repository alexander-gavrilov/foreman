# Acceptance scenarios for spec §8

One module per item of `docs/superpowers/specs/2026-11-05-foreman-design.md` §8. Each is an end-to-end scenario
through the real entry point (`skills/foreman/bin/foreman` as a subprocess; hooks are piped real payloads) in a
temporary HOME / FOREMAN_HOME, with the file-backed fake transport, a fake clock (`FOREMAN_NOW`) and the local
fake Telegram Bot API server (`tests/fakes/fake_telegram.py`). The shared harness is `common.py`.

## Run

```
python3 -m unittest discover -s tests/acceptance -t .          # all acceptance scenarios (~3 min)
python3 -m unittest tests.acceptance.test_a05_rotation         # one item
python3 -m unittest discover -s tests -t .                     # the whole suite (includes these)
```

## Safety

The subprocess environment is built from scratch: no `ORCA_*`, `HERDR_*`, `CLAUDE_*` variables; `PATH` holds only a
shim directory plus the Python directory. The shims (`claude agy antigravity codex gemini gh orca herdr notify-send
terminal-notifier osascript systemctl launchctl brew`) record their argv in a log and do nothing else, so a test can
assert "no model was started" and "nothing was posted". `config.json` sets `upstream.mode=draft`; the only place a
`gh issue create` argv is built (item 10) goes through an injected runner and `findings.default_runner` is rigged to
fail the test if a real `gh` were run.

## Status per item

| # | Module | Covers | Status |
|---|---|---|---|
| 1 | `test_a01_week_without_kicking` | a simulated week of Stop/stuck/limit/context/gone events reaches the orchestrator unprompted; status/board reflect it | pass |
| 2 | `test_a02_events_without_models` | finish / stuck / 70% / 90% → inbox → transport, no `claude`/`agy` spawn | pass |
| 3 | `test_a03_orchestrator_cannot_work` | Edit/Write/shell-write/MCP/git attempts denied (Claude + Antigravity dialects) | pass |
| 4 | `test_a04_idle_day` | 24 h of watch ticks: zero sends, zero model starts | pass |
| 5 | `test_a05_rotation` | handoff message, one batched ask + answer routed back, board shows old → new | pass |
| 6 | `test_a06_notifications` | Telegram topic + deep link, topic reply → Claude and Antigravity orchestrators, macOS `terminal-notifier -open` argv | pass |
| 7 | `test_a07_web` | `foreman web` + pairing + `/api/state` lists every project | pass |
| 8 | `test_a08_limits` | ≥70% steers, ≥90% refuses, empty reading keeps last value | pass; 1 expected failure (stale reading after window reset, PRODUCT BUG) |
| 9 | `test_a09_janitor` | 14-day folder listed then removed by the watch loop alone | pass |
| 10 | `test_a10_findings` | work-Mac default is a local draft; public issue argv has no project data | pass; 1 expected failure (credentials are not scrubbed, PRODUCT BUG) |
| 11 | `test_a11_install` | install/uninstall round trip, Linux and darwin branches, installed artifacts executed | pass |

`@unittest.expectedFailure` marks a scenario that exposes a product bug; the comment above it starts with
`PRODUCT BUG:`. When the product is fixed the test starts "unexpectedly succeeding": remove the decorator.

## What still needs the user (not testable here)

- **Item 1** over a *real* week: the "no kicking" outcome is a judgement of the user's week, not of one test run.
- **Item 6**: a Telegram notification opening the right chat **on a phone over WireGuard**, a reply typed in a real
  topic, and the real `terminal-notifier` click-through on the work Mac. Linux has no desktop-notification channel
  in v1 (spec §5.3 only defines Telegram for home and the macOS notification for the work Mac).
- **Item 7**: "matching the chosen layout on phone and desktop" is a visual check (use the mockup
  `assets/2026-11-05-web-ui-mockup.html` side by side); the API behind it is covered here.
- **Item 11**: the phrase "установи плагин" said to a real agent (the agent reading `SKILL.md` and running
  `npx skills add`), a **live install on a real Mac** (launchd, Homebrew, `terminal-notifier`), and the real
  `systemctl --user` on the home server. Here the service manager and brew are recording shims.
- Orchestrators on a live Antigravity (hook `deny` and Stop `continue` verified against the user's real Orca hook), and
  Codex hook trust, are one-time user steps (architecture §4a).
