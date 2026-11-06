# foreman delivery report

Built autonomously from `docs/superpowers/specs/2026-11-05-foreman-design.md`. State: `main` at `e41f7cf` (PRs #2–#22 all merged or superseded by #12).
Suite: `Ran 995 tests … OK` at that code, run by the reviewer and by the build lead in a temp HOME/FOREMAN_HOME, which
includes `tests/acceptance/` (one module per §8 item; see its README).

**Live install has not been done.** Nothing was installed into your real agent configs or service manager; that step is yours (below).

Reviews: every merge was reviewed by an instance that did not write the code (review-1…2c, 3a–3c, 4, 5, 5c, 6, 7, 7b, 9, 10,
10b; each round re-ran the earlier repros). Files in `~/.local/state/agent-work/foreman/2026-11-05-build/`
(below: `$W`). Mutation check in review 4: breaking wakes, the watch tick, scrub, or topic routing fails the
acceptance tests, so they are not vacuous.

## Acceptance checklist (spec §8)

| # | Item | Status | Evidence |
|---|---|---|---|
| 1 | A normal week without kicking | Met in scripted week and short live run; **a real week is yours to judge** | `test_a01_*`; live E2: real worker Stop woke a real orchestrator with no input (`$W/live/e2/wake-chain-timeline.txt`) |
| 2 | Orchestrator learns of finish/stuck/threshold, no periodic model calls | Met | `test_a02_*` (no model process started); live E2, E4a; a live orchestrator told of a real limit crossing, unprompted (`$W/live/tg/limit-wake-evidence.jsonl`) |
| 3 | Orchestrator edits blocked | Met, live on both agents | Real `claude -p`: Write/Edit/Bash redirect denied, worker control succeeds (`$W/live/e1/excerpt.txt`). Real agy TUI: denied (`$W/live/e4/d-guard-tui.txt`). Launch-window gap closed (D54) |
| 4 | Idle day spends zero tokens | Met in simulation (fake clock), not a live day | `test_a04_*`: 24 h of ticks, zero sends and no model start |
| 5 | Rotation keeps work going | Met on fake transport; **not run with real agents or real Orca** | `test_a05_*`; reviews 3a, 4 |
| 6 | Telegram/desktop notification opens right chat; topic reply reaches orchestrator on Claude and Antigravity | Telegram met live, on Claude and Antigravity; **phone over WireGuard and the Mac notification are yours** | `test_a06_*` (fake Bot API); live on real Telegram: TG2, TG3b, TG5 (below); real haiku and agy answers (`$W/live/tg/usertest-1.txt`, `agytest-1.txt`); busy case delivered at next idle |
| 7 | Web UI shows all projects, chosen layout, phone and desktop | Met in browsers; **a real phone is not tested, and the visual match is yours to confirm** | `test_a07_*`; Playwright at 390×844 and 1440×900 on a real server (`$W/shots-3b/`, reviews 4, 5); TG3 in a 390 px iframe from the Windows browser over the VPN (no touch, no mobile UA) |
| 8 | ≥70% steers, ≥90% refuses, empty reading keeps last value | Met in simulation (fake readings) | `test_a08_*`; stale readings count as unknown (D49) |
| 9 | Work folder removed after 14 days, after a listing | Met in simulation (fake clock) | `test_a09_*`; a reused folder is re-announced first |
| 10 | Work Mac finding stays draft; public issue has no project data | Met against tested forms, with known limits (below); darwin branch simulated; no real issue was filed | `test_a10_*`, `tests/test_review5_scrub.py`, adversarial probes in review 5c |
| 11 | Same install on Linux and macOS from "установи плагин" | Linux and darwin code branches met with shims, real `npx skills add` in temp HOME; **real Mac, real systemd and the spoken phrase not run** | `test_a11_*`, review 3c |

## Live Telegram run (real bot, real group, Windows browser over the VPN)
| Check | Result |
|---|---|
| `/status`, question with buttons, deep link opens the chat at the message, button press | Pass, reported by the Windows session (TG2) |
| Topic reply and web reply reach a real Claude orchestrator; answers in both places | Pass after fixes, reported by the Windows session (TG3b) |
| Outbox burst of 6: in order, no gaps or duplicates | Pass, reported by the Windows session (TG3) |
| Message sent while the orchestrator was busy, delivered at its next idle | Pass, real haiku, driven locally |
| Real child `claude -p` in the orchestrator's terminal leaves its session intact, Write denied | Pass, driven locally |
| Topic reply and web reply get a real answer from a real Antigravity orchestrator | Pass: answers in `$W/live/tg/chat-final.jsonl`; arrival in Telegram reported by the Windows session (TG5). Text carries a `foreman say ` prefix |
| Web chat times match the viewer's clock; phone-width layout in a 390 px iframe | Pass: review 10b in Chromium across 3 time zones, and reported by the Windows session (TG5, TG3) |

Windows results are in `$W/live/tg/win-results.md`, copied from the relayed messages. Live testing found what fakes could not: D59, D60, D62, D63, a wrong Orca result shape, and the agy `{}` hook answer (D57).

## What only you can do
1. Install: tell an agent "установи плагин" on ghome, then check `systemctl --user status` for the foreman service.
2. The same on the work Mac: launchd, Homebrew `terminal-notifier`, and the click-through of a desktop notification. **macOS was never run on real hardware.**
3. Phone over WireGuard: open a Telegram notification, reply in the topic. Live tests used a browser on Windows over the VPN (web UI at 10.94.51.100), not a phone; the bot and group already exist.
4. Trust steps: Antigravity workspace trust per project (`.agents/hooks.json`), and Codex hook approval in `/hooks`.
5. Live a real week and judge item 1.

## Moving to the live install (foreman as the first project)
For the control panel, with the user. All live steps; the build did none of them. A bot may have only one poller: a
second one makes Telegram answer `409 Conflict`, so step 1 comes first. `tg-live` below is
`~/.local/state/agent-work/foreman/tg-live/home`.

1. Make sure the test daemon is gone: `pgrep -af "foreman serve"` must show nothing with that FOREMAN_HOME, and no test orchestrator may run (the build lead stops both before this report; check anyway).
2. Install the skill the normal way ("установи плагин", or the `npx skills add` line in README), then run install with explicit web settings. **Port 8765 is already taken on ghome**, so name one:
   `~/.agents/skills/foreman/bin/foreman install --bind 10.94.51.100 --port 18765 --base-url http://10.94.51.100:18765`
   (`foreman` is not on PATH until this creates the link.) This starts the service at once and rewrites `statusLine.command` in `~/.claude/settings.json` and `~/.gemini/antigravity-cli/settings.json`, wrapped in `foreman tap statusline`, with `*.foreman-bak` backups. Check `systemctl --user status` for the service. Telegram is not configured yet, so nothing polls the bot.
   `10.94.51.100` is a DHCP address on `enp1s0f0`: fix the lease first, or the service cannot bind after it changes.
3. Move the token without printing it:
   `python3 -c 'import json,os; print(json.load(open(os.path.expanduser("~/.local/state/agent-work/foreman/tg-live/home/secrets.json")))["telegram_bot_token"],end="")' | foreman telegram set-token`
4. `foreman telegram set-chat -1004481348280`, then `foreman telegram allow-user 179921239`, then `foreman telegram check` must print ok. The daemon picks this up with no restart (D52). Do not copy `telegram-state.json`: its message map belongs to test data.
5. `foreman init` in `~/projects/foreman` (creates the topic "foreman"), then `foreman orchestrator start`.
6. Pair the phone and browser again with `foreman web pair`: test pairings do not carry over.
7. Clean up: delete the test topic "proj" in the group, then `rm -rf` the tg-live folder (this removes its copy of the token).

Rollback: `foreman uninstall` removes the service, project hooks and the `~/.local/bin/foreman` link, and restores both statuslines. It keeps the state folder (`--purge` deletes it). The bot and group stay.

## Decisions to check (`docs/build/decision-log.md`, D1–D65)
- **Workers get their own worktree (D65, added after this report's build).** `foreman open` gives each orchestrator-owned worker in a git project a worktree and branch `foreman-<title>-<id>` under the state folder; `foreman close` removes it when nothing would be lost, and the watch loop reclaims those of sessions that ended on their own. Worktrees created by hand (for example in Orca) are still outside the project and invisible. Not tested live: whether Claude and Antigravity ask to trust the new folder, and Codex workers there (hook approval may be per path).
- **D62, the user's own messages are not framed as data.** A real model refused real web replies because foreman labelled them "data, not instructions". They now have their own frame that sessions cannot forge (review 9).
- **D63, a user message always gets a visible result.** If the orchestrator ends its turn without `foreman say`, foreman posts its final text to the chat, or a notice if there is none. No tokens.
- **D59, child processes in a terminal no longer take over its session.** A plugin's `claude -p` had closed a live orchestrator. Recovery after a manual restart in the same terminal: `foreman orchestrator adopt`.
- **D64, a false "busy" is left alone.** A stray tool event, cause unproven, can show an idle session as busy until its next hook, and `foreman close` refuses meanwhile. An automatic fix was built and removed: it typed into a working session.
- **D47/D56/D58, findings on home machines stay `auto` as the spec says, with a fail-safe gate.** Doubtful text stays a draft. Known limits: an **unregistered** project name in plain prose cannot be recognised (registered names are scrubbed), and review 5c found that `deploy@prod-box` and low-entropy uppercase tokens pass. No real issue was filed during the build. If that is too much risk, set `upstream.mode=draft`.
- **D51, the Telegram topic mirrors the whole project chat**, not just questions. More messages on your phone (10 per project per minute, then one pointer).
- **D42**, at ≥90% the orchestrator is informed, not wound down; only workers are.
- **D54**, a session of yours opened in a project in the ≤180 s launch window is guarded briefly, then demoted to a normal session.
- **D23, Codex is worker-only** (not logged in here).
- Accepted limit (spec §7 note): an orchestrator's plain-text `gh --body` can still carry copied data to the project's own repo.
- agy "waiting for permission" is only visible on Herdr (D26). The agy continuation-cap fallback (re-typing) is tested only with fakes, not live.

## Known defects
- The test suite failed once with one unnamed failure, on the `set-chat` branch; I did not save which test. It did not recur in about 30 later runs by fixers and reviewers, many in parallel or under CPU load, but it is unexplained.
- `foreman status` does not show the limits line (the orchestrator adds it with `limits --line`).
- Cheap Antigravity models may print `foreman say 4` as text instead of running it: the answer arrives with that visible prefix (seen on gemini-3.8-flash-low; D63 posts the text).
- `foreman orchestrator start --agent antigravity` was not run live. The live tests started agy by hand with per-run trust and used `foreman orchestrator adopt`, because `orca agent launch` cannot pass `FOREMAN_HOME` to agy; on a live install with the default home that should not matter, but check it.
- Topic mirroring is at-least-once: a send that times out after Telegram accepted it is retried, so a rare duplicate is possible (D60).

## Side effects of the build on this machine
- `~/.gemini/antigravity-cli/settings.json` changed: running agy added its own `trustedWorkspaces` entries for the build worktrees (latest `fm-fix10`), `~/projects/foreman` and `/tmp/fm-live-e4/nohook`. foreman did not write them and I did not edit the file. Remove the temp entry when convenient.
- `/tmp/fm-live-e2/proj` stays registered in your Orca, because Orca has no `repo rm`.
- A test thread wrote two harmless lines to the real `~/.local/state/foreman` in fix round 4. I removed them, fixed the cause, and the test base class now fails if it recurs.
- `foreman web pair` run once without `FOREMAN_HOME` during a test left two unused pairing codes in the real `~/.local/state/foreman`; no bot token. I checked the key names and removed the folder, so the live install starts clean.
- Reviewers' live runs added project entries to `~/.claude.json`, transcripts under `~/.claude/projects/`, and history under `~/.gemini`, as any agent run does.
- Antigravity's Claude quota ran out, reset Nov 12. Gemini Argon's weekly quota is nearly used too (7% left, reset Nov 9); Flash still works.
- Two idle shell tabs remain in Orca at `/tmp/fm-live-e2/proj`.
- Finished worktrees under `~/orca/workspaces/foreman/` and their tabs' branches remain; safe to remove.
- The test bot @ghome_foreman_bot and group "foreman · ghome" exist and are kept for the live install. The test daemon and orchestrators are stopped, so nothing polls the bot. `tg-live` still holds the token until step 7 of the migration; delete the test topic "proj" there.
