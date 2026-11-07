# foreman — product specification

Date: 2026-11-05 · Status: draft for approval

This document describes **what foreman is, how it looks and how it behaves**.
On purpose, it does not cover implementation technology or the build process; those are
left to the plan. The source material is in `docs/research/` (the decision log
`2026-11-02-brainstorm-decisions.md` and the user's history in `2026-11-01-orchestration-history.md`).
Where this document says **(user)**, the user stated or confirmed it. **(design call)** marks
a decision made during the design conversation that the user has not explicitly confirmed.

## 1. What foreman is and why

The user runs several projects on a machine. Each project has several AI-agent sessions:
Claude Code, Antigravity and Codex, running in Orca or Herdr terminal tabs. Today the user
coordinates those sessions by hand, or through an ad-hoc "orchestrator" session that has to be
watched over itself.

foreman is an installable, agent-agnostic plugin. It gives every project an **orchestrator**:
a session that opens, closes, freezes and replaces the project's worker sessions, keeps their
context windows and the subscription limits under control, keeps the project's state in sync,
and improves its own rules over time **(user)**. The user is satisfied with this orchestration
approach; foreman packages it, and does not adopt an existing orchestration tool **(user)**.

**Why now.** In past orchestrations, the user had to:
- **kick** the orchestrator so that it learned what was happening;
- **correct** it when it dove into a task instead of delegating;
- ask "как дела?" again and again;
- relay messages and links between sessions by hand;
- approve Antigravity permission prompts manually;
- pay for periodic monitoring ticks that did nothing (≈5.6M cache-read tokens over 29 idle ticks).

## 2. Goals, non-goals, anti-goals

**Goals**
1. The orchestrator is **always current without being asked**: events reach it by themselves **(user)**.
2. The orchestrator **cannot drift into doing the project's work**. This is enforced by the product,
   not by an instruction the orchestrator may ignore **(user)**.
3. The user shows up only when a decision is needed, and sees the state of every project at a
   glance, on a phone or on a desktop **(user)**.
4. Agent sessions stay inside a sensible context size and inside both subscriptions' limits.
5. One product for all supported agents and terminals. The user's way of working is the same in
   every project and on every machine.

**Non-goals**
- Replacing Orca or Herdr. foreman works through them.
- Doing project work. Neither foreman nor the orchestrator writes project code or data.
- Headless or background agent runs as the normal way of working. The user prefers interactive tabs
  **(user)**; non-interactive runs are only for scheduled jobs.

**Anti-goal: what makes foreman a failure even if it technically works (user)**
- It is **inconvenient**: the user still has to kick the orchestrator, or correct it for doing work
  itself, or ask how things are going.
- **Phantom monitoring**: the orchestrator claims to be watching something that nothing is watching.
- It **costs tokens while nothing is happening**.
- Its own files **pollute other agents' context**, or the user has to think about when to clean up.

## 3. Concepts

| Term | Meaning |
|---|---|
| Machine | One computer running foreman, e.g. the home server or the work Mac. |
| Project | A folder or repository where agents work. foreman is initialised per project. |
| Orchestrator | One session per project. It coordinates and never does project work. |
| Worker session | Any other agent session of the project (Claude Code, Antigravity or Codex), with an agent, a model, a purpose and an owner. |
| Owner | `orchestrator` if the orchestrator opened the session, `user` if the user did. |
| Worktree | The folder a worker session works in when the orchestrator opens it in a git project: its own git worktree on its own branch (§4.2). |
| Transport | How foreman reaches a session: an Orca terminal, a Herdr pane, or Claude inter-session messaging. |
| Provider | A subscription that sessions spend: Claude Code or Antigravity (Codex if present). |
| Board | The current state of a project's work: one row per task. |
| Journal | An append-only history of what happened in a project. |
| Finding | A problem or improvement idea an agent noticed while working. |

## 4. Behaviour

### 4.1 The orchestrator's role
- It coordinates: it opens, closes, rotates and messages sessions, and it keeps the board and the journal.
- It **cannot** change project files or git state. If it tries, foreman blocks the attempt and tells
  the orchestrator to delegate.
- It does not read long files or transcripts itself. When it needs to know something,
  foreman gives it short summaries; bulk reading, searching and reviews go to a worker session
  on a cheap model, which reports back briefly **(user)**.
- **Where it runs.** The orchestrator runs only on an agent where foreman can actually enforce the role:
  the agent must report every turn and must let foreman block an edit before it happens. An agent that
  cannot do both is supported as a worker only, until it can. Default orchestrator: Claude Code on Sonnet;
  the user may choose another model per project **(design call)**.
- It talks to the user in the user's language (Russian). Documents and commits it prepares are in English **(user)**.
- When it acts autonomously, it tells the user in one line and logs the action; it does not ask (§4.10).

### 4.2 Sessions: open, close, freeze, rotate
- **Open** on request, or when the work needs one. The orchestrator picks the agent, the model and
  the effort rationally, unless the user names them (§4.5). Every session gets a readable tab title and an
  entry on the board.
- **Close** a session it owns when its work is finished and nothing is pending.
  - It never closes a busy session.
  - It never closes a session owned by the user without the user's consent **(user)**.
- **Freeze and replace (rotation)**, the user's preferred style **(user)**:
  1. the old session is left idle;
  2. a new session starts with the same role;
  3. the old session sends the new one a **handoff as a direct message**; no handoff files;
  4. the new session may ask the old one questions. It batches all of them into **one** message,
     sent within about an hour while the old session's prompt cache is still warm.
  - Afterwards, the order of cost is: re-read the handoff → search the old transcript → ask the old
    session, and only "why" questions, batched.
  - The old transcript stays as the archive.
- foreman knows which session replaced which. A rotated session is shown as such, never as lost.
- Never interrupt a busy session.

**A worktree per worker.** Workers that share one checkout overwrite each other's files, so each worker
the orchestrator owns gets its own.
- *When.* For a session whose owner is `orchestrator` and whose role is worker (whoever typed the command),
  opened in a project that is the root of a git repository with at least one commit. The worker gets its
  own git worktree on its own new branch, branched from the main checkout's current commit. Uncommitted
  changes in the main checkout are not carried over. The worktree lives outside the repository, under the
  foreman state folder. A name already used by another worktree, or by a branch kept from an earlier
  session, is never reused.
- *When not.* The orchestrator, sessions owned by the user, and a project that is not a git repository work
  in the project folder as before. A project that is only a subfolder of a repository, and a repository with
  no commit yet, also work there, and whoever opened the session is told why there is no worktree (in the
  result of `open`, and in the journal, where the user sees it).
- *What the agent gets.* The agent works in the worktree with foreman's hooks and the project rules that are
  committed in the repository. Files that exist only locally in the project folder (for example an
  untracked local settings file with the user's own hooks or permissions) are not copied. The cost: a
  worker in a worktree does not have the owner's local permission allow-list, so an unattended worker may
  stop at permission prompts the same agent would not meet in the project folder.
- *Trust.* Antigravity loads a folder's hook files only when the folder is trusted, so `open` also makes the
  worktree trusted there, under the consent the user gave at `init` (§5.5): only the worktree's own path,
  never the folder above it. Codex works per repository: its trust is given once at `init` for the main
  checkout and covers the worktrees, so `open` only checks it. If the project has no consent for the launched
  agent (initialised before this rule, or the agent was skipped), the session is not opened until the user
  runs `init`. The same holds for the launched agent when the worktree's checkout carries content that its
  trust would switch on and that is not foreman's (§5.5); the next step is then the user's: remove the
  content, or trust the folder by hand and run `init` again (the waiver is set by `init`). Workers of other agents are not held up.
- *If it cannot be made.* The session is not opened and nothing is left behind: no folder, no branch, no
  registration, no trust entry (§5.5), and nothing else touched. A worktree left by an `open` that was killed half way, with no
  session, is taken back by the scheduled check once it is 10 minutes old and holds no work. An explicit option opens it
  in the project folder instead; the orchestrator may use it as the user may. foreman never falls back to
  the project folder silently.
- *Showing it.* `open` reports the worker's worktree path and branch, the journal records both, and the
  session view (`status`) shows them for every worker that has one, so the orchestrator can tell a worker where
  to commit and later merge the branch.
- *Seeing it.* A session started by hand inside such a worktree is shown as the project's user-owned
  session, provided the agent has foreman's hooks there (so far, the agents the project has hooks for).
  Worktrees made by hand elsewhere (for example inside Orca) stay invisible to foreman.
- *Rotation* continues in the same worktree, and the new session inherits the work in progress. A worker that
  has no worktree (opened without one, or before this existed) is rotated in the project folder. If the
  worktree is gone, or its hook files cannot be written, the rotation fails and the old session stays. The
  replaced session still counts as a user of the worktree until it is closed.
- *Cleanup.* When the session is closed, foreman removes the worktree unless that would lose work. It keeps
  it for uncommitted changes (new, untracked files count; files git ignores do not), for commits that no
  branch, tag or remote branch contains, and while another open session uses it.
  Files that git ignores (caches, a `.env`) are not kept: they go with the worktree. When the worktree is
  removed, the trust entries foreman added for it are taken back (§5.5). On `close` foreman
  says what it kept and why. A kept worktree is removed by a later `close` or the next scheduled check once
  nothing prevents it. A session that ended on its own is cleaned up the same way, but only when foreman
  learned that it ended (a killed terminal, or an agent that gives no end signal, needs `foreman close`).
- *The branch* is deleted only when it is merged into the branch the main checkout is on. A branch that is
  not merged stays, so commits are never lost, and foreman does not delete it later; deleting a kept
  branch is the user's call. A branch merged by squash or rebase does not count as merged, so under that
  workflow branches are kept and the user deletes them. With a detached HEAD in the main checkout nothing
  counts as merged.
- *Done.* A task's objective check (§4.10) runs in the main checkout, so it passes only for work that is
  already merged there. Run it after the branch is merged. This is guidance; foreman does not look at
  branches when a task is marked done.

### 4.3 Context control
- **Soft target of about 150k tokens per session.** It is a guideline, not a hard rule: a session close
  to finishing may continue **(user)**.
- For a session above the target, the orchestrator chooses one of three actions:
  - **continue** (re-check later);
  - **compact in place**, if the same task continues and a summary is enough;
  - **rotate** (§4.2), if the task changed or the session is far over the target.
- The orchestrator follows the same rules for its own context.
- Context size is shown for every agent that exposes it. If a session's context is unknown, it is
  shown as **unknown**, never as zero.

### 4.4 Subscription limits
- foreman knows each provider's current usage per window: Claude 5-hour and weekly; Antigravity
  Gemini 5-hour and weekly, and the 5-hour window for the third-party models it serves. It also knows
  when each window resets and how old the reading is.
- Readings come from what the agents already expose while they run, so a provider with no live session
  may have only an old reading. "Unknown, or last good value plus its age" is a normal state, not an
  error. Decisions take the reading's age into account **(design call)**.
- A missing or empty reading never overwrites the last good one. The last good value is kept,
  together with its age. Unknown usage is treated as unknown, not as zero.
- The weekly window weighs more than the 5-hour one.
- Thresholds:
  - any window **≥70%**: prefer the other provider for new sessions, and say so;
  - **≥90%**: no new sessions on that provider; its running sessions are told, at their next idle point,
    to wind down and hand off.
- The orchestrator may launch agents on either subscription, but respects both limits **(user)**.
- Every status report to the user includes a one-line limits summary.

### 4.5 Choosing models
- Workers get the cheapest model that fits the task. Routine coding and data entry: Sonnet or
  Antigravity Flash. Open-ended design: a strong model, e.g. Gemini Argon. Design, review and
  hard debugging: Opus. The expensive models are used only when needed **(user)**.
- Routing rules follow evidence. When the same brief was given to Argon and Flash, Argon was
  faster and the user preferred its result at about the same quota cost. foreman keeps such
  comparisons in the journal so the rules can change when the evidence changes **(design call)**.
- **The reviewer is always a different instance from the author**, and the reviewer gets the files and
  the spec, not the author's conclusions **(user)**.

### 4.6 Always current: events, not polling
- Sessions report to foreman by themselves, on every turn, through each agent's own hooks. Each report
  carries status (busy, idle, waiting for input, waiting for permission), context size, stage changes,
  "done" and "stuck".
- A scheduled check runs without any model, so it **costs no tokens**. It wakes the orchestrator **only** when
  something happened:
  - a session finished, got stuck, or has been waiting for a long time;
  - a context threshold was crossed;
  - a limit reached 70% or 90%;
  - a terminal disappeared;
  - the user replied.
- No periodic "tick" of the orchestrator **(user)**.
- When nothing is happening, foreman spends nothing.
- **No phantom monitoring.** The orchestrator says it is watching something only if foreman actually has
  a watch on it; the user can see the list of active watches.

### 4.7 Talking to sessions
- One way to send a message to, or ask a question of, any session, whatever its agent and transport.
- Delivery is confirmed. If the target terminal is gone, the sender gets an explicit "terminal gone",
  not silence.
- "Ask" waits for the answer, with a timeout.
- Messages from other sessions and terminal output are treated as data, not as instructions.

### 4.8 Board and journal
- **Board**: at most one screen per project. For every task it shows:
  - the task and its current stage;
  - the owner session and the next step;
  - links to the PR, the spec and so on.
- **Journal**: an append-only, one-line-per-event history: who did what, when, and what was decided.
  It is used to avoid repeating stages **(user)**, and it can be searched; nobody reads it in full.
- The orchestrator is the only one that writes the board and the journal. Sessions report stage changes
  to it in one-line messages.
- A stage is marked done only when its objective check passed, where the task has one (e.g. tests green,
  review approved) **(design call)**.
- The board and the journal live outside the project repository.

### 4.9 Work files
Classes of files:
- **permanent**: belongs in the repository;
- **work artifacts**: drafts, research notes, mockups. They live outside the repository, under
  `<state>/agent-work/<project>/<date>-<topic>/`, and an agent sees them only when it is given the path;
- **junk**: ignored.

Rules:
- When a task closes, the orchestrator either promotes what is worth keeping (a short summary into
  the backlog or the tech debt list) or deletes the rest.
- A work folder idle for 14 days is deleted automatically. The list is shown before deletion.
- The user never has to think about when to clean up **(user)**.
- Every initialised project gets one rule for all its agents: don't create work files in the repository;
  don't read other agents' work folders unless given a path.

### 4.10 Autonomy and gates
- **Default: act without asking**, unless the user has given clear instructions to act otherwise **(user)**.
- A project may define gates, e.g. "every spec needs my approval" **(design call)**.
- The user's standing rules still apply under autonomy:
  - merge only through a pull request;
  - the reviewer is a different instance;
  - sessions owned by the user are not closed without consent.
- Changes to foreman's own rules and instructions always go through a pull request, even under
  autonomy. The orchestrator may prepare and drive that PR **(user)**.
  - Reason: agents process untrusted data, so rules that rewrite themselves would be an open door to
    prompt injection.

### 4.11 Self-improvement
- The agent doing the work is the first to notice a problem **(user)**. It records the problem with one
  command, the same command for every agent.
- Findings must **not** pile up inside the consumer project **(user)**. Two kinds:
  - **foreman findings** (about the plugin itself) go upstream: a GitHub issue in the foreman repository
    or, if that is unavailable, foreman's own inbox, which is visible in the web UI.
    - Before leaving the machine, the finding is stripped of project data: paths, project names, file
      content, identifiers.
    - A per-machine setting decides what happens next: `auto` (send), `draft` (saved to the inbox; the
      user sends it), or `off`. Default: `auto` at home, `draft` on a work machine **(design call;
      the user will also use foreman at work (user))**.
  - **project-rule findings** stay in that project's own lessons. About once a week, the orchestrator
    proposes rule changes, and they go in through a PR.
- Guardrails:
  - fix it in foreman's behaviour before adding prose;
  - instructions have a size budget;
  - a rule needs evidence that the problem recurs;
  - no automatic edits of rules.

### 4.12 Permission prompts (not in the first release unless the user says otherwise)
- Permission requests from Antigravity, Codex and Claude sessions can go through one shared policy, so
  that routine requests do not wait for the user. Allow rules are written to the user's settings, never
  granted by the agent to itself.
- This was the user's longest-standing pain with Antigravity. The user has not placed it in the first
  release; it is planned after the core, web UI and Telegram **(design call)**.

## 5. Interfaces

### 5.1 Command line
- One `foreman` command for both people and agents. Its output is deterministic, short and spends no
  model tokens.
- Skills tell agents **when and why**; the command does the **how**.
- Functions:
  - `status`: sessions, their state, context, owner; across projects or for one project;
  - `limits`: provider windows with usage, reset time and reading age;
  - `send` / `ask`: message any session (§4.7);
  - `open` / `close`: sessions, including rotation and each worker's worktree (§4.2);
  - `board` / `log`: read the board; append to or search the journal;
  - `watch`: the scheduled event check (§4.6) and the list of active watches;
  - `clean`: work-file cleanup, with a listing first (§4.9);
  - `report`: record a finding (§4.11);
  - `init`: set up a project; safe to run again;
  - `web`: start the web UI (§5.2).

### 5.2 Web UI
- **One web UI per machine.** It shows the orchestrators of all projects on that machine **(user)**.
- It works equally on a phone and on a desktop **(user)**.
- The layout is the chosen mockup, "Direction 3": a calm, dark control room
  (`assets/2026-11-05-web-ui-mockup.html`) **(user)**.
- **Top bar**: only the weekly limit of each provider, as two small meters. The 5-hour windows and the
  thresholds appear on hover or tap.
- **Left: triage rail** with every project of the machine, grouped by urgency: *Ждёт вас* (needs you),
  *Застряла* (stuck), *Работают* (working), *Тихо* (quiet).
  - Each row has the project name, one line (the pending question or the current stage), and a status dot.
- **Centre: the chat** with the selected project's orchestrator. The user reads its messages and replies.
  - The orchestrator is still a real interactive tab; the terminal stays the full view. The chat is not a
    mirror of the terminal. It holds only what the orchestrator explicitly addresses to the user, and the
    user's replies from any channel (web, Telegram). The orchestrator marks a message as a question when it
    needs a decision. The same conversation appears in the web chat and in the project's Telegram topic
    **(design call)**.
  - A reply that arrives while the orchestrator is busy is queued and delivered at its next idle point.
    If its terminal is gone, the user is told so in the chat instead of the reply vanishing **(design call)**.
  - A message that asks a question carries one-tap answer buttons (e.g. Да / Нет / Ответить…); no other
    message does.
- **Pinned status strip** above the chat: current stage, number of sessions, the heaviest context
  (e.g. "220k ⚠").
  - Clicking it opens a **drawer** on desktop (a **bottom sheet** on the phone) with the project's
    **Board** and **Sessions** (agent, model, context, owner, state).
- The default view never shows:
  - cache-warmth markers or cost calculations;
  - a full matrix of all projects × sessions;
  - long session IDs.

  Those details are inside the drawer.
- **Colour discipline**:
  - one accent colour for "needs you";
  - amber for warnings (context over target, a limit ≥70%);
  - red only for stuck sessions and limits ≥90%.
- **Phone**:
  1. the triage list;
  2. the project chat with the status strip;
  3. tapping the strip opens the Board / Sessions bottom sheet.
- **Live**: what is on screen updates by itself, without a reload.
- Through the UI, the user can talk to an orchestrator, and ask it to open, close or rotate a session.
  The UI also shows foreman's findings inbox.
- **Access**: never from the public internet; every access needs a token **(design call)**.
  - Home server: reachable from the home network, from the user's desktop and laptop over OpenVPN, and
    from phones over WireGuard **(user)**.
  - Work Mac: its web UI is local to the Mac. It is not connected to the home server and does not need to
    be **(user)**.
  - A device is paired once. Links in notifications do not carry the token, so Telegram never stores a
    secret **(design call)**.
  - Each machine has its own web address. A notification links to the web UI of the machine whose
    orchestrator sent it **(design call)**.

### 5.3 Notifications and Telegram
- When an orchestrator needs the user, it sends a **notification**. Opening the notification goes
  **straight to that project's chat**, at that message **(user)**.
- Telegram:
  - **one group per machine**, with **one topic per project**, where the project's orchestrator posts
    **(user)**;
  - the user can **reply in the topic**, and the reply goes to that project's orchestrator, whatever
    agent it runs on **(user)**;
  - One bot per machine, so two machines never compete for the same bot's messages **(design call)**.
  - foreman owns its Telegram bot. The user's existing aibroker is not the foundation: it routes only to
    one active session, is Claude-specific, and its Telegram adapter isn't installed. It may be added
    later as an extra channel **(design call)**.
  - Topics are created by the bot when a project is initialised **(design call)**.
  - The group's General topic belongs to foreman itself: limit warnings, findings, foreman's own errors
    **(design call)**.
  - Worker sessions get no topics. Their questions to the user go through their orchestrator, so every
    project has one voice **(design call)**.
- The notification channel can be replaced or extended without changing foreman's behaviour.
- **Work Mac**: it has no access to Telegram **(user)**. There, notifications are the Mac's own desktop
  notifications, and opening one goes to the same chat in the Mac's local web UI **(design call)**.
- **Telegram setup** is done by an agent, not by the user. The user gives Claude Code access to a desktop
  with a browser. Through Telegram in that browser, the agent creates the bot, the group, turns on topics
  and makes the bot an admin **(user)**. On a machine without Telegram, this step is skipped.

### 5.4 Install and project setup
- The user says "установи плагин" to any supported agent, and the agent does the rest **(user)**.
  - One install serves all supported agents on the machine.
  - There is no separate installer to run by hand.
- Initialising a project:
  - registers its hooks for every agent present;
  - makes the project folder trusted where an agent needs that to load the hooks (§5.5);
  - adds the one work-file rule (§4.9);
  - creates the project's Telegram topic, where the machine has Telegram;
  - creates its board.
- Running it again changes nothing that is already correct.
- Initialising, and removing foreman from a project, are refused inside a worker's worktree; they are done
  in the project's own folder. Removing foreman from a project also takes its hooks out of that project's
  kept worktrees, so they do not stay dirty or keep reporting, and takes back the trust entries foreman
  added for the project and its worktrees (§5.5).
- Uninstalling foreman takes its hooks and the trust entries it added out of kept worktrees too, and a full
  purge refuses while any worktree remains.

### 5.5 Trust in Antigravity and Codex
Antigravity loads a folder's hook files only when the folder is in its list of trusted workspaces. Codex loads
a project's hook files only when the project is trusted, and runs each hook only when its hash is approved.
Without that trust foreman's hooks silently do not run: the session is invisible and the rules of §4.1 are not
enforced. The user does not do this by hand: foreman writes the trust itself, narrowly. This replaces the
earlier rule that the user approves foreman's hooks in each agent (decision D24). Claude Code needs no such
write.

**What trust really grants.** It is not only "let foreman's hooks run".
- *Antigravity:* a trusted workspace runs **every** hook file the workspace has (`.agents/hooks.json`, with
  no hash), foreman's and the repository's own.
- *Codex:* `trust_level = "trusted"` turns on the project's whole config layer: Codex's own message for an
  untrusted project says it withholds "project-local config, hooks, and exec policies". So a
  `.codex/config.toml` (approval policy, sandbox mode, MCP servers) takes effect, and so does one that appears
  later, also in a worktree; exec policies (`.codex/rules`) are meant to as well (not exercised separately).
  Hook approval is separate and per hook (measured on Codex 0.146.1).
So foreman skips the trust of an agent, and says why, when the folder already holds what that trust would
switch on: a `.agents/hooks.json` with hooks that are not foreman's (Antigravity), or anything in `.codex/`
except foreman's `hooks.json` (Codex). Trusting such a folder is the user's own decision, made by hand.
- *When it is checked.* At `init`, and at every `open` and `rotate` for the **launched agent**, Codex included,
  on the worktree's own files, because a tracked file arrives with the checkout and a commit may add one after
  `init`. Foreign content at `open` or `rotate` means the session is not opened (the rotation fails), whatever
  consent `init` recorded.
- *The user's own trust, for the content the user was shown.* If `init` finds foreign content and the user has
  already trusted the folder by hand (for Antigravity, an exact entry for the project folder that is not in
  foreman's record; for Codex, Codex reports foreman's hooks `trusted` and the table is not in the record),
  `init` prints that content, writes nothing, counts the agent as consented and records a digest of the
  content with the consent. The digest covers only the content that is not foreman's (the other hooks, the other
  files), so a moved install that changes foreman's own handlers in the same file does not void the waiver. The waiver covers **that content only**: at `open` and `rotate` the check still
  runs, and content that is new or changed since the digest means the session is not opened, with "review it
  and run `init` again". The waiver lapses when the user's entry is gone. Without such a waiver the check
  always runs, whoever owns the entry: an entry that predates foreman, or a pending one left by a crash, is
  not a waiver. When the trust is foreman's own (in the record) and foreign content appears, nothing is
  waived: `init` reports it, and `open` refuses for that agent while the content is in the checkout. The
  route to a waiver then is `foreman init --remove`, the user's own entry, and `init` again; `init` says so.

**Consent, and who may trigger the write.**
- Running `foreman init` in a folder is the user's consent to trust **that folder**, for the agents the
  project has hooks for. `init` prints exactly what it trusted, what each kind of trust switches on (above),
  and what it skipped. Only `init` ever adds a project folder.
- *Consent is a field.* `init` sets, in the project's record, a consent per agent: *granted* (including when the
  entry was already there, or Codex already reports the hooks `trusted`) or unset (the agent was skipped or
  refused). `open` and `rotate` write trust only for an agent whose consent is granted. A project initialised
  before this rule has none: for the agent being launched, `open` is refused and `rotate` fails with "run
  `foreman init` again"; for the project's other agents `open` only warns. The orchestrator cannot run `init`
  (it writes trust), so on existing projects autonomous work with Antigravity or Codex workers waits for the
  user to run `init` once; Claude workers are not held up.
