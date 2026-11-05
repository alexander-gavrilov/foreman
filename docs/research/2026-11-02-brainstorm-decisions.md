# foreman — brainstorm decisions so far (2026-11-01 … 11-02)

Origin: Claude Code session `7c8eb793` in `~/obsidian/shopping-list`
(transcript: `~/.claude/projects/-home-alexander-obsidian-shopping-list/7c8eb793-0e6e-40c9-b304-9b662225b5d6.jsonl`).
The design conversation (superpowers:brainstorming) is NOT finished; the
written design doc has not been produced or approved yet. Items marked
**user** were stated/confirmed by the user; **proposal** = Claude's proposal
the user has not explicitly objected to; **open** = still to decide.

## What foreman is
- **user** A standalone, agent-agnostic plugin (Claude Code, Antigravity, Codex at least)
  for orchestrating agent sessions in any project: an orchestrator session opens,
  closes, freezes/replaces sessions, controls context-window size and subscription
  limits, keeps project state in sync, and improves its own rules.
- **user** Name: `foreman` ("orchestrator" too ambiguous).
- **user** The user is satisfied with *our* orchestration approach; existing
  orchestration tools (claw-orchestrator, zag, coders, Maestro, myclaude) are not to be adopted.
- **user** Uses both Herdr and Orca as terminal/agent transports → both first-class
  (adapter per transport; Claude inter-session messaging as another channel).

## Autonomy
- **user** Default: act without approvals unless the user gives explicit instructions
  otherwise (project-level setting can add gates, e.g. "approve every spec").
- **proposal** Standing user rules still apply under autonomy (merge only via PR,
  reviewer is always a different instance). Autonomous actions are reported in one
  line + logged, not asked.
- **proposal** Exception: changes to foreman's own rules/instructions go through PR
  (agents process untrusted data; self-rewriting rules = prompt-injection hole). The
  orchestrator may prepare and drive the PR.

## Context & handoff
- Soft target ~150k tokens per session (guideline, not hard rule): continue / `/compact` / rotate.
- `/compact` keeps full pre-compaction history in the transcript `.jsonl` (`compact_boundary` marker).
- **user** Preferred rotation style (historical): freeze old session, open new one,
  pass handoff by **direct message**, new session may ask the old one questions.
