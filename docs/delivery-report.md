# foreman delivery report

Built autonomously from `docs/superpowers/specs/2026-11-05-foreman-design.md`. State: `main` at `8e63558` (PR #12 merged;
#2 #3 #4 #5 #6 #7 #8 #9 #10 #11 all merged). Suite on main: `Ran 826 tests … OK`, run in a temp HOME/FOREMAN_HOME, which
includes `tests/acceptance/` (one module per §8 item; see its README).

**Live install has not been done.** Nothing was installed into your real agent configs or service manager; that step is yours (below).

Reviews: every merge was reviewed by an instance that did not write the code (review-1…2c, 3a–3c, 4, 5, 5c; the last
three rounds re-ran each earlier reviewer's repro). Files in `~/.local/state/agent-work/foreman/2026-11-05-build/`
(below: `$W`). Mutation check in review 4: breaking wakes, the watch tick, scrub, or topic routing fails the
acceptance tests, so they are not vacuous.

## Acceptance checklist (spec §8)

| # | Item | Status | Evidence |
|---|---|---|---|
| 1 | A normal week without kicking | Met in scripted week and short live run; **a real week is yours to judge** | `test_a01_*`; live E2: real worker Stop woke a real orchestrator with no input (`$W/live/e2/wake-chain-timeline.txt`) |
| 2 | Orchestrator learns of finish/stuck/threshold, no periodic model calls | Met | `test_a02_*` (no model process started); live E2, E4a |
| 3 | Orchestrator edits blocked | Met, live on both agents | Real `claude -p`: Write/Edit/Bash redirect denied, worker control succeeds (`$W/live/e1/excerpt.txt`). Real agy TUI: denied (`$W/live/e4/d-guard-tui.txt`). Launch-window gap closed (D54) |
| 4 | Idle day spends zero tokens | Met | `test_a04_*`: 24 h of ticks, zero sends and no model start |
| 5 | Rotation keeps work going | Met on fake transport; **not run with real agents** | `test_a05_*`; reviews 3a, 4 |
| 6 | Telegram/desktop notification opens right chat; topic reply reaches orchestrator on Claude and Antigravity | @@ITEM6@@ | `test_a06_*` (fake Bot API); real `user_reply` into a real agy orchestrator (`$W/live/e4/c-user-reply.txt`); deep link on phone width (`$W/shots-3b/02-phone-deeplink-chat.png`) |
| 7 | Web UI shows all projects, chosen layout, phone and desktop | Met; **visual match is yours to confirm** | `test_a07_*`; Playwright at 390×844 and 1440×900 on a real server (`$W/shots-3b/`, reviews 4, 5) |
| 8 | ≥70% steers, ≥90% refuses, empty reading keeps last value | Met | `test_a08_*`; stale readings count as unknown (D49) |
| 9 | Work folder removed after 14 days, after a listing | Met | `test_a09_*`; a reused folder is re-announced first |
| 10 | Work Mac finding stays draft; public issue has no project data | Met with one known limit (below); darwin branch simulated | `test_a10_*`, `tests/test_review5_scrub.py`, adversarial probes in review 5c |
| 11 | Same install on Linux and macOS from "установи плагин" | Linux and darwin code branches met with shims, real `npx skills add` in temp HOME; **real Mac, real systemd and the spoken phrase not run** | `test_a11_*`, review 3c |

## What only you can do
1. Install: tell an agent "установи плагин" on ghome, then check `systemctl --user status` for the foreman service.
2. The same on the work Mac: launchd, Homebrew `terminal-notifier`, and the click-through of a desktop notification. **macOS was never run on real hardware.**
3. Phone over WireGuard: open a Telegram notification, reply in the topic. @@TGSTEP@@
4. Trust steps: Antigravity workspace trust per project (`.agents/hooks.json`), and Codex hook approval in `/hooks`.
5. Live a real week and judge item 1.

## Moving to the live install (foreman as the first project)
For the control panel, with the user. All live steps; the build did none of them. A bot may have only one poller: a
second one makes Telegram answer `409 Conflict`, so step 1 comes first. `tg-live` below is
`~/.local/state/agent-work/foreman/tg-live/home`.

1. Make sure the test daemon is gone: `pgrep -af "foreman serve"` must show nothing with that FOREMAN_HOME, and no test orchestrator may run (the build lead stops both before this report; check anyway).
2. Install the skill the normal way ("установи плагин", or the `npx skills add` line in README), then run install with explicit web settings. **Port 8765 is already taken on ghome**, so name one:
   `foreman install --bind 10.94.51.100 --port 18765 --base-url http://10.94.51.100:18765`
   Check `systemctl --user status` for the service. Telegram is not configured yet, so nothing polls the bot.
3. Move the token without printing it:
   `python3 -c 'import json,os; print(json.load(open(os.path.expanduser("~/.local/state/agent-work/foreman/tg-live/home/secrets.json")))["telegram_bot_token"],end="")' | foreman telegram set-token`
4. `foreman telegram set-chat -1004481348280`, then `foreman telegram allow-user 179921239`, then `foreman telegram check` must print ok. The daemon picks this up with no restart (D52). Do not copy `telegram-state.json`: its message map belongs to test data.
5. `foreman init` in `~/projects/foreman` (creates the topic "foreman"), then `foreman orchestrator start`.
6. Pair the phone and browser again with `foreman web pair`: test pairings do not carry over.
7. Clean up: delete the test topic "proj" in the group, then `rm -rf` the tg-live folder (this removes its copy of the token).

Rollback: `foreman uninstall` removes the service and project hooks. The bot and group stay.

## Decisions to check (`docs/build/decision-log.md`, D1–D58)
- **Workers do not get their own worktree: please decide before running foreman on foreman.** `foreman open` starts every worker in the project's main folder, so parallel workers share one checkout. A session started by hand in an Orca worktree is outside the project path and has no hook files, so foreman does not see it. The spec does not ask for worktrees, so this is not an acceptance failure, and I did not build it. It is the first thing I would add.
- **D47/D56/D58, findings on home machines stay `auto` as the spec says, with a fail-safe gate.** Doubtful text stays a draft. Known limit: an **unregistered** project name in plain prose cannot be recognised; registered names are scrubbed. Review 5c also found that `deploy@prod-box` and low-entropy uppercase tokens pass. If that is too much risk, set `upstream.mode=draft`.
- **D51, the Telegram topic mirrors the whole project chat**, not just questions. More messages on your phone (10 per project per minute, then one pointer).
- **D42**, at ≥90% the orchestrator is informed, not wound down; only workers are.
- **D54**, a session of yours opened in a project in the ≤180 s launch window is guarded briefly, then demoted to a normal session.
- **D23, Codex is worker-only** (not logged in here).
- Accepted limit (spec §7 note): an orchestrator's plain-text `gh --body` can still carry copied data to the project's own repo.
- agy "waiting for permission" is only visible on Herdr (D26). The agy continuation-cap fallback (re-typing) is tested only with fakes, not live.

## Known defects
- The test suite failed once with one unnamed failure, on the `set-chat` branch; I did not save which test. It did not recur in about 30 later runs by fixers and reviewers, many in parallel or under CPU load, but it is unexplained.
- `foreman status` does not show the limits line (the orchestrator adds it with `limits --line`).
- Topic mirroring is at-least-once: a send that times out after Telegram accepted it is retried, so a rare duplicate is possible (D60).

## Side effects of the build on this machine
- `~/.gemini/antigravity-cli/settings.json` changed: running agy added its own `trustedWorkspaces` entries for the build worktrees, `~/projects/foreman` and `/tmp/fm-live-e4/nohook`. foreman did not write them and I did not edit the file. Remove the temp entry when convenient.
- `/tmp/fm-live-e2/proj` stays registered in your Orca, because Orca has no `repo rm`.
- A test thread wrote two harmless lines to the real `~/.local/state/foreman` in fix round 4. I removed them, fixed the cause, and the test base class now fails if it recurs.
- Antigravity's Claude quota is used up for about a week (it died at the first review on it); Gemini is unaffected.
- Finished worktrees under `~/orca/workspaces/foreman/` and their tabs' branches remain; safe to remove.
@@TGCLEAN@@