- *Consent is withdrawn* by `init --remove` and by `uninstall`, and by an `init` that now refuses or skips the
  agent. An `init` refused because it ran in an orchestrator-side session changes nothing.
- `open` adds the worktree it just made, and `rotate` the same worktree when it first gets Antigravity hook
  files (Antigravity only, below). Neither adds a project folder.
- The write is refused when the command runs inside a session of the orchestrator's side (an orchestrator, or
  a worker it owns), whatever path it names (`init --path` included). It is allowed from a terminal, and from
  a session the user owns, which is how an agent installs foreman for the user. `open` and `rotate` are
  run by an orchestrator by design and write only the paths in the next bullet.
- A path is acceptable only if, after resolving links, it is none of: `/`, the user's home or any folder
  above it, the state folder or anything inside it except a worktree foreman made for this project, any folder
  above the state folder, the Antigravity or Codex configuration folder or any folder above them, a folder
  that contains another registered project, or a folder that is not itself the root of a git repository and
  has a direct child that is (a folder of repositories such as `~/projects`; a monorepo root is itself a
  repository, so it is allowed). The last rule does not depend on what is registered, so it does not depend
  on the order of `init`s. A worktree
  must also be a direct child of `<state>/worktrees/<project>/`, equal to the path in the session's record, and
  listed by `git worktree list` of the project. Anything else: no trust is written, `init` does everything
  else and exits with a failure status naming the reason, and `open` and `rotate` refuse.
