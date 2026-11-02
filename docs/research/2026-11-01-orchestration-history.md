# Orchestration history — research notes (2026-11-01)

Source: Haiku subagent analysis of the user's own messages in Claude Code
transcripts (~/.claude/projects, 458 msgs) and Antigravity history (535 prompts).
Extraction scripts and raw hits: /tmp/claude-1000/orch-research/ (ephemeral).

## Timeline
- Jun–Aug: manual delegation. 2026-08-09 wish: web app "which sessions, what's done, unanswered questions, tokens". 2026-08-16 rule: orchestrator is a separate session from a prepared prompt; delegate work to Antigravity because Claude subagents burn the 5h limit.
- 2026-09-24 (shopping-list 0b8e8525): Herdr "Control Plane" tab with Antigravity; bash loop `herdr agent read` + `send-keys`; "отвечай на них за меня".
- 2026-10-06 (home fba1c9ae): panel via herdr skill; permission prompts from agy are a constant pain; wants auto-mode classifier for agy.
- 2026-10-10..13 (photo-qualifier): roles pq-godelclaude (Fable), pq-alex-deputy / photo-qualifier-e0 (user proxy), pq-implmenter; laptop via claude.ai bridge; Q&A via SendMessage and `orca terminal send`.
- 2026-10-16 (obsidian-personal 59a7c4c1): `orca terminal create --command claude` + send + `wait --for tui-idle`.
- 2026-10-17 (photo-qualifier d7ffb8a3 "pq-cp"): "ты контрольная панель… держи контекст компактным"; shared journal `.control/log.md`, ~7 sessions append one line each.
- 2026-10-18 (obsidian-personal c8b3df8c): control panel on headless server, bridge to laptop by claude.ai URL.
- 2026-10-25..26 (monitor-choice 97a3a7fb): coordinator reads `queue/PROTOCOL.md`, one subagent per file, `.done` files as signal; Argon quota exhausted.
- 2026-10-29 (shopping-list 60aa337f, b88f566b): spec review; v2 implementation in worktree in agy tab; review ping-pong via `docs/v2-*-review.md`.
- 2026-11-01 (shopping-list a0172d76): orchestrator via `orca agent launch` (Sonnet 5.5), ORCHESTRATOR.md, registry.json, `/loop 20m`; 29 quiet ticks ≈ 5.6M cache-read tokens, zero actions; loop stopped 17:44.

## Mechanics actually used (tool-call counts)
- herdr: send-keys 151, read 114, get 68 — the main channel historically.
- orca terminal: read 44, send 29, wait tui-idle 10; `orca agent launch`.
- Claude SendMessage 42 (+ bridge/uds addresses, incoming [cross-session-message]).
- aibroker send_to_session: 2 (barely used).
- Waiting: Monitor 7, CronCreate 2.
- 7 context overflows with summary continuation (Oct 10–29). State kept in files: `.control/log.md`, `queue/*.done`, `registry.json`.

## Pain points
1. agy permission prompts handled manually; classifier blocked agent from adding its own allow rule.
2. "Phantom monitoring": claimed to watch a tab without a running monitor ("А ты точно за ней сейчас следишь?").
3. No progress visibility: "Как дела?" asked repeatedly (5× monitor-choice, 4× shopping).
4. Stalls/lost sessions: sleeping Argon session, closed terminal with nothing sent, stuck stage 15.
5. Manual routing: user pasting claude.ai session URLs, relaying messages between sessions.
6. Tick cost (2026-11-01).
7. Need a journal to avoid repeating stages (17.10, 01.11).
8. Self-review: "спецификации делаешь только ты, её никто не проверяет".
9. Headless runs look like hangs; user prefers interactive tabs.

## Stated preferences
- Control panel only coordinates; questions to a Q&A session; doesn't read long answers.
- Save subscriptions: Opus/Fable/Argon only when needed; workers Sonnet/Haiku/Flash.
- Interactive Orca/Herdr tabs, headless only for timers.
- Reviewer is always a different instance; critic gets files, not conclusions.
- Branch/worktree always; merge via PR.
- Don't close sessions without consent.
- Docs/commits English; user-facing Russian.
- Contradiction in memories: "full autonomy, no approval gates" vs "approval gate on every spec".

## Requirements derived
1. Session registry across backends (Orca, Herdr, Claude bridge/uds, agy) with busy/idle/blocked/quota.
2. Reliable delivery: send, wait-for-idle, ack, explicit "terminal gone".
3. Permission policy for agy/Claude prompts (rules written to user settings, not self-granted).
4. Event-driven monitor instead of fixed loop.
5. Shared state journal/board, summaries not full answers.
6. Context control (threshold, handoff by message, TTL cleanup of work files).
7. Model/quota-aware routing with fallback; change model of a live session.
8. Independent review + configurable approval gates.

## Design conversation so far (this session 7c8eb793)
- Context monitor: ~/.claude/sessions/<pid>.json + /proc environ ORCA_TERMINAL_HANDLE + transcript usage; limits via statusline snapshots in ~/.cache/agent-limits/.
- Compact keeps full pre-compaction history in the .jsonl (compact_boundary marker).
- Handoff: freeze old session, new session gets handoff by direct message; batch questions to old session within 1h cache window; ladder: handoff → grep transcript → ask old session.
- File hygiene: work artifacts outside repo (agent-work/<date>-<topic>), TTL janitor; board + journal with orchestrator as single writer; lessons channel → weekly proposal → user approval → PR.
- Timer (systemd, script, 0 tokens) wakes orchestrator only on events.
- New direction: extract to separate agent-agnostic plugin repo (Claude Code, Antigravity, Codex); findings from consumer projects go upstream via a shell command (`orch report`) → GitHub issue or plugin control-panel inbox, scrubbed of project data; project-level lessons stay in project.