- **proposal** Handoff by message only, no handoff files (message persists in the
  recipient's transcript; old transcript is the archive). Q&A window: new session
  batches all questions to the old one within ~1h while its prompt cache is warm.
  Cost ladder afterwards: handoff text → grep old transcript → ask old session (only "why" questions, batched).
- Measured: a question to a cold 220k session ≈ 2× cache write ≈ 440k base-token
  equivalent vs ≈ 22k when warm (1h cache TTL).

## File hygiene (user concern: accumulating temp/handoff files pollute other agents' context)
- **proposal** Three classes: permanent (repo) / process work artifacts (outside repo:
  `~/.local/state/agent-work/<project>/<date>-<topic>/`, only seen when path is passed) / junk (.gitignore).
- **proposal** On task close the orchestrator promotes (short summary into backlog/tech-debt) or deletes;
  janitor deletes unclosed work dirs after 14 days idle; listing before deletion.
- **proposal** One rule in each project's AGENTS.md/CLAUDE.md: don't create work files in the repo; don't read other agent-work dirs unless given a path.

## State sync (user: "log the process so we don't repeat stages")
- **proposal** Board (current state, ≤1 screen: task, stage, owner session, next step, links)
  + append-only journal (grep only). Orchestrator is the single writer; sessions report
  stage transitions by one-line messages. Stored outside the repo.

## Self-improvement
- **user** The agent working in a project notices problems first. Findings must NOT
  accumulate inside the consumer project. Destinations: GitHub issue in foreman's
  repo, or the foreman project's own control panel (orchestrator inbox).
- **proposal** One shell command (`foreman report`) for all vendors; routes to GitHub
  issue if available, else local inbox outside the project; scrubs project data
  (paths, content, IDs). Two kinds: plugin-level findings → upstream; project-rule
  findings → stay in the project's own lessons → weekly proposal → PR.
- **proposal** Guardrails: fix in code before prose; instruction size budget; evidence-only
  rules; check whether the problem recurs; no auto-edits of rules.

## Monitoring cost (measured)
- `/loop 20m` orchestrator: 29 quiet ticks in ~10h, zero actions, ≈5.6M cache-read
  tokens (~19k base-equivalent per tick), context +1k/tick. Stopped.
- **user** approved replacing ticks with: systemd user timer runs a script (0 tokens),
  wakes the orchestrator only on new events (threshold crossed, limit ≥70/90%, stuck session).
- Hooks exist in all three vendors (Claude Code; Codex `hooks.json` with session_start,
  user_prompt_submit, pre/post_tool_use, permission_request, subagent_start; Gemini CLI
  BeforeAgent/AfterAgent; Antigravity project hooks via `~/.gemini/trusted_hooks.json`).
  **proposal** One common hook handler registered per vendor by `foreman init`:
  sessions self-report status/context per turn → polling unnecessary; permission
  requests routed through a shared policy (long-standing agy pain point).

## Subscriptions & limits
- **user** Two subscriptions (Claude Code, Antigravity); the orchestrator may use either but must respect limits.
- Limits are readable from statusline stdin JSON: Claude `rate_limits.five_hour/seven_day`;
  agy `quota["gemini-5h"|"gemini-weekly"|"3p-5h"]` + `context_window`. Prior art: statusline
  patches writing snapshots to `~/.cache/agent-limits/` (see prior-art/).
- **proposal** ≥70% → prefer other provider; ≥90% → no new sessions there, wind down;
  weekly window weighs more than 5h.

## Deployment
- **user** Install UX: user says "установи плагин" and the agent does everything.
- **proposal** No custom installer: `npx skills add <repo>` (vercel-labs/skills — already
  used on this machine, lock at `~/.agents/.skill-lock.json`) for all vendors; Claude Code
  plugin manifest in the same repo as a bonus path; scripts shipped inside the skill folder,
  pure-stdlib Python (no deps → no second install mechanism; `uv tool install git+…` only if deps ever needed).
  Project setup is itself a skill + `foreman init` (idempotent).

## The `foreman` CLI (deterministic, 0 tokens; skills = when/why, CLI = how)
- **proposal** `status`, `limits`, `send`/`ask` (transport-agnostic, explicit "terminal gone"),
  `open`/`close`, `board`/`log`, `watch` (timer entry), `clean`, `report`, `init`, and
  **user** `web` — a web UI.
- **user** Web UI must work on mobile AND desktop. Stack should be chosen on merit, not copied from shopping-list.
- **proposal** Stack: stdlib Python server + SSE for live updates + single HTML/vanilla JS,
  no build step (fits `npx skills` copy-only distribution); localhost + token by default.
  Shows sessions/context/limits/board/journal/findings; actions open/close/send/ask orchestrator.

## Prior art to review before the design doc
- `~/projects/delegate-agents` (skill + install.sh + commands), `~/projects/agentic-loop`
  (ORCHESTRATION.md, squad), `~/projects/work-inventory` (session dashboard web app spec),
  `~/projects/fancy-claude` (user's Claude plugin marketplace).
- `docs/research/2026-11-01-orchestration-history.md` — analysis of the user's past orchestration sessions.
- `docs/prior-art/shopping-list-orchestrator/` — first iteration (ctx monitor, instructions, statusline patches).

## Open
- GitHub: public or private repo (affects issue scrubbing).
- What the user wants to see/do first in the web UI.
- Anti-goals ("what would make this a failure even if it works") — not yet asked.
- Shopping-list migration: its `orchestrator/` stays until foreman replaces it.

## Clarifications from the predecessor session (2026-11-02, answers to batched handoff questions)
- Limits snapshots: the agy statusline patch is live (`~/.gemini/antigravity-cli/statusline-command.sh`);
  the Claude patch was overwritten at once, because fancy-statusline's UserPromptSubmit hook re-copies its script
  over `~/.claude/statusline-command.sh` on every prompt. → The Claude limits snapshot must live
  upstream (fancy-statusline) or in a foreman hook, never as a local patch. Backups: `*.bak-orch`.
- Codex: **user** named it among the main vendors ("хотя бы … Антигравити, ClaudeCode, Кодекс").
  It is installed and used (`~/.codex` with hooks enabled, herdr-agent-state.sh). Subscription: unknown.
- Web UI host/remote access: not discussed. "localhost + token, phone via existing HTTPS reverse proxy" is a
  proposal (shopping-list has docs/deployment-https.md). Everything runs on this machine (ghome) today;
  history mentions a headless server and a laptop bridged via claude.ai.
- agy permission policy: research note + proposal only; not confirmed as v1 scope.
- Autonomy, verbatim **user**: "по умолчанию без согласований если нет четких инструкций от меня действовать подругому".
  "Per-project gates" and "foreman rule changes only via PR" are proposals.
- Python over bash: Claude's call (JSON/JSONL parsing, /proc, locking, SSE server, zero deps); no user input.
- shopping-list orchestrator a0172d76: still running and idle, no ticks. `orchestrator/` is local and uncommitted.
  "shopping-list as first consumer" was not discussed (proposal).
- **user** preferences not captured earlier: cares about token cost ("сколько стоит / зачем") → show cost evidence
  with proposals; expects claims to be verified (corrected wrong "hooks only in Claude Code" and confusion
  of deployment mechanisms with orchestration tools); discuss before building; cheap subagents for research;
  file cleanup must be automatic ("не сидеть и каждый раз думать, когда чистить"); 150k is soft
  ("если стоит продолжать в текущем контекстном окне — продолжают").
- Rejected by **user**: periodic `/loop` ticks; basing the web UI on shopping-list's stack. Name chosen from
  baton/switchboard/foreman/helm.
- **user** (2026-11-02): this foreman session is the control panel. Opus does the design talk; bulk reading, transcript search
  and the builder-check go to cheap worker sessions (Sonnet / Antigravity Flash) in Orca tabs, which report short conclusions.

## Anti-goal and core properties (confirmed by user 2026-11-02)
- **user** Failure mode: foreman works but is inconvenient. Concretely, the user had to (a) "kick" the
  orchestrator so it caught up on what was happening, and (b) correct it when it dove into a task instead of delegating.
- **user-confirmed** Core property 1: the orchestrator is always current without being asked. Events come to it
  (session hooks report stage changes / done / stuck / context threshold; a 0-token timer wakes it only on events;
  board + journal are its memory, which survives rotation).
- **user-confirmed** Core property 2: the orchestrator cannot drift into doing the work. This is enforced mechanically,
  not by prose: a hook blocks project edits for the orchestrator role, `foreman` returns short summaries instead of
  raw files, and bulk reading defaults to cheap worker sessions.
- **user-confirmed** Success: no "как дела?" and no kicking. The user shows up only when a decision is needed and
  sees the state at a glance (terminal or phone).

## Web UI & notifications (2026-11-02)
- **user** Main screen = the board and everything as agreed. When foreman calls the user, it does so by a notification,
  and opening it goes straight to the chat with the orchestrator's message.
- **user** If Telegram: wants to reply there too; probably one channel per chat.
- Recon (Sonnet worker, 2026-11-02): aibroker v0.62.4 (MIT, third-party, mnott/AIBroker) has no Telegram code
  in core. Telegram lives in the separate adapter Telex (MTProto user account via gramjs), which is NOT installed here.
  Core routes inbound to a single "active session" by PTY typing (iTerm2/tmux), is Claude-oriented, and has no
  per-chat/per-topic routing and no shell CLI for Telegram send.
- **proposal** foreman owns a small Telegram Bot API notifier (stdlib urllib, long-poll getUpdates): one forum
  supergroup, one topic per project (message_thread_id), replies routed by topic to that project's orchestrator
  via the transport adapter (orca/herdr send). Notifier is an interface; aibroker/ntfy/etc. are optional sinks.
- **user** Web UI shows the orchestrator sessions of ALL projects on the machine (machine-level, like the Telegram group).
- **user** Telegram: one group per machine, one forum topic per project with its orchestrator. Details (bot creates topics on
  `foreman init`; General topic = foreman itself; worker sessions get no topics, their questions go through the orchestrator) are proposals.
- **user** Implementation split: Antigravity does design + frontend; a Claude session verifies functionality.
  **proposal** API/event contract is fixed in the spec first; Claude verifies against the spec's acceptance criteria and the code
  (Playwright/DevTools, mobile + desktop widths), not against agy's report; Argon (not Flash) for design.

## Web UI layout (2026-11-05)
- **user** Chosen: "Direction 3", the Argon variant. Mockup (outside repo): `~/.local/state/agent-work/foreman/2026-11-02-web-ui-mockups/.superpowers/brainstorm/2309803-1793804082/content/agy-direction-3.html`.
  The user found direction 1 (dark "dispatch", 3 columns) overloaded and direction 2 (light bento cards) too simplified.
  Direction 3 = direction 1's dark style, calmer. Left triage rail of all projects grouped Ждёт вас / Застряла / Работают / Тихо;
  centre = chat of the selected project; a pinned status strip above the chat (stage, N sessions, worst context) expands into a
  drawer (desktop) / bottom sheet (phone) with Board / Sessions; top bar = weekly limit meters only (5h on hover);
  one-tap decision buttons only on a question message; a notification lands on the project chat.
- Model routing evidence: the same brief on Gemini 4 Argon (medium) vs Gemini 3.8 Flash (high): Argon ~4 min, Flash ~7 min,
  both ≈1% of the agy 5h window. The user preferred Argon's result. → **proposal** design/open-ended UI work → Argon; Flash for
  well-specified mechanical work.
- Bug to design around: the agy statusline patch overwrote `~/.cache/agent-limits/agy.json` with `quota: null`. Limit snapshots must
  never be overwritten by empty data (keep the last good value + its age).
- **user** Remote access from outside home: through the user's existing WireGuard VPN. → **proposal** `foreman web` binds to
  LAN/VPN interfaces only (never public), token auth stays; no reverse proxy / public HTTPS needed for v1.
- **user** foreman will be shared: if it proves itself on the home server, the user will run it (or at least the
  approach) on the work computer too. → GitHub repo is public / shareable (my call, follows from "можно считать, что я буду шарить").
  **proposal** Work-project data must never reach the public repo: `foreman report` scrubs paths/project names/content,
  and a per-machine setting `upstream: auto | draft | off` (draft = issue text is saved to the local inbox and the user
  sends it). Suggested default: home `auto`, work `draft`.
- **user** Work computer: macOS, with Claude Code and Antigravity. → foreman must run on Linux AND macOS:
  timer = systemd user timer (Linux) / launchd agent (macOS); no hard dependency on `/proc` (session ↔ terminal
  mapping comes from hooks self-reporting, `/proc` only as a Linux fallback); portable `stat`/paths; stdlib Python 3.

## Answers after the builder check (2026-11-05)
- **user** Telegram setup: the user gives Claude Code access to a desktop with a browser; the agent creates the bot and the group,
  turns on topics and makes the bot an admin there (no manual user steps).
- **user** Network: desktop and laptop reach the home server over OpenVPN, phones over WireGuard. The work Mac does not
  connect to the home server (no point), and the work Mac has no access to Telegram.
  → **proposal** On the work Mac, notifications are local desktop notifications opening the local web UI.
- **user** Changes to foreman's own rules go only through a PR, even under default autonomy.
- Permission policy in v1: not answered → **proposal** after core, web UI and Telegram.
