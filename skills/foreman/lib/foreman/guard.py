"""Orchestrator guard (spec 4.1, architecture 4): an orchestrator may coordinate but not change project
files or git state. `check_tool` / `check_command` return None when the call is allowed, else the block
message that is shown to the agent.

Shell commands are allowed only if every pipeline segment starts with an allowlisted read-only or
coordination command and nothing writes to a file or runs nested code. The command line is scanned with a
small quote-aware scanner (not plain shlex: shlex cannot tell a quoted ">" from a redirection)."""
import re

SHELL_TOOLS = {"bash", "run_command", "shell", "local_shell", "exec_command", "shell_command",
               "execute_command", "run_shell_command", "unified_exec", "container.exec"}
EDIT_MARKERS = ("write", "edit", "patch", "replace", "create_file", "delete")
NOT_EDITING = {"todowrite"}  # bookkeeping tools whose names merely contain a marker

SIMPLE_COMMANDS = {"ls", "cat", "head", "tail", "grep", "rg", "wc", "jq", "echo", "pwd", "sleep", "which",
                   "basename", "dirname", "realpath", "stat", "du", "df", "whoami", "uname", "true", "false",
                   "date", "find"}
COORDINATION = {"foreman", "orca", "herdr"}
FIND_FORBIDDEN = {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls"}
GIT_READONLY = {"status", "log", "diff", "show", "rev-parse", "rev-list", "ls-files", "ls-tree", "ls-remote",
                "cat-file", "blame", "describe", "shortlog", "whatchanged", "diff-tree", "name-rev",
                "merge-base", "for-each-ref", "show-ref", "grep", "check-ignore", "count-objects",
                "show-branch", "cherry", "diff-index", "diff-files", "version"}
# options of the read-only subcommands that write a file or run a program
GIT_FORBIDDEN_OPTIONS = ("--output", "--upload-pack", "--receive-pack", "--exec", "--ext-diff",
                         "--open-files-in-pager", "--textconv")
GIT_GLOBAL_OK = {"--no-pager", "--no-optional-locks", "-P"}
GH_ALLOWED = {"pr": {"view", "list", "create", "merge", "review", "checks", "comment", "diff", "status"},
              "issue": {"view", "list", "create", "comment", "status"},
              "run": {"view", "list", "watch"},
              "repo": {"view", "list"}}
ENV_ASSIGN = re.compile(r"^FOREMAN_[A-Z0-9_]*=")

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
    pending = []  # heredocs waiting for the next newline: (delimiter, dash, quoted)
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
            for delim, dash, quoted in pending:
                body = []
                while i < n:
                    j = cmd.find("\n", i)
                    line = cmd[i:] if j < 0 else cmd[i:j]
                    i = n if j < 0 else j + 1
                    if (line.lstrip("\t") if dash else line) == delim:
                        break
                    body.append(line)
                if not quoted and re.search(r"\$\(|`", "\n".join(body)):
                    raise _Reject("command substitution in a here-document")
            pending = []
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
                if cmd[j] == "`" or cmd.startswith("$(", j):
                    raise _Reject("command substitution")
                part.append(cmd[j])
                j += 1
            if j >= n:
                raise _Reject("unbalanced quote")
            buf.append("".join(part))
            in_word = True
            i = j + 1
        elif c == "`" or cmd.startswith("$(", i):
            raise _Reject("command substitution")
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
            if cmd.startswith("<<<", i):
                toks.append(("redir", "<<<"))
                i += 3
            elif cmd.startswith("<<", i):
                i += 2
                dash = cmd.startswith("-", i)
                if dash:
                    i += 1
                while i < n and cmd[i] in " \t":
                    i += 1
                word, quoted = [], False
                while i < n and cmd[i] not in " \t\n;|&<>()":
                    if cmd[i] in "'\"\\":
                        quoted = True
                    else:
                        word.append(cmd[i])
                    i += 1
                if not word:
                    raise _Reject("malformed here-document")
                pending.append(("".join(word), dash, quoted))
            elif cmd.startswith("<>", i):
                toks.append(("redir", "<>"))
                i += 2
            elif cmd.startswith("<&", i):
                toks.append(("redir", "<&"))
                i += 2
            else:
                toks.append(("redir", "<"))
                i += 1
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


def _check_gh(args):
    if len(args) >= 2 and args[0] in GH_ALLOWED and args[1] in GH_ALLOWED[args[0]]:
        return
    raise _Reject("gh %s" % " ".join(args[:2]))


def _check_segment(words):
    while words and ENV_ASSIGN.match(words[0]):
        words = words[1:]
    if not words:
        raise _Reject("nothing to run")
    cmd, args = words[0], words[1:]
    if cmd in COORDINATION:
        return
    if cmd == "git":
        return _check_git(args)
    if cmd == "gh":
        return _check_gh(args)
    if cmd not in SIMPLE_COMMANDS:
        raise _Reject("`%s` is not an allowed command" % cmd)
    if cmd == "find" and any(a in FIND_FORBIDDEN for a in args):
        raise _Reject("find with an action that changes files")
    if cmd == "rg" and any(a.startswith(("--pre", "--hostname-bin")) for a in args):
        raise _Reject("rg option that runs a program")
    if cmd == "date" and any(a.startswith("--set") or re.match(r"^-[A-Za-z]*s", a) for a in args):
        raise _Reject("date sets the clock")


def check_command(cmd, project=None):
    """None if the shell command line is allowed for an orchestrator, else the block message."""
    try:
        if not isinstance(cmd, str) or not cmd.strip():
            raise _Reject("empty or unreadable command")
        segments = _segments(_scan(cmd))
        if not segments:
            raise _Reject("empty command")
        for words in segments:
            _check_segment(words)
    except _Reject as why:
        return _block(str(why), project)
    return None


def is_editing_tool(name):
    low = (name or "").lower()
    if not low or low in NOT_EDITING:
        return False
    return any(marker in low for marker in EDIT_MARKERS)


def check_tool(norm, project=None):
    """Guard a normalised pre_tool event: None = allowed, else the block message."""
    name = norm.get("tool_name")
    low = (name or "").lower()
    if low in SHELL_TOOLS:
        if not norm.get("command"):
            return _block("%s without a readable command" % name, project)
        return check_command(norm["command"], project)
    if is_editing_tool(name):
        return _block("%s edits files" % name, project)
    return None
