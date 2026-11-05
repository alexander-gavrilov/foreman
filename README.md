# foreman

Agent-agnostic orchestration of AI agent sessions (Claude Code, Antigravity, Codex) over Orca and Herdr.
Each project gets one orchestrator session that opens, closes and rotates workers, watches context windows and
subscription limits, and keeps a board and a journal. A watch loop, hooks and a web UI / Telegram front end keep
it current without polling the model.

Spec: `docs/superpowers/specs/2026-11-05-foreman-design.md`. Build contract: `docs/build/architecture.md`.

## Install

Say one phrase to any supported agent: **"установи плагин"** (or "install foreman"). It runs:

```
npx skills add alexander-gavrilov/foreman -g -y
~/.agents/skills/foreman/bin/foreman install      # --machine-kind work on a work Mac
foreman init                                      # once per project
```

Linux and macOS, Python 3.9+, standard library only. `foreman uninstall` reverts the install, including
foreman's hooks in every initialised project (`--purge` also deletes the state, and only a folder named `foreman`).

Install with `npx skills add` only. `foreman install` links `~/.local/bin/foreman` to the skill folder it runs
from, and the Claude Code plugin manifest (`.claude-plugin/`) installs into a versioned cache folder that
disappears on update, which would leave the link, the service and every hook pointing at nothing. If you use
the manifest for the skills, still run `install` from the `npx` copy.

A non-default `FOREMAN_HOME` is written into every hook and statusline command (`FOREMAN_HOME=... foreman hook ...`)
and into the service unit, so hooks and the daemon share one state. Move the state, then run `foreman install` and
`foreman init` again to refresh them.

## Commands

| Command | What it does |
|---|---|
| `foreman init [--path] [--name] [--remove]` | Set up a project: state, board, hooks per agent present, work-file rule, Telegram topic (`--remove` undoes it) |
| `foreman install` / `uninstall` | Machine setup: symlink, config, service, statusline taps / revert (uninstall also removes foreman's hooks from every initialised project) |
| `foreman status [--all]` | Sessions, state, context, owner |
| `foreman limits [--line]` | Subscription windows with usage, reset and reading age |
| `foreman open` / `close` / `rotate` | Start, stop or replace a worker session |
| `foreman send` / `ask` / `reply` | Message any session; `ask` waits for the answer |
| `foreman say` | Orchestrator to user (chat, Telegram, desktop notification) |
| `foreman board` / `log` | Board of tasks; append-only journal |
| `foreman stage <stage>` | A session reports a stage change |
| `foreman gate list` / `check <name>` | Project gates ("ask the user" or "proceed") |
| `foreman work new <topic>` / `close` | Work-file folders outside the repository |
| `foreman clean [--dry-run]` | Delete work folders idle for 14 days |
| `foreman report <text> [--kind foreman\|project]` | Record a finding; `--list`, `--send <id>` |
| `foreman watch list` | Active watches (the loop, sessions, limits) |
| `foreman web pair` / `serve` | Web UI pairing; the daemon (web, Telegram, watch loop) |
| `foreman hook` / `tap` / `deliver` | Internal: agent hooks, statusline tap, message delivery |

Findings about foreman itself are scrubbed of project data before they leave the machine; at home they become a
GitHub issue, on a work machine they stay a local draft until you send them.

Skills: `skills/foreman` (every agent), `skills/foreman-orchestrator` (the orchestrator role). Tests:
`python3 -m unittest discover -s tests -t .`
