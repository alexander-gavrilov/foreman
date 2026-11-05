# Telegram Setup Runbook for Autonomous Agents

This runbook guides an AI agent with browser access to Telegram Web (`https://web.telegram.org`) to configure the dedicated Telegram channel and forum supergroup for a foreman machine (architecture §10, spec §5.3).

> **Security Rule**: Never log, commit, or print plain API tokens to disk or logs. The token is piped directly into `foreman telegram set-token`.

---

## Prerequisites

1. An agent session with browser access (or desktop access) logged into Telegram Web.
2. The machine identifier (`<machine>`, e.g. `ghome`).
3. Access to run shell commands on the local machine with `foreman` CLI on PATH.

---

## Step 1: Create the Telegram Bot with @BotFather

1. Open Telegram Web and search for `@BotFather`.
2. Start the chat with `/start`.
3. Send `/newbot`.
4. When prompted for the bot's name, send:
   ```text
   foreman <machine>
   ```
   *(Example: `foreman ghome`)*
5. When prompted for a username (must end in `bot`), send a unique username:
   ```text
   <machine>_foreman_bot
   ```
   *(Example: `ghome_foreman_bot`)*
6. @BotFather will respond with a congratulations message containing the HTTP API token:
   `Use this token to access the HTTP API: <TOKEN>`
7. Copy the token into memory. Do not save it in files yet.

---

## Step 2: Disable Privacy Mode

By default, Telegram bots only receive messages that start with `/` or mention the bot. To receive topic messages and user replies in the forum group, privacy mode must be disabled:

1. In `@BotFather`, send:
   ```text
   /setprivacy
   ```
2. Select your newly created bot from the inline keyboard.
3. Select **Disable**.
4. @BotFather will confirm: `Success! The new status is: DISABLED.`

---

## Step 3: Create the Supergroup

1. In Telegram Web, open the menu (top left) and click **New Group**.
2. Group Name:
   ```text
   foreman · <machine>
   ```
   *(Example: `foreman · ghome`)*
3. Add the newly created bot (`@<machine>_foreman_bot`) as an initial member.
4. Click **Create Group**.

---

## Step 4: Enable Topics (Forum Mode)

1. Open the group details / settings:
   - Click the group header / title at the top.
   - Click **Edit** (pencil icon) or group settings.
2. Locate the **Topics** toggle (sometimes labeled **Forum**).
3. Toggle **Topics** to **ON**.
4. Save the changes. The group now has a default **General** topic.

---

## Step 5: Promote the Bot to Administrator

The bot requires topic creation and management rights:

1. In group settings, open **Administrators** -> **Add Admin**.
2. Select `@<machine>_foreman_bot`.
3. Ensure the following administrator permissions are enabled:
   - **Manage Topics** (required to create topics for projects)
   - **Post Messages** / **Send Messages**
   - **Delete Messages** (optional, recommended)
4. Save admin permissions.

---

## Step 6: Send Setup Message & Obtain Supergroup Chat ID

To associate the chat and authorize your user account:

1. Send any message (e.g. `setup`) in the group's **General** topic from the user account that will control foreman.
2. Obtain the numerical chat ID (supergroups typically start with `-100`):
   - **Option A (Browser URL)**: Inspect the browser URL in Telegram Web:
     `https://web.telegram.org/a/#-1001234567890` -> `-1001234567890`.
   - **Option B (Bot API)**:
     ```bash
     curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates" | jq '.result[-1].message.chat.id'
     ```

---

## Step 7: Configure foreman CLI & Inbound User Filtering

On the machine where foreman is installed:

1. **Store Bot Token**:
   Pipe the token directly into stdin so it is never exposed in shell history:
   ```bash
   echo -n "<TOKEN>" | foreman telegram set-token
   ```
   This creates/updates `$FOREMAN_HOME/secrets.json` with permissions `0600`.

2. **Set Supergroup Chat ID & Authorize Setup User**:
   ```bash
   foreman telegram set-chat -1001234567890
   ```
   This stores `telegram.chat_id` in `$FOREMAN_HOME/config.json`.
   It automatically inspects recent updates to record the user ID who sent the setup message into `telegram.allowed_user_ids`.

3. **Authorize Additional Users (Optional)**:
   Foreman strictly filters inbound updates: messages and button callbacks are accepted **only** from the configured `chat_id` AND user IDs listed in `telegram.allowed_user_ids`. To allow another user:
   ```bash
   foreman telegram allow-user <USER_ID>
   ```
   When `foreman telegram poll` encounters a message from an unlisted user or foreign chat, it prints a notice to stderr (`telegram: update from unknown user <ID> in chat <CHAT_ID>`) and logs it to `events.jsonl`, allowing the setup agent or administrator to pick up the ID.

4. **Verify Setup**:
   Run the diagnostics check:
   ```bash
   foreman telegram check
   ```
   **Expected output**:
   ```text
   ok: bot @ghome_foreman_bot in chat foreman · ghome (-1001234567890)
   ```
   If any permission or configuration is missing, `telegram check` will exit with code 1 and print exactly what is missing (e.g. missing token, missing `can_manage_topics`, or forum mode disabled).

---

## Step 8: Project Topics Initialization

When initializing or migrating a project:
```bash
foreman init
```
or explicitly:
```bash
foreman telegram topic <project-slug>
```
This creates a dedicated forum topic named after the project in the supergroup and records `telegram_topic_id` in `projects/<project-slug>/project.json`. Running this again is idempotent and will not duplicate topics.

---

## Step 9: Verification

1. **General Topic (/status)**:
   - In Telegram Web, navigate to the **General** topic.
   - Send:
     ```text
     /status
     ```
   - Bot responds immediately with active projects and the one-line limits summary:
     ```text
     demo: 1 active session(s)
     limits: Claude wk 45% · 5h 12% | agy wk 18% · 5h 5%
     ```
   - Note: Bot messages never expose device tokens or pairing secrets.

2. **Project Notification**:
   - In a project with an active orchestrator, trigger a question or say:
     ```bash
     foreman say "Build complete. Run migration?" --question --options "Yes|No"
     ```
   - Check the project topic in Telegram: the message appears with a click-through URL `<web.base_url>/#/p/<slug>/m/<msg_id>` and inline buttons `[Yes]` `[No]`.
   - Clicking a button submits the user reply and delivers it to the orchestrator.

3. **Topic Reply**:
   - Reply directly in text to a message in the project topic.
   - Verify that the message is received and enqueued in the project's orchestrator inbox.

---

## Step 10: Rollback / Decommission

If decommissioning the machine's Telegram channel:

1. Revoke the token in `@BotFather` (`/revoke` -> select bot).
2. Remove the bot from the supergroup.
3. Remove secrets and configuration locally:
   ```bash
   foreman telegram set-chat ""
   # Remove bot token entry from $FOREMAN_HOME/secrets.json
   ```