- *Threat model.* An agent that runs as the user can edit the agents' settings files directly; this section
  does not defend against that. It makes sure that foreman itself is not the lever: a session of the
  orchestrator's side, found through the session environment it was started with, cannot make foreman trust a
  folder. A process that removes that environment looks like a terminal; that is the agent-as-the-user case
  above and is out of scope.
- The path written is the resolved one, and only that: Codex does not match a link, and an entry that is a
  link would follow a later retarget to another folder.

**Add-only.** foreman never removes, reorders or rewrites an entry that is already there, and adds no pattern
and no folder above the project. An entry already there is left alone and is not recorded as foreman's. An
entry that refuses the trust (Codex `trust_level = "untrusted"`, a hook with `enabled = false`) is the user's
decision: it is kept, and `init` reports the project as not trusted instead of passing silently.

**How a file is edited** (the same for both agents' files):
- *Target.* The file the agent really reads: Codex's `config.toml` under `CODEX_HOME` when set, else
  `~/.codex`; Antigravity's `settings.json`. A link is followed and its target edited (a file that is not a
  regular file owned by the user is refused), so a managed dotfile is not replaced by a plain copy.
- *Lock.* foreman holds a lock for that file from the read to the end of the rename and the update of
  `trust.json`, so two foreman commands (two `open`s at once, §8.12) cannot lose each other's entry. The lock
  file lives in the state folder, not beside the target. Every foreman edit of these files takes the same
  lock, the statusline wrapping and unwrapping included (which also gets the atomic rename below).
