# Integration facts (research, 2026-11-05)

Collected by research workers (Claude Sonnet 5.5: hooks; Gemini 3.8 Flash: transports) on the build machine.
Versions: claude 2.1.318, agy 1.5.1, codex-cli 0.146.1, Orca 1.4.239, Herdr 0.9.5. Tags: [V-run] verified by running, [V-file] read from files, [V-doc]/[S-doc] documentation, UNVERIFIED.

Caveat: agy test run inherited ORCA_* env, so the user's real Orca/Herdr agy hooks may have posted 1-2 spurious events for this pane. Claude test was run with ORCA_/HERDR_ env stripped and --setting-sources project.

## 1. CLAUDE CODE
Hooks config: ~/.claude/settings.json (user), <proj>/.claude/settings.json, <proj>/.claude/settings.local.json, managed policy, plugin hooks/hooks.json [V-doc]. Flag --setting-sources, --settings <file|json> exist [V-file: claude --help].
Register: {"hooks":{"<Event>":[{"matcher":"<regex|*>","hooks":[{"type":"command","command":"...","timeout":10}]}]}} [V-run]. Types: command|http|mcp_tool|prompt|agent. Default timeout 600s (SessionEnd budget 1.5s) [V-doc].
Events (33) [V-doc]: SessionStart Setup UserPromptSubmit UserPromptExpansion PreToolUse PermissionRequest PermissionDenied PostToolUse PostToolUseFailure PostToolBatch Notification MessageDisplay SubagentStart SubagentStop TaskCreated TaskCompleted Stop StopFailure TeammateIdle InstructionsLoaded ConfigChange CwdChanged DirectoryAdded FileChanged WorktreeCreate WorktreeRemove PreCompact PostCompact PreModelSwitch PostModelSwitch Elicitation ElicitationResult SessionEnd.
stdin payloads [V-run, claude -p, haiku]:
 common: session_id, transcript_path (~/.claude/projects/<cwd-slug>/<session_id>.jsonl), cwd, hook_event_name; (+prompt_id, permission_mode, effort{level} on most; agent_id/agent_type inside subagents)
 SessionStart: +source (startup|resume|clear|compact|fork). UserPromptSubmit: +prompt. SessionEnd: +reason (observed "other"; matchers clear|resume|logout|prompt_input_exit|other).
 PreToolUse: +tool_name, tool_input, tool_use_id. PostToolUse: +tool_name, tool_input, tool_response{stdout,stderr,interrupted,...}, tool_use_id, duration_ms.
 Stop: +stop_hook_active, last_assistant_message, background_tasks[], session_crons[].
 PermissionRequest [S-doc]: tool_name, tool_input, permission_suggestions[]. Notification [S-doc]: message (+notification_type; values permission_prompt, idle_prompt, auth_success, elicitation_dialog per ~/.claude/plugins/marketplaces/claude-plugins-official/plugins/claude-code-setup/skills/claude-automation-recommender/references/hooks-patterns.md). StopFailure matchers: rate_limit, overloaded, authentication_failed, billing_error, server_error, unknown [V-doc]. Exact Notification/PermissionRequest/StopFailure payloads NOT captured in -p mode: UNVERIFIED.
