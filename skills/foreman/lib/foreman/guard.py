"""Orchestrator guard (spec 4.1, architecture 4): an orchestrator may coordinate but not change project
files or git state. `check_tool` / `check_command` return None when the call is allowed, else the block
message that is shown to the agent.

Shell commands are allowed only if every pipeline segment starts with an allowlisted read-only or
coordination command and nothing writes to a file or runs nested code. The command line is scanned with a
small quote-aware scanner (not plain shlex: shlex cannot tell a quoted ">" from a redirection)."""
import os
import re

SHELL_TOOLS = {"bash", "run_command", "shell", "local_shell", "exec_command", "shell_command",
               "execute_command", "run_shell_command", "unified_exec", "container.exec"}
# Deny by default: only these read-only / coordination tools (and the shell tools above) may be used by an
# orchestrator. Subagent calls (Agent/Task) are covered because their own tool calls hit the same guard.
ALLOWED_TOOLS = {
    # Claude Code
    "read", "grep", "glob", "ls", "todowrite", "webfetch", "websearch", "sendmessage", "agent", "task",
    "toolsearch", "skill",
    # Antigravity
    "view_file", "list_dir", "grep_search", "find_by_name", "read_url_content", "search_web",
    # Codex (names unverified, from its documentation)
    "read_file", "grep_files", "update_plan", "web_search", "view_image",
}

SIMPLE_COMMANDS = {"ls", "cat", "head", "tail", "grep", "rg", "wc", "jq", "echo", "pwd", "which", "tree",
                   "stat", "file", "date", "find"}
COORDINATION_READONLY = {"orca": {("terminal", "list"), ("terminal", "show"), ("terminal", "read"),
                                  ("terminal", "wait"), ("worktree", "ps"), ("worktree", "list")},
                         "herdr": {("pane", "list"), ("pane", "read"), ("agent", "list"), ("agent", "read"),
                                   ("agent", "wait")}}
# foreman subcommands an orchestrator may run. Value: None = any arguments, or a checker (see _check_foreman).
FOREMAN_SAFE = {"status", "limits", "send", "ask", "reply", "say", "open", "close", "rotate", "compact", "board",
                "log", "stage", "watch", "clean", "work", "report", "gate", "orchestrator"}