- *Against the agents.* They cannot be locked. Just before the rename foreman compares the file with what it
  read, and starts again from the read if it changed; after the rename it reads the file back and, if its
  entry is missing, starts again too. Up to three times, then it reports failure. A change made inside the
  last instant can still be lost; the check under "Is it working" finds a lost entry later.
- *Narrow.* The new text is the old text plus the added lines; comments, order and formatting survive.
  Paths are encoded by a real JSON or TOML encoder, and a path with a control character is refused. Before
  the rename foreman parses the old and the new text and requires the difference to be exactly the intended
  one; otherwise nothing is written.
- *Atomic.* The temporary file and the backup are created exclusively, with the original's permissions set
  before any content is written, in the target's folder; the temporary file is then renamed over the target.
- *Idempotent.* When everything is already there, nothing is written, no backup is made and the state does
  not change.
- *Invalid input.* A file that is not valid (strict JSON, no BOM or comments; TOML), is not the expected shape
  (`trustedWorkspaces` not a list or holding non-strings; `hooks` or `projects` not a table), or is not writable is left exactly as it was, with no backup,
  and the reason is reported. Parsing TOML needs Python 3.11 (`tomllib`); on an older Python Codex trust is not
  written and `init` says so.
- *Backup.* Before the first edit of a file, a copy with the same permissions is saved as
  `<file>.foreman-bak` and never overwritten. It is the file as it was before foreman's first edit, for the
  user to compare; it is not used to undo a trust edit. The Antigravity file already has one from the
  statusline wrapping, and they share it. `uninstall` deletes a backup only if the file equals it again.