BLOCK a tool: stdout {"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"X"}} exit 0 -> tool did not run, model saw "PreToolUse:Bash hook error: X" [V-run]. Alternatives [V-doc]: exit 2 + stderr reason (blocks even if JSON says allow); permissionDecision also allow|ask|defer; updatedInput rewrites input.
PermissionRequest decide [S-doc]: {"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"allow|deny","message":"..."}}} (exit 2 not honoured).
STOP injects & continues [V-run]: Stop hook printing {"decision":"block","reason":"Also reply FOREMAN-CONTINUE"} -> agent continued, Stop fired again with stop_hook_active:true, last_assistant_message="FOREMAN-CONTINUE". Also exit 2+stderr [V-doc]. Loop guard = stop_hook_active.
Context injection [S-doc]: UserPromptSubmit/SessionStart/PostToolUse hookSpecificOutput.additionalContext (plain stdout also added for UserPromptSubmit/SessionStart). {"continue":false,"stopReason":".."} halts.
Per-turn: Stop = every turn end; UserPromptSubmit = every prompt [V-run]. IDLE/WAITING detection: Stop (turn done, idle); Notification matcher permission_prompt (fires ~6s after prompt shown) / idle_prompt (60s+ idle); PermissionRequest fires when a dialog appears. Non-hook alt [V-file]: ~/.claude/sessions/<pid>.json {pid,sessionId,cwd,startedAt,procStart,version,kind,entrypoint,name,status,statusUpdatedAt,updatedAt,messagingSocketPath}; status values seen: idle|busy|shell (no "waiting" seen; undocumented, may change).
Context window: (a) statusline stdin context_window{context_window_size,used_percentage,remaining_percentage,total_input_tokens,total_output_tokens,current_usage{input_tokens,output_tokens,cache_creation_input_tokens,cache_read_input_tokens}} + exceeds_200k_tokens [V-doc; used by ~/.claude/statusline-command.sh]; used_percentage excludes output tokens, fields may be null early. (b) transcript JSONL last main-chain assistant message .message.usage {input_tokens,cache_creation_input_tokens,cache_read_input_tokens,output_tokens}; ctx = sum [V-file: docs/prior-art/shopping-list-orchestrator/ctx_monitor.py]. (c) `claude -p --output-format json` -> modelUsage.<model>.contextWindow (1000000 for haiku-5-5) [V-run].
Limits (statusline stdin only): rate_limits{five_hour{used_percentage 0-100,resets_at epoch SECONDS},seven_day{same},spend_limit{used_percentage,resets_at,used_usd,limit_usd,period}} [V-doc]; present only for claude.ai plans and only after first API response. Local snapshot ~/.cache/agent-limits/claude.json = {"at":epoch,"rate_limits":{"five_hour":{"used_percentage":8,"resets_at":1793530800},"seven_day":{...}},"model":"claude-opus-5-5"} [V-file]. Other statusline keys [V-doc]: cwd,session_id,session_name,prompt_id,transcript_path,model{id,display_name},workspace{current_dir,project_dir,...},version,output_style{name},cost{total_cost_usd,total_duration_ms,total_api_duration_ms,total_lines_added,total_lines_removed},effort{level},thinking{enabled},prompt_cache{...},fast_mode,vim,agent,pr,worktree. Runs at session start, after each assistant message, /compact, permission-mode change, refreshInterval timer; debounced 300ms; may go quiet while idle.
Statusline config: ~/.claude/settings.json key statusLine {"type":"command","command":"bash ~/.claude/statusline-command.sh","padding"?,"refreshInterval"?} [V-file/V-doc]. Wrapper: read stdin once, tee to file, pipe to original script. (~/.claude/statusline-command.sh already snapshots to ~/.cache/agent-limits/ [V-file]; fancy-statusline plugin overwrote earlier patch per memory.) Not run in -p mode.

## 2. ANTIGRAVITY CLI (agy)
Hooks config [V-file/V-run]: user ~/.gemini/config/hooks.json (shared TUI+backend; NOT ~/.gemini/antigravity-cli/settings.json, which has no hooks key); workspace <ws>/.agents/hooks.json (needs trusted workspace; loaded in -p with --settings '{"trustedWorkspaces":[..]}'); plugins' hooks.json; `agy -p "/hooks"` lists active hooks. NOT Claude layout: top-level key = hook NAME, then event:
 {"name":{"enabled":true,"SessionStart":[{"type":"command","command":"..","timeout":10}],"PreInvocation":[flat],"PostInvocation":[flat],"Stop":[flat],"PreToolUse":[{"matcher":"run_command|*","hooks":[{"type":"command","command":".."}]}],"PostToolUse":[grouped like PreToolUse]}}
 Flat events = list of handler objects (using matcher form there gives "command hook must specify 'command'" and the whole hook name is dropped) [V-run]. Only these 6 events exist (SessionStart, PreToolUse, PostToolUse, PreInvocation, PostInvocation, Stop) [V-doc: help text embedded in agy binary]. Unknown events (UserPromptSubmit, Notification, PermissionRequest, SessionEnd, PreCompact) silently ignored [V-run]. timeout seconds, default 30; cwd = dir of hooks.json; run via sh -c; synchronous; only type command. Event name is NOT in the payload (Orca wrapper passes env ORCA_ANTIGRAVITY_EVENT; pass it as an argument yourself).
stdin payloads, camelCase [V-run]: common conversationId, workspacePaths[], transcriptPath (~/.gemini/antigravity-cli/brain/<conv>/.system_generated/logs/transcript_full.jsonl), artifactDirectoryPath, modelName, lastUserInput. SessionStart: common only. PreInvocation/PostInvocation: +invocationNum, initialNumSteps (fire for every model call in a turn). PreToolUse: +stepIdx, toolCall{name:"run_command",args{CommandLine,...}}. PostToolUse: +toolCall, stepIdx, error? (documented; DID NOT FIRE in my -p run with matcher "*": UNVERIFIED). Stop: +executionNum, terminationReason (NO_TOOL_CALL|MAX_INVOCATIONS|MAX_FORCED_INVOCATIONS|MAX_TOKEN_BUDGET_EXCEEDED|ERROR|USER_CANCELED|TERMINAL_STEP_TYPE|TERMINAL_CUSTOM_HOOK|INJECTED_RESPONSE), error, fullyIdle (bool, no bg tasks).
Output stdout JSON REQUIRED ({} when nothing) [V-doc]. BLOCK tool: PreToolUse -> {"decision":"allow|deny|ask|force_ask","reason":"..","permissionOverrides":[..],"overwrite":{arg:val}} (deny = hard block; overwrite shallow-merges tool args). [V-run E4, interactive TUI] a PreToolUse hook answering `{}` makes agy deny the call ("tool call denied by pre-tool hook: ", empty reason) even though nothing blocked it; `{"decision":"ask"}` hands it to agy's normal permission flow and the call runs; so foreman answers `ask` when it does not deny. A deny with a reason is shown to the model as is. Orca's own wrapper emits {"decision":"ask"} for PreToolUse and {"decision":""} for Stop [V-file].
Stop continue: {"decision":"continue","reason":"text"} -> re-enters loop, reason injected as system message; anything else lets it stop; consecutive-continuation cap exists (changelog 1.x "after a configurable number of consecutive continuations the hook can no longer block") [V-doc]. PostInvocation {"terminationBehavior":"force_continue"|"terminate","injectSteps":[..]}. PreInvocation {"injectSteps":[{"userMessage":"..."}|{"ephemeralMessage":".."}|{"systemMessage":{"systemMessage":".."}}|{"errorMessage":{"userMessage":".."}}],"terminationBehavior":"terminate"} (block prompt before model call). SessionStart {"injectSteps":[...]}.
Per-turn/idle: Stop fires at end of each turn with fullyIdle (verified, fired once in -p). No Notification/PermissionRequest event => "waiting for permission" NOT detectable via hooks; PreToolUse fires before the permission decision. Herdr uses screen detection for agy ("Lifecycle state comes from Herdr's screen detection", ~/.gemini/config/hooks/herdr-agent-state.sh). Env in hook process [V-run]: ANTIGRAVITY_CONVERSATION_ID plus inherited ORCA_*/HERDR_*.
Context window: ONLY via statusline stdin context_window{total_input_tokens,total_output_tokens,context_window_size,used_percentage,remaining_percentage,current_usage{input_tokens,output_tokens,cache_creation_input_tokens,cache_read_input_tokens}} [V-file: ~/.cache/agent-limits/agy-ctx/term_<uuid>.json, e.g. size 1048576, used_percentage 1.857]. Not in hook payload.
Limits (statusline stdin) [V-file: ~/.cache/agent-limits/agy.json]: {"quota":{"<key>":{"remaining_fraction":0..1,"reset_time":"2026-11-05T17:54:33Z","reset_in_seconds":17965}},"plan_tier":"Google AI Pro","model":..}. Keys seen: gemini-5h, gemini-weekly, 3p-5h, 3p-weekly, gemini-argon-wtu-weekly. Unit: fraction REMAINING (used% = 100*(1-remaining_fraction)); reset_in_seconds relative to snapshot time. quota can be null (known agy-statusbar bug per .remember). Other keys: model{id,display_name,effort}, cycle_mode, cwd, workspace.current_dir, conversation_title, cost.
Statusline config: ~/.gemini/antigravity-cli/settings.json key statusLine {"type":"command","command":"bash ~/.gemini/antigravity-cli/statusline-command.sh","enabled":true}; `--settings <json|file>` overrides per session. NOT run in -p mode. That script already tees to ~/.cache/agent-limits/agy.json and agy-ctx/$ORCA_TERMINAL_HANDLE.json [V-file].
Print mode: `agy -p ... --output-format json|stream-json` (+finished_reason), `--model gemini-3.8-flash-low` (cheapest), --dangerously-skip-permissions.

## 3. CODEX CLI (codex-cli 0.146.1) — NOT LOGGED IN here (401), so NO runtime verification; all UNVERIFIED unless stated
Hooks config: ~/.codex/hooks.json, ~/.codex/config.toml inline [hooks], <repo>/.codex/hooks.json [S-doc]; feature [features] hooks=true (stable) [V-file: ~/.codex/config.toml, `codex features list`]. Trust required: each hook hash stored in config.toml [hooks.state."<path>:<event>:<i>:<j>"] enabled=true, trusted_hash=sha256:.. [V-file]; untrusted/changed hooks are skipped until reviewed via /hooks (`--dangerously-bypass-hook-trust` per run) [S-doc]. My /tmp project hooks.json could not be tested (no auth).
Register (same layout as Claude) [V-file ~/.codex/hooks.json]: {"hooks":{"Stop":[{"hooks":[{"type":"command","command":"..","timeout":10}]}],"PreToolUse":[{"matcher":"..","hooks":[..]}]}}. Events [S-doc/V-file binary]: SessionStart SessionEnd UserPromptSubmit PreToolUse PermissionRequest PostToolUse PreModelRequest PostModelRequest PreCompact PostCompact SubagentStart SubagentStop Stop Interrupt. No Notification event. Default timeout 600s; SessionEnd/Interrupt ~1-3s.
stdin [S-doc]: session_id, transcript_path, cwd, hook_event_name, model, permission_mode; turn_id on turn events; SessionStart.source (startup|resume|clear|compact|fork); UserPromptSubmit.prompt; PreToolUse/PostToolUse tool_name, tool_input, tool_use_id (+tool_response); PermissionRequest tool_name, tool_input{description}; Stop stop_hook_active, last_assistant_message; SubagentStop +agent_id,agent_type,agent_transcript_path; PostModelRequest +status,error,token_usage. [V-file] Herdr script reads hook_event_name, session_id, transcript_path, source on SessionStart.
Block tool [S-doc]: exit 2 + stderr, or {"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":".."}} (ask NOT supported; binary: "PreToolUse hook returned unsupported continue:false"). PermissionRequest: {"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"allow|deny","message":".."}}}; "interrupt:true" unsupported. Stop continue: {"decision":"block","reason":".."} (reason = new continuation prompt) or exit 2; additionalContext via hookSpecificOutput for SessionStart/UserPromptSubmit/PreToolUse/PostToolUse/SubagentStart.
Per-turn/waiting: Stop = turn end; PermissionRequest hook fires when an approval is needed (usable as "waiting for permission"); UserPromptSubmit = turn start. Env: CODEX_THREAD_ID is set for child processes (binary strings + herdr script compares it to session_id) [V-file]; CODEX_HOME.
Legacy notify: config.toml `notify=["cmd",..]` receives JSON argv with keys type "agent-turn-complete", thread-id, turn-id, cwd, client, input-messages, last-assistant-message (kebab-case) [V-file: binary strings; not run]; ignored in project-local config [S-doc].
Context window: no external statusline command. tui.status_line / tui.terminal_title take built-in item ids [V-file binary]: project-name current-dir run-state thread-title git-branch context-remaining context-used five-hour-limit weekly-limit codex-version used-tokens total-input-tokens total-output-tokens thread-id fast-mode model-with-reasoning reasoning task-progress. Machine-readable: rollout JSONL (~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl; dir absent here) TokenCountEvent{info{total_token_usage,..,model_context_window},rate_limits}; RateLimitWindow{used_percent,window_minutes,resets_at}; `codex app-server`: account/rateLimits/read, account/rateLimits/updated, thread/tokenUsage/updated [S-doc]. Exact JSON nesting and resets_at unit (epoch s vs relative): UNVERIFIED. Subscription limits => primary/secondary windows (5h/weekly) in rate_limits.

## 4. ENVIRONMENT VARIABLE NAMES (from /proc/<pid>/environ of live claude/agy processes)
Orca terminal (claude pid, agy pid) [V-file]: ORCA_TERMINAL_HANDLE (=term_<uuid>), ORCA_PANE_KEY (<tabuuid>:<paneuuid>), ORCA_TAB_ID, ORCA_WORKTREE_ID (<uuid>::<path>), ORCA_WORKSPACE_ID, ORCA_AGENT_PANE, ORCA_AGENT_LAUNCH, ORCA_AGENT_LAUNCH_TOKEN, ORCA_AGENT_HOOK_ENDPOINT (=~/.config/orca/agent-hooks/endpoint.env, holds PORT/TOKEN/ENV/VERSION/TRANSPORT), ORCA_AGENT_HOOK_{ENV,PORT,TOKEN,TRANSPORT,VERSION,OPENCODE_TUI}, ORCA_CLI_COMMAND, ORCA_CLI_BIN_DIR, ORCA_VERSION, ORCA_APP_VERSION, ORCA_USER_DATA(_PATH), ORCA_RUNTIME_SOURCE_{ID,INCARNATION,PROFILE_PATH}, ORCA_CLAUDE_PROFILE_POINTER, ORCA_HISTFILE, ORCA_IMAGE_PROTOCOL, ORCA_NODE_OPTIONS, ORCA_NODE_REPL_EXTERNAL_MODULE, ORCA_OPENCODE_AGENT, ORCA_SPEECH_RECOGNIZER_DIR. Orca's per-agent hook scripts: ~/.orca/agent-hooks/{claude,codex,antigravity,gemini}-hook.sh POST payload to http://127.0.0.1:$ORCA_AGENT_HOOK_PORT/hook/<agent> with header X-Orca-Agent-Hook-Token and base64 meta of pane key/tab/worktree.
Herdr pane [V-file]: HERDR_ENV=1, HERDR_PANE_ID (e.g. wE:p4), HERDR_TAB_ID (wE:t3), HERDR_WORKSPACE_ID (wE), HERDR_SOCKET_PATH (~/.config/herdr/herdr.sock), HERDR_BIN_PATH. Herdr hook scripts only report session id via socket method pane.report_agent_session; run-state comes from screen detection.
Agent-set: claude: CLAUDE_CODE_ENTRYPOINT, CLAUDE_CODE_SESSION_ID, CLAUDECODE, CLAUDE_PID, CLAUDE_EFFORT, CLAUDE_CODE_EXECPATH (seen in child env) ; agy: ANTIGRAVITY_CONVERSATION_ID; codex: CODEX_THREAD_ID (UNVERIFIED at runtime). Nested Orca-terminal + Herdr is possible (both sets present on same pid).
Cleanup: /tmp/foreman-research-{agy,claude,codex} removed; no user config modified.

# Transports

## 1. Orca Transport (`~/.config/orca/cli/bin/orca`)
Runtime status: operational (v1.4.239, verified via `orca status --json`).

- **List live terminals**: `orca terminal list --json` (options: `--worktree <selector>`, `--include-visual-layouts`).
  - Key fields in `result.terminals[]`: `handle` (e.g. `"term_..."`), `title`, `worktreePath` (cwd), `agentIdentity` (`"claude"`, `"antigravity"`), `connected`, `writable`, `lastOutputAt`, `preview`, `tabId`, `leafId`.
  - Source: `orca terminal list --json`, `orca terminal list --help`.
- **Launch agent**: `orca agent launch --agent <claude|antigravity|codex> --worktree <selector> --model <model> --effort <effort> --prompt "<prompt>" [--surface chat|terminal] [--focus] --json`
  (For new checkout: `orca worktree create --name <name> --agent <id> --model <model> --effort <effort> --prompt "<prompt>" --json`).
  - Key fields (Orca 1.4.239, verified live): the launch handle is `result.outcome.handle` (e.g. `"term_..."`), next to `result.outcome.kind` (`"structured"` chat or `"terminal"`) and `result.outcome.paneKey`; `receipt.detail` and `prompt.outcome` (`"journaled"` or `"handed-to-terminal"`) are siblings of `outcome` under `result`, not under a `result.launch` wrapper. `OrcaTransport.launch` reads `result.outcome.handle` first and keeps `result.launch.*` / `result.terminal.handle` only as fallbacks for other versions. The agent starts working inside the launch call, so its first hooks can arrive before `foreman open` has recorded the session (see the pending-orchestrator marker, D54).
  - Source: `orca skills get orca-cli`, `orca agent launch --help`, `orca agent-context --json`.
- **Rename tab**: `orca terminal rename --terminal <handle> --title "<new-title>" --json`
  - Key fields in `result.rename`: `handle`, `tabId`, `title`.
  - Source: verified on scratch terminal `term_<uuid>`.
- **Read bounded output**: `orca terminal read --terminal <handle> [--cursor <n>] [--limit <n>] [--screen] --json`
  - Key fields in `result.terminal`: `handle`, `status` (`"running"`), `tail` (array of strings), `oldestCursor`, `nextCursor`, `latestCursor`, `returnedLineCount`, `source` (`"stream"` | `"screen"`).
  - Source: verified on scratch terminal via `orca terminal read`.
- **Send text and submit (Enter)**: `orca terminal send --terminal <handle> --text "<text>" --enter [--wait-submit <sec>] --json`
  - Key fields in `result.send`: `handle`, `accepted` (bool), `bytesWritten`, `prompt.requestId`, `prompt.stages` (`["input_accepted"]`).
  - Source: verified on scratch terminal via `orca terminal send`.
- **Wait for idle / exit**: `orca terminal wait --terminal <handle> --for tui-idle [--timeout-ms <ms>] --json` (or `--for exit`).
  - Output on timeout: exit code 1; `{"ok": false, "error": {"code": "timeout", "message": "timeout"}}`.
  - Source: verified on scratch terminal via `orca terminal wait`.
- **Close terminal**: `orca terminal close --terminal <handle> [--tab] --json`
  - Key fields in `result.close`: `handle`, `tabId`, `closeMode` (`"tab"`), `ptyKilled`.
  - Source: verified on scratch terminal via `orca terminal close`.
- **Detect dead terminal handle**: Sending to a dead handle (`orca terminal send --terminal <dead_handle> ... --json`) fails with exit code 1:
  `{"ok": false, "error": {"code": "terminal_not_writable", "message": "terminal_not_writable Terminal prompt request ID: ..."}}`.
  `orca terminal show --terminal <dead_handle> --json` reports `connected: false`, `writable: false`, `exitCause: {"kind": "operator_close"}`.
  - Source: verified on closed scratch terminal.
- **Agent status (busy/idle/waiting) in Orca**:
  - `terminal list` and `terminal show` do **NOT** expose agent lifecycle state (`agentWait` is null; only process fields `connected`, `writable`, `orphaned` are present).
  - `orca worktree ps --json` **DOES** expose per-agent status: `result.worktrees[].agents[]` maps `paneKey` (`<tabId>:<leafId>`) to `state: "working" | "done"`, `mainAgent.state`, `toolName`, `toolInput`, and `programStatus.state`.
  - Source: `orca terminal show`, `orca terminal list`, `orca worktree ps --json`.

## 2. Herdr Transport (`~/.local/bin/herdr`)
Runtime status: operational (v0.9.5, socket `~/.config/herdr/herdr.sock`, verified via `herdr status --json`).

- **List live terminals/panes**: `herdr pane list` (or `herdr agent list`).
  - Key fields in `result.panes[]`: `pane_id` (e.g. `"w8:p1"`), `tab_id`, `workspace_id`, `terminal_id`, `terminal_title`, `cwd`, `foreground_cwd`, `agent` (`"claude"`, `"agy"`, `"opencode"`), `agent_status` (`"idle"`, `"working"`, `"blocked"`, `"done"`, `"unknown"`), `label`.
  - Source: `herdr pane list`, `herdr agent list`.
- **Launch agent**: Three-step deterministic sequence:
  1. Create pane/tab: `herdr pane split --current --direction right --cwd <dir> --no-focus` (or `herdr tab create --workspace <id> --cwd <dir> --label <name> --no-focus`) -> returns `result.pane.pane_id` (e.g. `"wH:p1"`).
  2. Start agent process: `herdr agent start <NAME> --kind <claude|agy|codex> --pane <pane_id> [--timeout <ms>] -- [--model <m>] [--effort <e>]` -> launches agent CLI in pane and registers identity.
  3. Deliver prompt: `herdr agent prompt <NAME|pane_id> "<PROMPT>" --wait` (waits for settled states `idle`, `done`, `blocked`).
  - Source: `herdr --skill`, `herdr agent start --help`, `herdr agent prompt --help`.
- **Rename tab**: `herdr tab rename <tab_id> "<label>"`
  - Key fields in `result.tab`: `tab_id`, `workspace_id`, `label`, `pane_count`.
  - Source: verified on scratch workspace `wH` via `herdr tab rename`.
- **Read bounded output**: `herdr pane read <pane_id> --lines <n> --source recent-unwrapped` (or `herdr agent read <NAME>`).
  - Returns plain text or ANSI output. For structured wait: `herdr pane wait-output <--match <str>|--regex <pattern>> <pane_id>` returns `result.read.text`.
  - Source: verified on scratch workspace `wH` via `herdr pane read` and `wait-output`.
- **Send text and submit (Enter)**:
  - Raw pane: `herdr pane run <pane_id> "<command>"` (queues text + Enter atomically) or `herdr pane send-text <pane_id> "<text>"` + `herdr pane send-keys <pane_id> enter`.
  - Agent pane: `herdr agent prompt <TARGET> "<text>"` (guards against blocked/working state).
  - Source: verified on scratch pane `wH:p1` via `herdr pane run`.
- **Wait for idle**: `herdr agent wait <TARGET> [--until <idle|done|blocked|working>] [--timeout <ms>]` (matches settled `idle`, `done`, `blocked`).
  - Pane level: `herdr pane wait-output <--match <str>|--regex <pattern>> <pane_id> [--timeout <ms>]`.
  - Source: `herdr agent wait --help`, verified on scratch pane.
- **Close terminal/pane**: `herdr pane close <pane_id>` (or `herdr workspace close <workspace_id>`).
  - Returns: `{"id": "cli:pane:close", "result": {"type": "ok"}}`.
  - Source: verified on scratch pane `wH:p1`.
- **Detect dead terminal handle**: Sending to a dead handle (`herdr pane run <dead_pane_id> "test"`) fails with exit code 1:
  `{"error": {"code": "pane_not_found", "message": "pane <id> not found"}, "id": "cli:request"}`.
  For `agent wait`: `{"error": {"code": "agent_not_found", "message": "agent target <id> not found"}, "id": "cli:agent:wait"}`.
  - Source: verified on closed scratch pane `wH:p1`.
- **Agent status (busy/idle/waiting) in Herdr**:
  - **YES directly exposed**: `agent_status` is returned per pane and agent in `herdr pane list` and `herdr agent list`, tracking `"idle"`, `"working"`, `"blocked"` (waiting for approval/question), `"done"`, or `"unknown"`.
  - Source: `herdr pane list`, `herdr agent list`.

## 3. Shell-Level Claude Code Inter-Session Messaging
- **`claude` CLI Subcommand**: **None exists**. `claude --help` lists subcommands (`agents`, `attach`, `doctor`, `logs`, `mcp`, `plugin`, `rm`, `stop`, etc.) with no CLI `send` or `message` command.
- **In-Session Agent Tool**: Built-in `SendMessage` tool (`to`, `message`, optional `notify_when_idle`). Supports messaging named local sessions, subagents, teammates, or remote sessions. (Source: string analysis of `~/.local/bin/claude` chunk bytecode).
- **Session Registry & Discovery**:
  - `claude agents --json` lists active sessions: `[{"pid": ..., "sessionId": "...", "name": "...", "cwd": "...", "status": "idle"|"busy"}]`.
  - `~/.claude/sessions/<pid>.json` publishes metadata: `pid`, `sessionId`, `name`, `status`, and `"messagingSocketPath": "/run/user/<uid>/cc-socks/<pid>.sock"`.
  - `~/.claude/sessions/<pid>.<hash>.key` publishes authentication: `{"peerToken": "<32-char-hex-token>", ...}`.
- **Shell-Level Socket Injection Protocol**:
  Claude Code sessions bind a Unix domain socket at `$XDG_RUNTIME_DIR/cc-socks/<pid>.sock` (e.g. `$XDG_RUNTIME_DIR/cc-socks/<pid>.sock`; fallback `/tmp/cc-socks-<uid>/<pid>.sock`).
  An external shell script sends messages directly by connecting to the socket with the peer token (verified via Claude Code binary embedded diagnostic string):
  ```bash
  {
    echo '{"type":"auth","token":"'"$PEER_TOKEN"'"}'
    echo '{"type":"user","message":{"role":"user","content":"<PROMPT>"}}'
  } | socat - UNIX-CONNECT:"$XDG_RUNTIME_DIR/cc-socks/<PID>.sock"
  # Alternatively: nc -N -U "$XDG_RUNTIME_DIR/cc-socks/<PID>.sock"
  ```
  - Sockets under `~/.claude`: Only `~/.claude/remote/run/<id>/rpc.sock` exists (for daemon/remote control), not for inter-session chat.
  - Source: `~/.local/bin/claude` binary strings, `~/.claude/sessions/*.json`, `$XDG_RUNTIME_DIR/cc-socks/`.

