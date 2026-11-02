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