- *Which state.* Trust is written only when the state folder is the default one for the HOME in effect, so a
  trial instance with a throwaway state folder cannot leave entries its own record would never remove. A file
  outside that HOME (a `CODEX_HOME` elsewhere, a dotfile link whose target lies outside) is written only when
  HOME is the account's own home, so a real user's setup works and a trial with a redirected HOME cannot reach
  the real files through an inherited `CODEX_HOME`. Otherwise the step is skipped and said so.

**What is recorded.** `trust.json` in the state folder lists, per file, the entries foreman added: the path or
the Codex key and the hash written, the project, whether it is for the project folder or a worktree, and the
time. An entry is
written to the record as *pending* before the file is edited and becomes *applied* after the read-back. A
pending entry is never removed from the agent's file, because after a crash foreman cannot know whether the
entry was already the user's; it is reported and then dropped from the record, so a crash can leave one
entry that nobody removes. If `trust.json` cannot be
read, foreman adds nothing and removes nothing, and reports it.

**Taking it back.** foreman removes only *applied* entries, and only while they are still as foreman wrote them
(a Codex table must hold exactly the keys and values foreman wrote; one the user has changed stays and is
reported).
- When a worktree is removed (§4.2) its entries go with it; a kept worktree stays trusted. The scheduled
  check also removes recorded worktree entries whose folder no longer exists. A launch that fails after the
  write takes its entry back.