USER_ONLY_ORCHESTRATOR = ("adopt", "repair")
FETCH_FLAGS = {"--prune", "-p", "--all", "-q", "--quiet", "-v", "--verbose"}
REMOTE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
MERGE_FLAGS = {"--squash", "-s", "--merge", "-m", "--rebase", "-r", "--auto"}
MERGE_VALUE_FLAGS = {"--repo", "-R", "--match-head-commit", "--subject", "-t", "--body", "-b"}
FIND_FORBIDDEN = {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls"}
GIT_READONLY = {"status", "log", "diff", "show", "rev-parse", "rev-list", "ls-files", "ls-tree", "ls-remote",
                "cat-file", "blame", "describe", "shortlog", "whatchanged", "diff-tree", "name-rev",
                "merge-base", "for-each-ref", "show-ref", "grep", "check-ignore", "count-objects",
                "show-branch", "cherry", "diff-index", "diff-files", "version"}
# options of the read-only subcommands that write a file or run a program
GIT_FORBIDDEN_OPTIONS = ("--output", "--upload-pack", "--receive-pack", "--exec", "--ext-diff",
                         "--open-files-in-pager", "--textconv", "--config-env")
GIT_GLOBAL_OK = {"--no-pager", "--no-optional-locks", "-P"}
# `pr review` is absent on purpose: reviews come from a different instance, never from the orchestrator
# gh resolves "the repo" from the cwd with a remote named `upstream` before `origin`, so commands that post
# somewhere must name the repo explicitly (-R/--repo or a URL), and it must be the project's own origin.
GH_NEEDS_EXPLICIT_REPO = {("issue", "create"), ("issue", "comment"), ("pr", "create"), ("pr", "comment"),
                          ("pr", "merge")}
GH_ALLOWED = {"pr": {"view", "list", "create", "merge", "checks", "comment", "diff", "status"},
              "issue": {"view", "list", "create", "comment", "status"},
              "run": {"view", "list", "watch"},
              "repo": {"view", "list"}}
ENV_ASSIGN = re.compile(r"^FOREMAN_ACTOR=")  # the only variable an orchestrator may set

SEPARATORS = {"|", "||", "&&", ";", "&"}
WRITE_REDIRECTS = {">", ">>", ">|", "&>", "&>>", "<>"}


class _Reject(Exception):
    pass


def _block(detail, project=None):
    where = " --project %s" % project if project else ""
    return ("foreman: the orchestrator coordinates; it does not change project files or git state "
            "(blocked: %s). Delegate the work: open a worker session with `foreman open%s ...` and give it "
            "this task, then wait for its report." % (detail, where))


# ---------------------------------------------------------------- scanner

def _scan(cmd):
    """-> list of ("word", text) / ("op", text) / ("redir", op) tokens. Raises _Reject."""
    toks = []
    buf = []
    in_word = False
    i, n = 0, len(cmd)

    def flush():
        nonlocal in_word, buf
        if in_word:
            toks.append(("word", "".join(buf)))
        buf, in_word = [], False

    def fd_prefix():
        """A pure-digit word right before a redirection is its fd number, not an argument."""
        nonlocal in_word, buf
        if in_word and "".join(buf).isdigit():
            buf, in_word = [], False

    while i < n:
        c = cmd[i]
        if c in " \t":
            flush()
            i += 1
        elif c == "\n":
            flush()
            toks.append(("op", ";"))
            i += 1
        elif c == "\\":
            if i + 1 < n and cmd[i + 1] == "\n":
                i += 2
                continue
            buf.append(cmd[i + 1] if i + 1 < n else "")
            in_word = True
            i += 2
        elif c == "'":
            j = cmd.find("'", i + 1)
            if j < 0:
                raise _Reject("unbalanced quote")
            buf.append(cmd[i + 1:j])
            in_word = True
            i = j + 1
        elif c == '"':
            j = i + 1
            part = []
            while j < n and cmd[j] != '"':
                if cmd[j] == "\\" and j + 1 < n:
                    part.append(cmd[j + 1])
                    j += 2
                    continue
                if cmd[j] in "`$":
                    raise _Reject("shell expansion inside double quotes (use single quotes)")
                part.append(cmd[j])
                j += 1
            if j >= n:
                raise _Reject("unbalanced quote")
            buf.append("".join(part))
            in_word = True
            i = j + 1
        elif c in "`$":
            raise _Reject("command or parameter substitution")
        elif c in "()":
            raise _Reject("subshell or process substitution")
        elif c == "#" and not in_word:
            while i < n and cmd[i] != "\n":
                i += 1
        elif c == "|":
            flush()
            i += 2 if cmd[i + 1:i + 2] in ("|", "&") else 1  # | || |&
            toks.append(("op", "|"))
        elif c == "&":
            flush()
            if cmd.startswith("&>>", i):
                toks.append(("redir", "&>>"))
                i += 3
            elif cmd.startswith("&>", i):
                toks.append(("redir", "&>"))
                i += 2
            elif cmd.startswith("&&", i):
                toks.append(("op", "&&"))
                i += 2
            else:
                toks.append(("op", "&"))
                i += 1
        elif c == ";":
            flush()
            toks.append(("op", ";"))
            i += 1
        elif c == ">":
            fd_prefix()
            flush()
            for op in (">>", ">&", ">|", ">"):
                if cmd.startswith(op, i):
                    toks.append(("redir", op))
                    i += len(op)
                    break
        elif c == "<":
            fd_prefix()
            flush()
            if cmd.startswith("<<", i):
                raise _Reject("here-document or here-string")
            elif cmd.startswith("<>", i):
                toks.append(("redir", "<>"))
                i += 2
            elif cmd.startswith("<&", i):
                toks.append(("redir", "<&"))
                i += 2
            else:
                toks.append(("redir", "<"))
                i += 1
        elif c in "{}*?[":
            raise _Reject("unquoted brace or glob expansion (bash would expand it after the check)")
        else:
            buf.append(c)
            in_word = True
            i += 1
    flush()
    return toks


def _segments(toks):
    """Split the token list into command segments (lists of words), validating redirections."""
    segments, words = [], []
    k = 0
    while k < len(toks):
        kind, text = toks[k]
        if kind == "op":
            segments.append(words)
            words = []
        elif kind == "redir":
            k += 1
            if k >= len(toks) or toks[k][0] != "word":
                raise _Reject("redirection without a target")
            target = toks[k][1]
            if text in WRITE_REDIRECTS and target != "/dev/null":
                raise _Reject("redirection to %s" % target)
            if text == ">&" and target != "/dev/null" and not (target.isdigit() or target == "-"):
                raise _Reject("redirection to %s" % target)
        else:
            words.append(text)
        k += 1
    segments.append(words)
    return [w for w in segments if w]


# ---------------------------------------------------------------- per-command rules

def _git_dangerous_option(sub, arg):
    """Every token is checked, also after "--": an option such as `grep -e` can take "--" as its value, so
    "--" does not reliably end the options. git accepts any unambiguous abbreviation of a long option (--out=f, --upload=cmd), so a forbidden
    option is matched by prefix in both directions; `git grep` also takes -O inside a short-option cluster."""
    if arg.startswith("--"):
        name = arg.split("=", 1)[0]
        return len(name) > 2 and any(opt.startswith(name) or name.startswith(opt)
                                     for opt in GIT_FORBIDDEN_OPTIONS)
    return sub == "grep" and re.match(r"^-[^-]*O", arg) is not None


def _check_git(args):
    i = 0
    while i < len(args) and args[i].startswith("-"):
        a = args[i]
        if a == "-C" and i + 1 < len(args):
            i += 2
        elif a in GIT_GLOBAL_OK or a.startswith("--git-dir=") or a.startswith("--work-tree="):
            i += 1
        else:
            raise _Reject("git option %s" % a)
    if i >= len(args):
        raise _Reject("git without a subcommand")
    sub, rest = args[i], args[i + 1:]
    if sub == "fetch":
        return _check_fetch(rest)
    if sub in GIT_READONLY:
        for a in rest:
            if _git_dangerous_option(sub, a):
                raise _Reject("git %s %s writes a file or runs a program" % (sub, a))
        return
    if sub in ("branch", "tag"):
        flags = {"-a", "-r", "-v", "-vv", "--list", "-l", "--show-current", "--all", "--remotes", "--verbose",
                 "--no-color", "--color", "-n", "--contains", "--merged", "--no-merged", "--points-at"}
        takes_value = {"--contains", "--merged", "--no-merged", "--points-at"}
        listing, expect_value = False, False
        for a in rest:
            if a.startswith("-"):
                if a not in flags and not a.startswith(("--format=", "--sort=")):
                    raise _Reject("git %s %s" % (sub, a))
                listing = listing or a in ("--list", "-l")
                expect_value = a in takes_value
            elif not (listing or expect_value):
                raise _Reject("git %s <name> changes refs" % sub)
            else:
                expect_value = False
        return
    if sub == "remote":
        if all(a in ("-v", "--verbose") for a in rest) or (rest and rest[0] in ("show", "get-url")):
            return
        raise _Reject("git remote changes configuration")
    if sub == "stash":
        if rest and rest[0] in ("list", "show"):
            return
        raise _Reject("git stash changes state")
    if sub == "worktree":
        if rest and rest[0] == "list":
            return
        raise _Reject("git worktree changes state")
    if sub == "config":
        reads = {"--get", "--get-all", "--get-regexp", "--list", "-l"}
        writes = {"--unset", "--unset-all", "--add", "--replace-all", "--edit", "-e", "--rename-section",
                  "--remove-section"}
        if any(a in reads for a in rest) and not any(a in writes for a in rest):
            return
        raise _Reject("git config writes configuration")
    if sub == "reflog":
        if not rest or rest[0] == "show":
            return
        raise _Reject("git reflog %s" % rest[0])
    raise _Reject("git %s" % sub)


def _check_fetch(rest):
    """`git fetch [--prune|--all|-q|-v] [<remote name>]`: a refspec (or a path or URL as the remote) would let
    the fetch move local branches."""
    positional = 0
    for a in rest:
        if a in FETCH_FLAGS:
            continue
        if a.startswith(("-", "+")) or not REMOTE_NAME.match(a):
            raise _Reject("git fetch %s" % a)
        positional += 1
    if positional > 1:
        raise _Reject("git fetch with a refspec")


def _check_merge(rest):
    """gh pr merge <pr> [--squash|--merge|--rebase|--auto] [--repo R] ...: never --delete-branch (gh would
    switch the orchestrator's working tree to the default branch and delete the local branch)."""
    skip = False
    for a in rest:
        if skip:
            skip = False
            continue
        name, eq, _ = a.partition("=")
        if a.startswith("-"):
            if name in MERGE_FLAGS and not eq:
                continue
            if name in MERGE_VALUE_FLAGS:
                skip = not eq
                continue
            raise _Reject("gh pr merge %s" % a)


def _norm_repo(value):
    """'host/owner/repo' (lowercase, no .git) for OWNER/REPO, HOST/OWNER/REPO, https/ssh URLs and scp-style
    remotes; None if it is none of these."""
    v = (value or "").strip()
    m = (re.match(r"^[a-z][a-z0-9+.-]*://(?:[^@/]+@)?([^/:]+)(?::\d+)?/(.+?)/?$", v, re.I) or
         re.match(r"^(?:[^@/\s]+@)?([^/:\s]+):(?!//)(.+?)/?$", v))
    if m:
        host, path = m.group(1), m.group(2)
    elif v.count("/") == 1 and not v.startswith("/"):
        host, path = "github.com", v
    elif v.count("/") == 2 and not v.startswith("/"):
        host, _, path = v.partition("/")
    else:
        return None
    path = re.sub(r"\.git$", "", path)
    return "%s/%s" % (host.lower(), path.lower()) if path.count("/") == 1 else None


def _git_config_file(folder):
    """The config file of the repository at `folder` (follows a worktree's .git pointer file)."""
    dot = os.path.join(folder, ".git")
    if os.path.isfile(dot):
        with open(dot, "r", encoding="utf-8") as f:
            first = f.readline().strip()
        if not first.startswith("gitdir:"):
            return None
        gitdir = os.path.join(folder, first[len("gitdir:"):].strip())
        common = os.path.join(gitdir, "commondir")
        if os.path.isfile(common):
            with open(common, "r", encoding="utf-8") as f:
                gitdir = os.path.join(gitdir, f.readline().strip())
        return os.path.join(gitdir, "config")
    return os.path.join(dot, "config")


def project_origin(project):
    """Normalised origin remote of the project's repository, read from its .git/config without running
    git. None when anything is unknown (the caller then denies)."""
    try:
        from foreman import projects
        proj = projects.load_project(project) if project else None
        cfg = _git_config_file(proj["path"]) if proj and proj.get("path") else None
        if not cfg:
            return None
        in_origin = False
        with open(cfg, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line.startswith("["):
                    in_origin = re.match(r'^\[remote\s+"origin"\]$', line, re.I) is not None
                elif in_origin and re.match(r"^url\s*=", line, re.I):
                    return _norm_repo(line.split("=", 1)[1])
    except Exception:
        return None
    return None


def _check_gh_arguments(args, project, needs_repo=False):
    """Rules for every gh command: no body files, no @file bodies, and a repo override (-R/--repo, or a PR/
    issue URL) only for the project's own origin, otherwise project data could be published elsewhere."""
    origin = None
    resolved = False

    def same_repo(value):
        nonlocal origin, resolved
        if not resolved:
            origin, resolved = project_origin(project), True
        return origin is not None and _norm_repo(value) == origin

    explicit = False  # the repo is named by a validated -R/--repo or a URL of the origin
    expect = None  # the flag whose value is the next word
    for a in args:
        name = a.split("=", 1)[0]
        if expect:
            flag, expect = expect, None
            if flag in ("-R", "--repo"):
                if not same_repo(a):
                    raise _Reject("gh --repo %s is not this project's origin" % a)
                explicit = True
            elif a.startswith("@"):
                raise _Reject("gh %s @file reads a local file" % flag)
            continue
        if name == "--body-file" or re.match(r"^-[^-]*F", a):
            raise _Reject("gh body files are not allowed")
        if name in ("-R", "--repo"):
            if "=" in a:
                if not same_repo(a.split("=", 1)[1]):
                    raise _Reject("gh --repo %s is not this project's origin" % a)
                explicit = True
            else:
                expect = name
            continue
        if a.startswith("-R") and not a.startswith("--"):
            if not same_repo(a[2:].lstrip("=")):
                raise _Reject("gh %s is not this project's origin" % a)
            explicit = True
            continue
        if name in ("--body", "-b"):
            if "=" in a:
                if a.split("=", 1)[1].startswith("@"):
                    raise _Reject("gh --body @file reads a local file")
            else:
                expect = name
            continue
        if a.startswith("-b") and not a.startswith("--") and a[2:].lstrip("=").startswith("@"):
            raise _Reject("gh -b @file reads a local file")
        if re.match(r"^-[^-R b]", a) and re.search(r"[Rb]", a[2:]):
            # a short-flag cluster such as -aR or -sb hides the repo/body flags behind another letter
            raise _Reject("combined short flags containing -R or -b are not allowed")
        if "://" in a and not a.startswith("-"):
            m = re.match(r"^https?://([^/]+)/([^/]+)/([^/]+)/(?:pull|issues)/\d+/?$", a)
            if not m or not same_repo("%s/%s/%s" % m.groups()):
                raise _Reject("gh URL %s is not this project's origin" % a)
            explicit = True
    if needs_repo and not explicit:
        raise _Reject("gh needs an explicit `-R <this project's origin>` here (without it gh may pick the "
                      "`upstream` remote, often a public repository)")
    if expect in ("-R", "--repo"):
        raise _Reject("gh --repo without a value")


def _check_gh(args, project=None):
    if len(args) >= 2 and args[0] in GH_ALLOWED and args[1] in GH_ALLOWED[args[0]]:
        _check_gh_arguments(args[2:], project, tuple(args[:2]) in GH_NEEDS_EXPLICIT_REPO)
        if args[:2] == ["pr", "merge"]:
            _check_merge(args[2:])
        return
    raise _Reject("gh %s" % " ".join(args[:2]))


def _abbrev_of(arg, option, minimum=3):
    """True if `arg` (--name or --name=value) is `option` or an abbreviation of it (argparse accepts those)."""
    name = arg.split("=", 1)[0]
    return arg.startswith("--") and len(name) >= minimum and option.startswith(name)


def _check_foreman(args):
    if not args:
        raise _Reject("foreman without a subcommand")
    sub, rest = args[0], args[1:]
    if sub.startswith("-"):
        if args in (["--help"], ["-h"], ["--version"]):
            return
        raise _Reject("foreman option %s" % sub)
    if sub not in FOREMAN_SAFE:
        raise _Reject("`foreman %s` is not available to an orchestrator" % sub)
    action = rest[0] if rest else None  # the action must come right after the subcommand, no option before it
    if sub == "board":
        if action not in ("show", "add", "set", "done"):
            raise _Reject("foreman board %s" % (action or ""))
        for a in rest:
            if _abbrev_of(a, "--check", 3) or a == "--check" or re.match(r"^check\s*=", a, re.I):
                raise _Reject("an orchestrator cannot set or run board checks")
    elif sub == "orchestrator":
        if not action or action.startswith("-") or any(name.startswith(action) for name in USER_ONLY_ORCHESTRATOR):
            raise _Reject("`foreman orchestrator %s` is user-only or unknown" % (action or ""))
    elif sub == "watch":
        if action != "list":
            raise _Reject("only `foreman watch list` is allowed")
    elif sub == "clean":
        if not any(_abbrev_of(a, "--dry-run", 3) for a in rest):
            raise _Reject("`foreman clean` needs --dry-run")


def _check_coordination(cmd, args):
    """orca / herdr: the first two words must be exactly an allowed read-only pair (an option placed before
    the subcommand would let its value pass for the subcommand while the tool runs another)."""
    if tuple(args[:2]) not in COORDINATION_READONLY[cmd]:
        raise _Reject("`%s %s` is not a read-only subcommand" % (cmd, " ".join(args[:2])))


def _check_segment(words, project=None):
    while words and ENV_ASSIGN.match(words[0]):
        words = words[1:]
    if not words:
        raise _Reject("nothing to run")
    cmd, args = words[0], words[1:]
    if cmd == "foreman":
        return _check_foreman(args)
    if cmd in COORDINATION_READONLY:
        return _check_coordination(cmd, args)
    if cmd == "git":
        return _check_git(args)
    if cmd == "gh":
        return _check_gh(args, project)
    if cmd not in SIMPLE_COMMANDS:
        raise _Reject("`%s` is not an allowed command" % cmd)
    if cmd == "find" and any(a in FIND_FORBIDDEN for a in args):
        raise _Reject("find with an action that changes files")
    if cmd == "rg" and any(a.startswith(("--pre", "--hostname-bin")) for a in args):
        raise _Reject("rg option that runs a program")
    if cmd == "date" and any(a.startswith("--set") or re.match(r"^-[A-Za-z]*s", a) for a in args):
        raise _Reject("date sets the clock")
    if cmd == "tree" and any(re.match(r"^-[^-]*o", a) or a.startswith("--o") for a in args):
        raise _Reject("tree writes its output to a file")
    if cmd == "file" and any(re.match(r"^-[^-]*C", a) or _abbrev_of(a, "--compile", 3) for a in args):
        raise _Reject("file -C writes a compiled magic file")


def check_command(cmd, project=None):
    """None if the shell command line is allowed for an orchestrator, else the block message.
    Fails closed: anything unexpected while classifying denies the command."""
    try:
        if not isinstance(cmd, str) or not cmd.strip():
            raise _Reject("empty or unreadable command")
        segments = _segments(_scan(cmd))
        if not segments:
            raise _Reject("empty command")
        for words in segments:
            _check_segment(words, project)
    except _Reject as why:
        return _block(str(why), project)
    except Exception as exc:  # fail closed
        return _block("the command could not be classified (%s)" % type(exc).__name__, project)
    return None


def unverifiable(project=None):
    """The block message when the guard itself could not run (fail closed)."""
    return _block("the guard could not verify this call; if this keeps happening the user can repair the "
                  "orchestrator's session record with `foreman orchestrator repair` (run by the user, not by "
                  "you)", project)


def is_allowed_tool(name):
    return isinstance(name, str) and name.lower() in ALLOWED_TOOLS


def check_tool(norm, project=None):
    """Guard a normalised pre_tool event: None = allowed, else the block message. Deny by default and fail
    closed: unknown tools (every mcp__* tool included) and any internal error are denied."""
    try:
        name = norm.get("tool_name")
        low = name.lower() if isinstance(name, str) else ""
        if low in SHELL_TOOLS:
            if not norm.get("command"):
                return _block("%s without a readable command" % name, project)
            return check_command(norm["command"], project)
        if is_allowed_tool(name):
            return None
        return _block("tool %s is not allowed for an orchestrator" % (name or "(none)"), project)
    except Exception as exc:
        return _block("the tool call could not be classified (%s)" % type(exc).__name__, project)
