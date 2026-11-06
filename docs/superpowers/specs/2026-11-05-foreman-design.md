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
- *If it cannot be made.* The session is not opened and nothing is left behind: no folder, no branch, no
  registration, and nothing else touched. A worktree left by an `open` that was killed half way, with no
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
  Files that git ignores (caches, a `.env`) are not kept: they go with the worktree. On `close` foreman
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
  - adds the one work-file rule (§4.9);
  - creates the project's Telegram topic, where the machine has Telegram;
  - creates its board.
- Running it again changes nothing that is already correct.
- Initialising, and removing foreman from a project, are refused inside a worker's worktree; they are done
  in the project's own folder. Removing foreman from a project also takes its hooks out of that project's
  kept worktrees, so they do not stay dirty or keep reporting.
- Uninstalling foreman takes its hooks out of kept worktrees too, and a full purge refuses while any
  worktree remains.

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
- foreman never grants permissions to itself (§4.12).

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
3. Does Codex approve foreman's hooks per path, so a fresh worktree needs a new approval? Does any agent
   ask to trust a new worktree folder under Herdr? Claude Code and Antigravity were tried on Orca and no
   trust prompt blocked them. Until checked, foreman prints a note when it opens a Codex worker in a new
   worktree.