- `init --remove` removes the project's entries, and those of its kept worktrees that have no open session.
- `uninstall` removes all of them, before it restores the statusline commands, so a settings file that nothing
  else changed returns to its original bytes. A full purge refuses while a recorded entry could not be removed.
- A file that cannot be read then keeps its records and is reported.

**Is it working.** After a write foreman reads the file back (Antigravity), or asks Codex read-only about the
real state and expects `trusted`. (Codex's query is not free of side effects: it can create files in the Codex
home.) The scheduled check repeats that for every initialised project and its open workers, at most once an
hour per project, and reports drift once per occurrence, with `status`: an Antigravity entry dropped by the agent
rewriting its file, a Codex hook turned `modified` by a moved install or a changed `FOREMAN_HOME`, or a
`.codex/` content other than foreman's `hooks.json` or a foreign `.agents/hooks.json` that appeared in a trusted
folder or one of its worktrees, and is not covered by a waiver (content inside a waiver is not reported again). It only reports. `init` repairs the project folder and the entries of live
worktrees. A `.codex/config.toml` that a worker drops into a worktree after `init` takes effect for the next
Codex session there before the report: that residual risk is accepted, not prevented.

**Antigravity.**
- *Where.* `trustedWorkspaces`, a list of absolute folder paths, in `~/.gemini/antigravity-cli/settings.json`,
  the file whose statusline foreman already wraps. Antigravity adds entries there itself: today's file holds
  entries it wrote for earlier worktrees next to broad ones for the home folder and `~/projects`. foreman
  neither relies on the broad ones nor removes them. A missing file is created with mode 0600 holding only
  this key, if its folder exists.
- *What.* The project folder at `init`. A worktree at `open`, after it and its hook files exist, once its own
  `.agents/hooks.json` is checked to hold only foreman's hooks, and before the agent is launched, whenever the worktree gets Antigravity hook files, whichever agent is launched in it, so
  a session started by hand there is seen as well (§4.2).
- *Evidence.* Antigravity loads a workspace's hook files only for a trusted workspace, and loaded them in a run
  that supplied the entry (`docs/build/integration-facts.md`, Antigravity).

**Codex.** Whether the hook hash can be reproduced decides the case: **it can, so Codex is handled like
Antigravity and is not a manual step**, with one difference: Codex works **per repository**, not per worktree.
- *Evidence (Codex 0.146.1, and its source).*
  1. `~/.codex/config.toml` holds, per approved hook, `[hooks.state."<hooks file path>:<event>:<group>:<handler>"]`
     with `enabled` and `trusted_hash = "sha256:…"`.
  2. The hash is the SHA-256 of the compact JSON, with sorted keys, of
     `{"event_name": <snake_case event>, "matcher": <only if the group has one>, "hooks": [{"type": "command",
     "command": …, "timeout": <seconds, 600 if none>, "async": false}]}`. It covers the command text, timeout
     and matcher, not the script the command runs. This is what Codex's source does (a normalised hook
     identity hashed by `version_for_toml`).
  3. Reproduced: this recipe gives all 9 hashes in the user's real file. For three hooks shaped like foreman's
     (one with matcher `*`, one with the SessionEnd default timeout), Codex's `hooks/list` reported the same
     `currentHash`, and the status changed from `untrusted` to `trusted` once those hashes were written to a
     scratch Codex home.
  4. A project's hook files are listed at all only when `[projects."<folder>"]` is trusted.
  5. In a linked git worktree Codex ignores the worktree's `.codex/hooks.json` and reads the main checkout's;
     the key names the main checkout's file, and approving it makes the hooks `trusted` in every worktree
     (measured twice, independently). A worktree needs no Codex entry of its own.
  6. Any change to a hook (a new `FOREMAN_HOME`, a moved install) turns it `modified`; Codex then skips it
     without a word.
- *What.* At `init`, for the main checkout: the `[projects."<folder>"]` entry with `trust_level = "trusted"`
  (skipped as above when `.codex/` holds more than foreman's `hooks.json`), and for each of foreman's hooks in its
  `.codex/hooks.json` a `[hooks.state."<key>"]` table with `enabled = true` and the hash. `open` and `rotate`
  write nothing for Codex; for a Codex worker they check, read-only, that Codex reports foreman's hooks
  `trusted` from the worktree. If it does not (for example Codex was not installed at `init`, so the main
  checkout has no Codex hooks), the session is not opened or the rotation fails with "run `foreman init`
  again". The Codex hook file that `open` writes into a worktree is ignored by Codex. `init` inside a linked
  worktree that foreman did not make (an Orca workspace) writes no Codex trust and says that Codex trust is the
  main checkout's: run `init` there.
- *Which hooks.* "foreman's hooks" means exactly the handlers foreman generates now. foreman approves a hook
  only if `hooks/list` reports its command, timeout and matcher equal to those, its source `project`, and its
  path the project's `.codex/hooks.json`. Anything else in a slot that looks like foreman's is reported and never
  approved, even if it matches today's test for a foreman handler. After the query foreman checks that the
  file has not changed, else starts again.
- *Where the hash comes from.* From Codex, not from a copy of the recipe in foreman, because the recipe is
  Codex's internal and may change between versions. foreman asks `codex app-server` (`hooks/list`, which needs
  no login) about the folder, with its Codex home pointing at a scratch folder that holds only the
  `[projects]` entry, and writes the key and `currentHash` it reports. The user's real `config.toml` is not
  touched by this check. A table that foreman recorded and whose key or hash is no longer right (a changed hook,
  or a shifted group index) is replaced; one that foreman did not add is never touched. When such a table
  leaves a foreman hook `modified` (the user approved an older hook by hand), `init` reports the next step:
  approve the hook in Codex `/hooks` from the main checkout, or delete that table from `config.toml` and run
  `init` again.
- *If it cannot.* When `codex` is missing, does not answer within 10 seconds, or reports none of foreman's
  hooks for the folder, nothing is written and `init` says that the hooks still need approval in Codex
  `/hooks`. A worker whose hooks Codex skips is not seen by foreman.
- *Role.* Codex is still a worker agent only in v1. The reason is not trust: its hooks have not been exercised
  in a logged-in session (D23), and the findings above come from `hooks/list`, not from a hook firing.

**When the file cannot be edited.**

| State of the file | `init` | `open` of a worker whose agent needs it | `rotate` to that agent |
|---|---|---|---|
| fine, entry added | continues | continues | continues |
| missing | Antigravity: created; Codex: created holding only the added tables | same | same |
| invalid, wrong shape, unwritable, lock lost three times | does everything else, then exits with failure and the manual step | session not opened, nothing left behind | rotation fails, old session stays |
| trust refused (by the user, by the rules above, or foreign content in the folder) | exits with failure naming the reason and the user's next step; passes once the user has trusted the folder by hand (Antigravity: the entry; Codex: the hooks reported `trusted`) | session not opened | rotation fails |

For a worker of another agent in the same project, a failed or refused write is a warning in the result of
`open`, not a refusal.

Codex trust is never written by `open` or `rotate`; for a Codex worker they require that foreman's hooks
already report `trusted` (above).

**What the skills say.** The rule "never trust a hook on the user's behalf" becomes: *`foreman init` does the
narrow trust write; sessions and orchestrators never do it themselves.* An agent that installs foreman for the
user may run `foreman init`; an orchestrator or a worker it owns may not trigger the write.

## 6. Where it runs
- **Machines**: Linux (the home server) and macOS (the work computer) **(user)**.
- **Agents**: Claude Code, Antigravity and Codex as a minimum **(user)**.
  - The work Mac has Claude Code and Antigravity.
  - Codex is installed at home.
- **Transports**: Orca and Herdr, both first-class **(user)**; plus Claude inter-session messaging.
- **Distribution**: a public repository that others can install from **(user: will share; design call:
  public)**.

## 7. Privacy and safety
- Nothing from a project leaves the machine unless it has been stripped of project data (§4.11).
- Terminal output, messages from other sessions and transcripts are untrusted data. They never become
  instructions or rule changes by themselves.
- The web UI is never reachable from the public internet (§5.2).
- foreman never grants permissions to itself (§4.12). The one trust it grants is the narrow, recorded write of
  §5.5, on the user's consent at `init`. An orchestrator, a worker it owns or a message cannot make it
  write trust; trust is not limited to hooks, and §5.5 says what each kind switches on.

## 8. Acceptance: how we know it works
1. Over a normal week of work, the user does not have to kick the orchestrator or ask how things are going.
2. When a worker finishes, gets stuck or crosses a threshold, the orchestrator learns about it without
   being asked, and with no periodic model calls.
3. An attempt by the orchestrator to edit project files is blocked.
4. With nothing happening for a day, foreman spends zero model tokens.
5. A rotation keeps the work going:
   - the new session has the handoff as a message;
   - a batched question to the old session within the warm-cache window gets answered;
   - the board shows who replaced whom.
6. At home: a Telegram notification opens the right project's chat on a phone over WireGuard. A reply in
   the project's topic reaches that project's orchestrator, on Claude Code and on Antigravity alike.
   On the work Mac: a desktop notification opens the right chat in the local web UI.
7. The web UI shows every project of the machine, matching the chosen layout on phone and desktop.
8. A limit at ≥70% steers new sessions to the other provider; at ≥90% no new sessions start there.
   An empty reading never hides the last known value.
9. A work folder left for 14 days disappears with no action from the user, after a listing.
10. A finding reported from the work Mac stays a local draft; on a public issue it contains no project data.
11. The same install works on Linux and macOS, from the phrase "установи плагин".
12. Two workers opened at once in a git project each work in their own worktree and branch, and neither
    changes the other's files. Closing one removes its worktree unless that would lose work, and leaves
    the other untouched.
13. After `foreman init` in a folder, a new Antigravity session started there and an Antigravity worker opened
    in a worktree load foreman's hooks with no trust step done by hand, and Codex reports foreman's hooks
    `trusted` for the folder and for its worktrees. `foreman uninstall` leaves both agents' settings files
    with every entry that was there before and none that foreman added (but for one a crash left pending, §5.5).
    A Codex hook actually firing in a
    worktree needs a logged-in Codex session; until one exists (D23) that part is checked with `hooks/list`.
14. Trust is refused, with a reason and a failure status, and the files are unchanged, for: `/`, the home
    folder or a link to it; a folder of repositories such as `~/projects`, as the first project or after its
    children; a folder with a foreign `.agents/hooks.json` or, for Codex, anything in `.codex/` but foreman's
    `hooks.json`, also when it arrives with a worktree's checkout, and also when the folder's entry predates
    foreman and the content was committed after `init`. With a waiver: the same content opens, changed content
    is refused, and so is an `open` after the user's entry was removed; a hook file whose command is not what foreman generates; a malformed
    settings file; an orchestrator's session running `init --path`. Two workers opened at once both end up
    trusted. An entry that is already there is never removed by foreman, including after a crash between the
    record and the edit.

## 9. Left to the builder
- Thresholds for "stuck" and "waiting too long", and what counts as "quiet" in the triage rail.
- The syntax for project gates (§4.10).
- How sessions the user already had open get registered when a project is initialised.

## 10. Open questions
1. Does the user have a Codex subscription that foreman should count in limits, or is Codex only an agent
   it can drive? Until answered, foreman shows Codex limits only if Codex exposes them.
2. For the plan, not this document: the order in which the parts arrive (proposed: core → web UI +
   Telegram → self-improvement → permissions), and whether shopping-list is the first project to switch
   to foreman (replacing its local `orchestrator/`).
3. Does any agent ask the user to trust a new worktree folder under Herdr? Claude Code and Antigravity were
   tried on Orca and no trust prompt blocked them. (Codex needs nothing for a worktree: it reads the main
   checkout's hooks, §5.5.)
4. Does Antigravity treat a workspace as trusted when only a parent folder is listed (the user's file lists
   the home folder and `~/projects`)? §5.5 adds the exact resolved path, so this decides only how many entries pile up.
5. Codex's `hooks/list` is the only source of the hook key and hash that §5.5 relies on. If a Codex version
   drops it, Codex trust falls back to the user's approval in `/hooks` for that version, and the scheduled
   check reports the hooks as not trusted.
6. A logged-in Codex session is needed to confirm that a hook fires from a worktree, and with which working
   folder.
7. Should foreman ever write Codex trust for a repository that has its own `.codex/` content (a
   `config.toml`, rules), after showing the user what it contains? §5.5 says no, the user does it by hand.
   Whatever the answer, a config file that a worker drops into a worktree after `init` takes effect before the
   scheduled check reports it; the user should accept that knowingly.
