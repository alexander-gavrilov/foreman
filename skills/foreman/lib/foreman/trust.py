"""Narrow trust writes for Antigravity and Codex (spec 5.5).

`foreman init` is the user's consent to trust one folder. This module writes that trust, and nothing wider:
* Antigravity: the folder's resolved path in `trustedWorkspaces` of its settings.json; a worker's worktree at `open`;
* Codex: `[projects."<folder>"] trust_level = "trusted"` and one `[hooks.state."<key>"]` table per foreman hook,
  with the hash Codex itself reports (`hooks/list`); Codex trust is per repository, so `open` only checks it.
Entries are add-only, recorded in `trust.json` (pending before the edit, applied after the read-back), and taken back
only while they are still as foreman wrote them.
"""
import hashlib
import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time

from foreman import initproj, paths, projects, sessions, store, timeutil
from foreman import install as machine
from foreman import trustfile
from foreman.trustfile import TrustEditError

AGENTS = ("antigravity", "codex")
AGY_KEY = "trustedWorkspaces"
CODEX_TIMEOUT_S = 10
CHECK_EVERY_S = 3600
MANUAL = {
    "antigravity": "trust the workspace in Antigravity yourself",
    "codex": "approve the foreman hooks in Codex /hooks",
}


class TrustRefused(Exception):
    """Trust is not written for this agent, and the reason is the message (a user decision, a rule of 5.5, foreign
    content, a file that cannot be edited). `skip` is True for a step that does not apply at all (not a failure)."""

    def __init__(self, reason, skip=False):
        super().__init__(reason)
        self.reason = reason
        self.skip = skip


class RecordError(Exception):
    pass


# ------------------------------------------------------------ where, and whether

def account_home():
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_dir
    except Exception:
        return None


def active():
    """(True, "") when foreman may write trust at all: the state folder is the default one for the HOME in effect, so
    a trial instance with a throwaway state folder cannot leave entries its own record would never remove."""
    if os.path.abspath(paths.home()) != os.path.abspath(paths.default_home()):
        return False, "the state folder is not the default one (a trial instance writes no trust)"
    return True, ""


def agy_settings(home=None):
    return os.path.join(machine.user_home(home), ".gemini", "antigravity-cli", "settings.json")


def codex_home(home=None):
    explicit = os.environ.get("CODEX_HOME")
    return os.path.abspath(os.path.expanduser(explicit)) if explicit else os.path.join(machine.user_home(home), ".codex")


def codex_config(home=None):
    return os.path.join(codex_home(home), "config.toml")


def _inside(path, root):
    path, root = os.path.realpath(path), os.path.realpath(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def reach_ok(target):
    """A file outside the HOME in effect (a CODEX_HOME elsewhere, a link whose target lies outside) is written only
    when HOME is the account's own home: a trial with a redirected HOME must not reach the real files through an
    inherited variable."""
    home = machine.user_home()
    real = trustfile.resolve_target(target)
    if _inside(real, home):
        return
    acct = account_home()
    if not acct or os.path.realpath(acct) != os.path.realpath(home):
        raise TrustRefused("%s lies outside the HOME in effect and HOME is not the account's own home" % real, skip=True)


# ------------------------------------------------------------ the record (trust.json)

def record_file():
    return os.path.join(paths.home(), "trust.json")


def load_record():
    try:
        raw = trustfile.read_bytes(record_file())
    except OSError as exc:
        raise RecordError("trust.json cannot be read (%s)" % exc)
    if raw is None:
        return {"version": 1, "entries": []}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise RecordError("trust.json is not valid JSON; foreman adds and removes nothing until it is fixed")
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise RecordError("trust.json does not have the expected shape; foreman adds and removes nothing")
    return data


def _mutate(fn):
    with store.locked(record_file() + ".rec"):
        data = load_record()
        result = fn(data)
        store.write_json(record_file(), data)
        return result


def entries(file=None, project=None, state=None):
    rows = [e for e in load_record()["entries"] if isinstance(e, dict)]
    return [e for e in rows if (file is None or e.get("file") == file) and (project is None or e.get("project") == project)
            and (state is None or e.get("state") == state)]


def _new_id():
    import secrets
    return "tr-" + secrets.token_hex(4)


def _put(entry):
    """Record `entry`, replacing an earlier record of the same thing (an entry the agent dropped and foreman put back)."""
    def same(e):
        return e.get("id") == entry["id"] or (e.get("file"), e.get("kind"), e.get("value")) == \
            (entry["file"], entry["kind"], entry["value"])

    def fn(data):
        data["entries"] = [e for e in data["entries"] if not same(e)] + [entry]
    _mutate(fn)


def _drop(ids):
    ids = set(ids)

    def fn(data):
        data["entries"] = [e for e in data["entries"] if e.get("id") not in ids]
    _mutate(fn)


def key_created(target):
    """foreman made the `trustedWorkspaces` list of this file (it was not there): the list goes with its last entry."""
    return bool((load_record().get("keys") or {}).get(target))


def _set_key_created(target, flag):
    def fn(data):
        keys = data.setdefault("keys", {})
        if flag:
            keys[target] = True
        else:
            keys.pop(target, None)
    _mutate(fn)


def _set_state(eid, state):
    def fn(data):
        for e in data["entries"]:
            if e.get("id") == eid:
                e["state"] = state
    _mutate(fn)


# ------------------------------------------------------------ consent (a field of the project record)

def consent(slug, agent):
    proj = projects.load_project(slug) or {}
    c = (proj.get("trust_consent") or {}).get(agent)
    return c if isinstance(c, dict) and c.get("granted") else None


def set_consent(slug, agent, granted, waiver=None):
    def fn(proj):
        proj = proj if isinstance(proj, dict) else {}
        tc = proj.get("trust_consent") if isinstance(proj.get("trust_consent"), dict) else {}
        if granted:
            tc[agent] = {"granted": True, "at": timeutil.now_iso()}
            if waiver:
                tc[agent]["waiver"] = waiver
        else:
            tc.pop(agent, None)
        proj["trust_consent"] = tc
        return proj
    store.update_json(paths.project_file(slug), fn, default={})


# ------------------------------------------------------------ who may trigger

def orchestrator_side():
    """The session this command runs in, when it is of the orchestrator's side (an orchestrator, or a worker it
    owns); None for a terminal and for a session the user owns."""
    from foreman import messaging
    rec = messaging.current_session()
    if rec and (rec.get("role") == "orchestrator" or rec.get("owner") == "orchestrator"):
        return rec
    return None


# ------------------------------------------------------------ which paths

def _git_root(path):
    return os.path.lexists(os.path.join(path, ".git"))


def _worktree_list(project_path):
    try:
        out = subprocess.run(["git", "-C", project_path, "worktree", "list", "--porcelain"], capture_output=True,
                             text=True, timeout=30, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return []
    return [os.path.realpath(l.split(" ", 1)[1]) for l in out.stdout.splitlines() if l.startswith("worktree ")]


def check_path(path, slug=None, worktree_of=None, expect=None):
    """The resolved path when trust may be written for it, else TrustRefused (5.5, the path rule). `worktree_of`
    is the project slug for a worker's worktree, `expect` the path the session's record names for it."""
    real = os.path.realpath(os.path.abspath(os.path.expanduser(path)))
    if real == os.sep:
        raise TrustRefused("%s is the root folder" % real)
    home = os.path.realpath(machine.user_home())
    if real == home or home.startswith(real + os.sep):
        raise TrustRefused("%s is the home folder or a folder above it" % real)
    state = os.path.realpath(paths.home())
    if state.startswith(real + os.sep) or real == state:
        raise TrustRefused("%s is the state folder or a folder above it" % real)
    for cfg in (os.path.dirname(agy_settings()), codex_home()):
        cfg = os.path.realpath(cfg)
        if real == cfg or cfg.startswith(real + os.sep):
            raise TrustRefused("%s is an agent's configuration folder or a folder above it" % real)
    if worktree_of:
        wt_root = os.path.realpath(paths.worktrees_dir(worktree_of))
        if os.path.dirname(real) != wt_root:
            raise TrustRefused("%s is not a direct child of the project's worktrees folder" % real)
        if expect is not None and real != os.path.realpath(expect):
            raise TrustRefused("%s is not the worktree the session's record names" % real)
        proj = projects.load_project(worktree_of) or {}
        if real not in _worktree_list(proj.get("path") or ""):
            raise TrustRefused("%s is not listed by `git worktree list` of the project" % real)
        return real
    if real.startswith(state + os.sep):
        raise TrustRefused("%s is inside the state folder" % real)
    for proj in projects.list_projects():
        other = os.path.realpath(proj.get("path") or "")
        if other and other != real and other.startswith(real + os.sep):
            raise TrustRefused("%s contains another registered project (%s)" % (real, proj.get("slug")))
    if not _git_root(real):
        try:
            with os.scandir(real) as it:
                children = [e.path for e in it if e.is_dir()]  # a link to a repository is a repository child
        except OSError as exc:
            raise TrustRefused("%s cannot be listed, so it cannot be told from a folder of repositories (%s)"
                               % (real, exc))
        if any(_git_root(c) for c in children):
            raise TrustRefused("%s is a folder of repositories, not a repository" % real)
    return real


# ------------------------------------------------------------ foreign content: what trust would switch on

def _sha(obj):
    if not isinstance(obj, (bytes, bytearray)):
        obj = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(obj).hexdigest()


def digest(units):
    return _sha(units)


def foreign_agy(folder, fm):
    """The hooks of `folder`'s `.agents/hooks.json` that are not exactly what foreman generates: {unit: sha}."""
    path = os.path.join(folder, initproj.HOOK_FILES["antigravity"])
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError) as exc:
        raise TrustRefused("%s cannot be read (%s)" % (path, exc))
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        raise TrustRefused("%s is not valid JSON, so its hooks cannot be told from foreman's" % path)
    if not isinstance(data, dict):
        return {"file": _sha(data)}
    ours, units = initproj.antigravity_hooks(fm), {}
    for key, value in data.items():
        if key != "foreman" or not isinstance(value, dict):
            units["entry:" + key] = _sha(value)
            continue
        for event, val in value.items():
            if event == "enabled":
                continue
            want = ours.get(event)
            if want is None:
                units["foreman:%s" % event] = _sha(val)
            elif not isinstance(val, list):
                units["foreman:%s:value" % event] = _sha(val)
            else:
                for item in val:
                    if item not in want:
                        units["foreman:%s:%s" % (event, _sha(item)[:16])] = _sha(item)
    return units


def foreign_codex(folder):
    """Everything in `folder/.codex/` except foreman's `hooks.json`: {relative path: sha of its content}."""
    root = os.path.join(folder, ".codex")
    units = {}
    if not os.path.isdir(root) or os.path.islink(root):
        return {".codex": "link"} if os.path.islink(root) else units
    for cur, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(files + [d for d in dirs if os.path.islink(os.path.join(cur, d))]):
            full = os.path.join(cur, name)
            rel = os.path.relpath(full, root)
            if rel == "hooks.json":
                continue
            if os.path.islink(full):
                units[rel] = _sha("link:" + os.readlink(full))
                continue
            h = hashlib.sha256()
            try:
                with open(full, "rb") as f:
                    for chunk in iter(lambda: f.read(1 << 16), b""):
                        h.update(chunk)
            except OSError:
                h.update(b"unreadable")
            units[rel] = h.hexdigest()
        if not files and not dirs and cur != root:
            units[os.path.relpath(cur, root) + "/"] = "empty-dir"
    return units


def foreign(agent, folder, fm):
    return foreign_agy(folder, fm) if agent == "antigravity" else foreign_codex(folder)


def foreign_text(agent, units):
    what = "hooks in .agents/hooks.json that are not foreman's" if agent == "antigravity" else \
        "files in .codex/ besides foreman's hooks.json"
    return "%s (%s)" % (what, ", ".join(sorted(units)[:6]) + (", ..." if len(units) > 6 else ""))


# ------------------------------------------------------------ Antigravity

def agy_state(value):
    """absent | ours | theirs for the workspace entry `value` in the real settings file."""
    target = trustfile.resolve_target(agy_settings())
    try:
        text = trustfile.decode(trustfile.read_bytes(target), target)
        lst = trustfile.json_list(trustfile.parse_json_strict(text, target), AGY_KEY) if text else None
    except TrustEditError as exc:
        raise TrustRefused("%s: %s" % (target, exc))
    except OSError as exc:
        raise TrustRefused("%s cannot be read (%s)" % (target, exc))
    if not lst or value not in lst:
        return "absent"
    mine = any(e.get("value") == value and e.get("file") == target and e.get("kind") == "agy-workspace"
               for e in entries())
    return "ours" if mine else "theirs"


def _agy_new_text(value):
    return '{\n  "%s": [\n    %s\n  ]\n}\n' % (AGY_KEY, json.dumps(value, ensure_ascii=False))


def agy_add(value, project, scope, worktree=None):
    """Add `value` to trustedWorkspaces. Returns "added", or "present" when the entry was already there (the user's, or
    one foreman recorded earlier: left alone, never recorded as new). TrustRefused when the file cannot be edited."""
    if any(ord(c) < 0x20 or ord(c) == 0x7f for c in value):
        raise TrustRefused("a path with a control character cannot be written: %r" % value)
    reach_ok(agy_settings())
    target = trustfile.resolve_target(agy_settings())
    eid = _new_id()
    seen = {}

    def build(text, existed):
        if not existed:
            seen["created_key"] = True
            return _agy_new_text(value)
        lst = trustfile.json_list(trustfile.parse_json_strict(text, target), AGY_KEY)
        if lst is not None and value in lst:
            return None
        seen["created_key"] = lst is None
        return trustfile.json_add_to_list(text, AGY_KEY, value)

    def verify(old, new):
        want = trustfile.parse_json_strict(old, target) if old.strip() else {}
        want = dict(want)
        want[AGY_KEY] = list(want.get(AGY_KEY) or []) + [value]
        if trustfile.parse_json_strict(new, target) != want:
            raise TrustEditError("the edit would change more than the one entry; nothing was written")

    def present(text):
        return value in (trustfile.json_list(trustfile.parse_json_strict(text, target), AGY_KEY) or [])

    def pending():
        _put({"id": eid, "file": target, "kind": "agy-workspace", "value": value, "project": project,
              "scope": scope, "worktree": worktree, "state": "pending", "at": timeutil.now_iso()})

    def applied():
        _set_state(eid, "applied")
        if seen.get("created_key"):
            _set_key_created(target, True)  # per file, not per entry: it decides when the last entry goes

    try:
        load_record()  # an unreadable record: nothing is added
        done = trustfile.edit(target, build, verify, present, before_write=pending,
                              after_applied=applied, on_abort=lambda: _drop([eid]))
    except trustfile.TrustEditWritten as exc:
        raise TrustRefused(str(exc))  # the entry is in the file: its pending record stays
    except TrustEditError as exc:
        _drop_pending_quiet(eid)
        raise TrustRefused(str(exc))
    except (RecordError, OSError) as exc:
        raise TrustRefused(str(exc))
    return "present" if done == "unchanged" else "added"


def _drop_pending_quiet(eid):
    try:
        _drop([eid])
    except Exception:
        pass


def _remove_agy(entry):
    """Take one applied Antigravity entry back. Returns (ok, line)."""
    target, value = entry["file"], entry["value"]
    created = key_created(target)
    seen = {}

    def build(text, existed):
        if not existed:
            return None
        lst = trustfile.json_list(trustfile.parse_json_strict(text, target), AGY_KEY)
        if not lst or value not in lst:
            seen["gone"] = True
            return None
        seen["drops_key"] = created and len(lst) == 1
        new = trustfile.json_remove_from_list(text, AGY_KEY, value, drop_key_if_empty=created)
        if new is None:
            raise TrustEditError("the entry could not be located in the text")
        return new

    def verify(old, new):
        want = dict(trustfile.parse_json_strict(old, target))
        lst = list(want[AGY_KEY])
        lst.pop(len(lst) - 1 - lst[::-1].index(value))  # one occurrence: a copy somebody else added stays
        if lst or not created:
            want[AGY_KEY] = lst
        else:
            del want[AGY_KEY]
        if trustfile.parse_json_strict(new, target) != want:
            raise TrustEditError("the edit would change more than the one entry; nothing was written")

    def gone(text):
        lst = trustfile.json_list(trustfile.parse_json_strict(text, target), AGY_KEY) or []
        return len(lst) < seen.get("before", 1 << 30)

    def count_before(text, existed):
        lst = trustfile.json_list(trustfile.parse_json_strict(text, target), AGY_KEY) if existed and text.strip() else []
        seen["before"] = len(lst or [])
        return build(text, existed)

    try:
        trustfile.edit(target, count_before, verify, gone)
    except TrustEditError as exc:
        return False, "left the Antigravity trust entry %s: %s" % (value, exc)
    if seen.get("drops_key"):
        _set_key_created(target, False)
    return True, "removed the Antigravity trust entry %s" % value


# ------------------------------------------------------------ Codex: asking Codex

class CodexError(Exception):
    pass


def codex_binary():
    return shutil.which("codex")


def hooks_list(folder, home_dir, timeout=None):
    """Ask `codex app-server` (hooks/list, no login needed) about `folder`, with its Codex home at `home_dir`.
    Returns the hook entries. CodexError when codex is missing, silent for `timeout` seconds, or answers badly."""
    timeout = timeout or CODEX_TIMEOUT_S
    binary = codex_binary()
    if not binary:
        raise CodexError("codex is not installed")
    env = dict(os.environ, CODEX_HOME=home_dir)
    try:
        proc = subprocess.Popen([binary, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, env=env, text=True, cwd=home_dir)
    except OSError as exc:
        raise CodexError("codex could not be started (%s)" % exc)
    lines = queue.Queue()

    def pump():
        try:
            for line in proc.stdout:
                lines.put(line)
        finally:
            lines.put(None)
    threading.Thread(target=pump, daemon=True).start()

    def send(obj):
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    def wait_for(rid, deadline):
        while True:
            try:
                line = lines.get(timeout=max(0.05, deadline - time.time()))
            except queue.Empty:
                raise CodexError("codex did not answer within %d seconds" % timeout)
            if line is None:
                raise CodexError("codex exited without an answer")
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("id") == rid:
                if "error" in msg:
                    raise CodexError("codex answered with an error: %s" % str(msg["error"])[:120])
                return msg.get("result")
    deadline = time.time() + timeout
    try:
        send({"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "foreman", "title": "foreman",
                                                                         "version": "1"}}})
        wait_for(1, deadline)
        send({"method": "initialized"})
        send({"id": 2, "method": "hooks/list", "params": {"cwds": [folder]}})
        result = wait_for(2, deadline)
    except (OSError, ValueError) as exc:
        raise CodexError("codex could not be asked (%s)" % exc)
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
        proc.kill()
        proc.wait()
        for pipe in (proc.stdout,):
            try:
                pipe.close()
            except OSError:
                pass
    data = (result or {}).get("data") if isinstance(result, dict) else None
    if not isinstance(data, list):
        raise CodexError("codex answered in a shape foreman does not know")
    for entry in data:
        if isinstance(entry, dict) and os.path.realpath(entry.get("cwd") or "") == os.path.realpath(folder):
            return [h for h in entry.get("hooks") or [] if isinstance(h, dict)]
    return []


def working(h):
    """Codex reports the hook trusted and it is not switched off (a hook with enabled = false is the user's decision)."""
    return h.get("trustStatus") == "trusted" and h.get("enabled") is not False


def codex_expected(fm):
    """foreman's Codex hooks as generated now: {Event: (command, timeout, matcher)} (SessionEnd has no timeout:
    Codex's default is 600 seconds)."""
    return {event: (initproj._cmd(fm, "codex", event), 10 if event != "SessionEnd" else 600,
                    "*" if event in initproj.TOOL_EVENTS else None) for event in initproj.CODEX_EVENTS}


def main_checkout(folder):
    """The main checkout of the repository `folder` belongs to (`folder` itself unless it is a linked worktree)."""
    real = os.path.realpath(folder)
    try:
        out = subprocess.run(["git", "-C", real, "rev-parse", "--git-common-dir"], capture_output=True, text=True,
                             timeout=30, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return real
    common = out.stdout.strip()
    if out.returncode != 0 or not common:
        return real
    common = os.path.realpath(os.path.join(real, common))
    return os.path.dirname(common) if os.path.basename(common) == ".git" else real


def classify(hooks, folder, fm):
    """Split what Codex lists for `folder` into foreman's hooks ({Event: hook}) and look-alikes (listed in a slot of
    the project's hook file but not equal to what foreman generates: never approved)."""
    want = codex_expected(fm)
    # in a linked worktree Codex reads the main checkout's hook file (measured, 5.5)
    hook_file = os.path.join(main_checkout(folder), initproj.HOOK_FILES["codex"])
    mine, lookalike = {}, []
    for h in hooks:
        if h.get("source") != "project" or os.path.realpath(h.get("sourcePath") or "") != hook_file:
            continue
        name = h.get("eventName") or ""
        event = name[:1].upper() + name[1:]
        exp = want.get(event)
        if exp and (h.get("command"), h.get("timeoutSec"), h.get("matcher")) == exp and event not in mine:
            mine[event] = h
        elif initproj._FOREMAN_HANDLER.search(h.get("command") or ""):
            lookalike.append(h)
    return mine, lookalike


def scratch_hooks(folder, fm):
    """The hook keys and hashes Codex itself reports for `folder` when it is trusted, asked with a Codex home that
    holds only that one entry (the user's own config.toml is not involved)."""
    scratch = tempfile.mkdtemp(prefix="foreman-codex-")
    try:
        with open(os.path.join(scratch, "config.toml"), "w", encoding="utf-8") as f:
            f.write(trustfile.toml_table_text(["projects", folder], {"trust_level": "trusted"}))
        return classify(hooks_list(folder, scratch), folder, fm)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def real_hooks(folder, fm):
    """What Codex reports for `folder` in the user's real Codex home (read only; Codex may create files there)."""
    return classify(hooks_list(folder, codex_home()), folder, fm)


def _file_sig(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def codex_config_state(folder):
    """(trust_level or None, {hooks.state key: table}, error) from the real config.toml."""
    target = trustfile.resolve_target(codex_config())
    try:
        raw = trustfile.read_bytes(target)
        cfg = trustfile.parse_toml(trustfile.decode(raw, target), target) if raw is not None else {}
    except TrustEditError as exc:
        raise TrustRefused(str(exc))
    except OSError as exc:
        raise TrustRefused("%s cannot be read (%s)" % (target, exc))
    projects_t, hooks_t = cfg.get("projects", {}), cfg.get("hooks", {})
    if not isinstance(projects_t, dict) or not isinstance(hooks_t, dict) or \
            not isinstance(hooks_t.get("state", {}), dict):
        raise TrustRefused("%s has a `projects` or `hooks` entry that is not a table" % target)
    row = projects_t.get(folder)
    level = row.get("trust_level") if isinstance(row, dict) else None
    return level, dict(hooks_t.get("state") or {})


def _table_ok(table, current_hash):
    return isinstance(table, dict) and table.get("trusted_hash") == current_hash and table.get("enabled") is not False


def _codex_blocks(folder, hooks, project_trusted):
    blocks = []
    if not project_trusted:
        blocks.append(("project", folder, {"trust_level": "trusted"},
                       trustfile.toml_table_text(["projects", folder], {"trust_level": "trusted"})))
    for event in initproj.CODEX_EVENTS:
        h = hooks[event]
        values = {"enabled": True, "trusted_hash": h["currentHash"]}
        blocks.append(("hook", h["key"], values, trustfile.toml_table_text(["hooks", "state", h["key"]], values)))
    return blocks


def codex_add(folder, project, hooks, fm):
    """Write the Codex trust for `folder`: its [projects] entry (unless trusted already) and one hook table per foreman
    hook (unless already trusted with that hash). Returns the list of what was written. TrustRefused on any problem."""
    reach_ok(codex_config())
    if trustfile.toml_module() is None:
        raise TrustRefused("Python 3.11 or newer is needed to edit Codex's config.toml", skip=True)
    target = trustfile.resolve_target(codex_config())
    ids = {}
    added = {}

    def build(text, existed):
        cfg = trustfile.parse_toml(text, target) if text.strip() else {}
        for key in ("projects", "hooks"):
            if not isinstance(cfg.get(key, {}), dict):
                raise TrustEditError("`%s` in config.toml is not a table" % key)
        if not isinstance(cfg.get("hooks", {}).get("state", {}), dict):
            raise TrustEditError("`hooks.state` in config.toml is not a table")
        row = cfg.get("projects", {}).get(folder)
        if row is not None:
            if not isinstance(row, dict):
                raise TrustEditError("the [projects] entry for %s is not a table" % folder)
            if row.get("trust_level") == "untrusted":
                raise TrustEditError("Codex marks %s untrusted: that is your decision, foreman keeps it" % folder)
            if row.get("trust_level") != "trusted":
                raise TrustEditError("the [projects] entry for %s has no trust_level = \"trusted\"; foreman never "
                                     "edits an entry that is already there, add it by hand" % folder)
        state = cfg.get("hooks", {}).get("state", {})
        blocks = _codex_blocks(folder, hooks, row is not None)
        todo = []
        for kind, key, values, block in blocks:
            have = state.get(key) if kind == "hook" else None
            if kind == "hook" and have is not None:
                if isinstance(have, dict) and have.get("trusted_hash") == values["trusted_hash"] \
                        and have.get("enabled") is not False:
                    continue
                continue  # a table that is there is never touched (the caller reports a modified hook)
            todo.append((kind, key, values, block))
        if not todo:
            added.clear()
            return None
        new, texts = trustfile.toml_append_tables(text, [b[3] for b in todo])
        added.clear()  # the plan is made again on every try: a table somebody added meanwhile is not ours
        for (kind, key, values, _), t in zip(todo, texts):
            added[(kind, key)] = (values, t)
        return new

    def expect(old):
        want = json.loads(json.dumps(trustfile.parse_toml(old, target) if old.strip() else {}))
        for (kind, key), (values, _) in added.items():
            if kind == "project":
                want.setdefault("projects", {})[key] = dict(values)
            else:
                want.setdefault("hooks", {}).setdefault("state", {})[key] = dict(values)
        return want

    def verify(old, new):
        try:
            got = json.loads(json.dumps(trustfile.parse_toml(new, target)))
        except TrustEditError as exc:
            raise TrustEditError("the edit would not parse (%s); add the trust by hand" % exc)
        if got != expect(old):
            raise TrustEditError("the edit would change more than the added tables; nothing was written")

    def present(text):
        got = trustfile.parse_toml(text, target)
        for (kind, key), (values, _) in added.items():
            cur = got.get("projects", {}).get(key) if kind == "project" else got.get("hooks", {}).get("state", {}).get(key)
            if cur != values:
                return False
        return True

    def pending():
        stale = [ids.pop(k) for k in list(ids) if k not in added]  # planned by an earlier try, not written by this one
        _drop_pending_quiet_many(stale)
        for (kind, key), (values, text) in added.items():
            eid = ids.setdefault((kind, key), _new_id())
            _put({"id": eid, "file": target, "kind": "codex-" + kind, "value": key, "values": values, "text": text,
                  "project": project, "scope": "project", "worktree": None, "state": "pending",
                  "at": timeutil.now_iso()})

    def applied():
        for eid in ids.values():
            _set_state(eid, "applied")

    try:
        load_record()
        done = trustfile.edit(target, build, verify, present, before_write=pending, after_applied=applied,
                              on_abort=lambda: _drop(list(ids.values())))
    except trustfile.TrustEditWritten as exc:
        raise TrustRefused(str(exc))  # the tables are in the file: their pending records stay
    except TrustEditError as exc:
        _drop_pending_quiet_many(list(ids.values()))
        raise TrustRefused(str(exc))
    except (RecordError, OSError) as exc:
        raise TrustRefused(str(exc))
    return [] if done == "unchanged" else ["%s %s" % (k, v) for (k, v) in sorted(added)]


def _drop_pending_quiet_many(ids):
    if ids:
        try:
            _drop(ids)
        except Exception:
            pass


def _remove_codex(entry):
    """Take one applied Codex table back, only while it still holds exactly what foreman wrote."""
    target = entry["file"]
    seen = {}

    def tables(cfg):
        if entry["kind"] == "codex-project":
            return cfg.get("projects", {}), entry["value"]
        return cfg.get("hooks", {}).get("state", {}), entry["value"]

    def build(text, existed):
        if not existed:
            return None
        cfg = trustfile.parse_toml(text, target)
        holder, key = tables(cfg)
        cur = holder.get(key) if isinstance(holder, dict) else None
        if cur is None:
            seen["gone"] = True
            return None
        if cur != entry.get("values"):
            raise TrustEditError("the table was changed since foreman wrote it; it stays")
        new = trustfile.toml_remove_text(text, entry.get("text") or "")
        if new is None:
            raise TrustEditError("the table is not in the text foreman wrote; it stays")
        return new

    def verify(old, new):
        want = json.loads(json.dumps(trustfile.parse_toml(old, target)))
        holder, key = tables(want)
        holder.pop(key, None)
        got = json.loads(json.dumps(trustfile.parse_toml(new, target)))
        if _prune(got) != _prune(want):
            raise TrustEditError("the edit would change more than the one table; nothing was written")

    def gone(text):
        cfg = trustfile.parse_toml(text, target)
        holder, key = tables(cfg)
        return not (isinstance(holder, dict) and key in holder)

    try:
        trustfile.edit(target, build, verify, gone)
    except TrustEditError as exc:
        return False, "left the Codex trust table %s: %s" % (entry["value"], exc)
    return True, "removed the Codex trust table %s" % entry["value"]


def _prune(d):
    """A parsed config with empty tables dropped (removing the last table of a parent leaves no empty parent)."""
    if isinstance(d, dict):
        out = {k: _prune(v) for k, v in d.items()}
        return {k: v for k, v in out.items() if v != {}}
    return d


# ------------------------------------------------------------ taking entries back

def _remove_entries(rows):
    """Remove these record entries from the agents' files. Returns (lines, remaining). Only *applied* entries are
    removed from a file; a pending one is reported and dropped (foreman cannot know it was not the user's)."""
    lines, drop = [], []
    for e in rows:
        if e.get("state") == "pending":
            lines.append("left the %s %s: foreman could not tell whether it was already yours (a crash left it "
                         "pending); remove it by hand if it is not" % (e.get("kind"), e.get("value")))
            drop.append(e["id"])
            continue
        try:
            ok, line = (_remove_agy if e.get("kind") == "agy-workspace" else _remove_codex)(e)
        except Exception as exc:  # one entry failing must not stop the others
            ok, line = False, "left the %s %s: %s" % (e.get("kind"), e.get("value"), exc)
        lines.append(line)
        if ok:
            drop.append(e["id"])
    if drop:
        _drop(drop)
    return lines


def _guarded(fn, *args):
    try:
        return fn(*args)
    except RecordError as exc:
        return ["trust entries not touched: %s" % exc]


def remove_worktree(path):
    """The entries foreman added for the worktree `path` (it is being removed)."""
    def go():
        real = os.path.realpath(path)
        return _remove_entries([e for e in entries() if e.get("scope") == "worktree"
                                and os.path.realpath(e.get("worktree") or "") == real])
    return _guarded(go)


def remove_project(slug, in_use=None):
    """`init --remove`: the project's entries, and those of its kept worktrees that have no open session. Consent is
    withdrawn. `in_use` is the set of worktree paths with an open session."""
    def go():
        keep = {os.path.realpath(p) for p in (in_use or ())}
        rows = [e for e in entries(project=slug)
                if e.get("scope") != "worktree" or os.path.realpath(e.get("worktree") or "") not in keep]
        return _remove_entries(rows)
    lines = _guarded(go)
    for agent in AGENTS:
        try:
            set_consent(slug, agent, False)
        except Exception:
            pass
    return lines


def remove_all():
    """`uninstall`: every entry foreman added, and every consent."""
    lines = _guarded(lambda: _remove_entries(entries()))
    for proj in projects.list_projects():
        for agent in AGENTS:
            if (proj.get("trust_consent") or {}).get(agent):
                try:
                    set_consent(proj["slug"], agent, False)
                except Exception:
                    pass
    return lines


def drop_backups_if_equal():
    """`uninstall`: a `<file>.foreman-bak` of an agent file is deleted only when the file equals it again (nothing of
    foreman's is left in it). Returns the lines."""
    lines = []
    for source in (agy_settings(), codex_config()):
        target = trustfile.resolve_target(source)
        backup = target + trustfile.BACKUP_SUFFIX
        try:
            with trustfile.file_lock(target):
                saved = trustfile.read_bytes(backup)
                if saved is not None and saved == trustfile.read_bytes(target):
                    os.unlink(backup)
                    lines.append("removed the backup %s (the file is as it was)" % backup)
        except OSError:
            continue
    return lines


def remaining():
    """Entries still recorded (a full purge refuses while any could not be removed)."""
    try:
        return entries()
    except RecordError as exc:
        return [{"kind": "record", "value": str(exc)}]


def remove_missing_worktrees():
    """The scheduled check: entries recorded for worktrees whose folder is gone."""
    def go():
        return _remove_entries([e for e in entries() if e.get("scope") == "worktree"
                                and not os.path.isdir(e.get("worktree") or "")])
    return _guarded(go)


# ------------------------------------------------------------ waiver

def _waiver_units(units):
    return {"digest": digest(units), "units": dict(units)}


def user_entry_still_there(slug, agent, folder, fm):
    """The user's own trust, for the content they were shown, is still in place (a waiver lapses when it is not)."""
    try:
        if agent == "antigravity":
            return agy_state(os.path.realpath(folder)) == "theirs"
        mine, _ = real_hooks(folder, fm)
        return len(mine) == len(initproj.CODEX_EVENTS) and all(working(h) for h in mine.values()) and \
            not any(e.get("kind") == "codex-hook" and e.get("project") == slug for e in entries())
    except (TrustRefused, CodexError, RecordError):
        return False


def content_allowed(slug, agent, folder, units, fm, project_folder, trial=False):
    """None when the launched agent may open in `folder` with this foreign content; else the reason it may not."""
    if not units:
        return None
    c = consent(slug, agent) or {}
    waiver = c.get("waiver")
    if not waiver and trial:
        return "%s holds %s; foreman does not trust a folder with content its trust would switch on. Remove it (a " \
               "trial instance cannot waive it)" % (folder, foreign_text(agent, units))
    if not waiver:
        return "%s holds %s; foreman does not trust a folder with content its trust would switch on. Remove it, or " \
               "trust the folder by hand and run `foreman init` again" % (folder, foreign_text(agent, units))
    if not user_entry_still_there(slug, agent, project_folder, fm):
        return "your own trust entry for %s is gone, so the content you reviewed is not covered any more; run " \
               "`foreman init` again" % project_folder
    shown = waiver.get("units") or {}
    new = sorted(u for u, h in units.items() if shown.get(u) != h)
    if new:
        return "%s holds content that is new or changed since `foreman init` showed it (%s); review it and run " \
               "`foreman init` again" % (folder, ", ".join(new[:6]))
    return None


# ------------------------------------------------------------ init

def _is_linked_worktree(path):
    try:
        git = lambda *a: subprocess.run(["git", "-C", path] + list(a), capture_output=True, text=True, timeout=30,
                                        stdin=subprocess.DEVNULL).stdout.strip()
        a, b = git("rev-parse", "--git-dir"), git("rev-parse", "--git-common-dir")
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(a and b) and os.path.realpath(os.path.join(path, a)) != os.path.realpath(os.path.join(path, b))


def _init_agy(slug, path, fm, out):
    real = check_path(path)
    units = foreign_agy(real, fm)
    state = agy_state(real)
    if units:
        if state == "theirs":
            out["changes"].append("Antigravity: %s is already trusted by you and holds %s; foreman wrote nothing and "
                                  "allows only this content (a digest of it is recorded)" % (real, foreign_text(
                                      "antigravity", units)))
            return _waiver_units(units)
        if state == "ours":
            raise TrustRefused("%s now holds %s while foreman's own trust entry is in place; foreman opens no Antigravity "
                               "worker here until that is gone. To trust the content yourself: `foreman init --remove`, "
                               "trust the folder by hand, then `foreman init`" % (real, foreign_text("antigravity", units)),
                               )
        raise TrustRefused("%s holds %s. Trusting a folder with such content is your decision: trust it in Antigravity "
                           "yourself, then run `foreman init` again" % (real, foreign_text("antigravity", units)))
    if state == "absent":
        agy_add(real, slug, "project")
        out["changes"].append("Antigravity: trusted %s (a trusted workspace runs every hook file it has; foreman's "
                              "recorded entry)" % real)
    return None


def _init_codex(slug, path, fm, out):
    real = check_path(path)
    if _is_linked_worktree(real):
        raise TrustRefused("this is a linked worktree: Codex trust is the main checkout's, run `foreman init` there",
                           skip=True)
    if trustfile.toml_module() is None:
        raise TrustRefused("Python 3.11 or newer is needed to edit Codex's config.toml; approve the foreman hooks in "
                           "Codex /hooks", skip=True)
    if not codex_binary():
        raise TrustRefused("codex is not installed; approve the foreman hooks in Codex /hooks once it is", skip=True)
    reach_ok(codex_config())
    level, state = codex_config_state(real)  # an invalid or odd config.toml is reported before Codex is asked
    units = foreign_codex(real)
    try:
        mine_real, look = real_hooks(real, fm)
    except CodexError as exc:
        raise TrustRefused("%s; the hooks still need approval in Codex /hooks" % exc, skip=True)
    recorded = any(e.get("kind") == "codex-hook" and e.get("project") == slug for e in entries())
    if units:
        user_trusted = len(mine_real) == len(initproj.CODEX_EVENTS) and not recorded and \
            all(working(h) for h in mine_real.values())
        if user_trusted:
            out["changes"].append("Codex: %s is already trusted by you and holds %s; foreman wrote nothing and allows "
                                  "only this content (a digest of it is recorded)" % (real, foreign_text("codex", units)))
            return _waiver_units(units)
        if recorded:
            raise TrustRefused("%s now holds %s while foreman's own Codex trust is in place; foreman opens no Codex "
                               "worker here until that is gone. To trust the content yourself: `foreman init --remove`, "
                               "approve the hooks in Codex /hooks, then `foreman init`" % (real, foreign_text("codex", units)))
        raise TrustRefused("%s holds %s. Trusting such a repository is your decision: trust it in Codex yourself "
                           "(its hooks in /hooks), then run `foreman init` again" % (real, foreign_text("codex", units)))
    if level == "untrusted":
        raise TrustRefused("Codex marks %s untrusted: that is your decision and foreman keeps it" % real)
    try:
        hook_file = os.path.join(real, initproj.HOOK_FILES["codex"])
        before = _file_sig(hook_file)
        wanted, wlook = scratch_hooks(real, fm)
        if before != _file_sig(hook_file):  # the hook file changed under the query: ask again once
            wanted, wlook = scratch_hooks(real, fm)
    except CodexError as exc:
        raise TrustRefused("%s; the hooks still need approval in Codex /hooks" % exc, skip=True)
    if not wanted:
        raise TrustRefused("Codex reports none of foreman's hooks for %s; they still need approval in Codex /hooks" % real,
                           skip=True)
    if len(wanted) != len(initproj.CODEX_EVENTS):
        missing = sorted(set(initproj.CODEX_EVENTS) - set(wanted))
        raise TrustRefused("Codex does not report foreman's hook for %s as foreman generates it; nothing was approved "
                           "(%s)" % (", ".join(missing), "approve them in Codex /hooks"))
    if wlook:
        raise TrustRefused("%d hook(s) in %s look like foreman's but are not what foreman generates; nothing was "
                           "approved" % (len(wlook), initproj.HOOK_FILES["codex"]))
    # a table foreman recorded whose key or hash is no longer right is replaced; one it did not add is never touched
    keys = {h["key"]: h["currentHash"] for h in wanted.values()}
    recorded_keys = {e["value"] for e in entries(project=slug) if e.get("kind") == "codex-hook"}
    clash = sorted(k for k, h in keys.items() if k in state and k not in recorded_keys
                   and not _table_ok(state[k], h))
    if clash:
        raise TrustRefused("config.toml already has a table of yours for a foreman hook with another hash or switched "
                           "off (%s); foreman writes nothing: approve the hook in Codex /hooks from the main checkout, "
                           "or delete that table from config.toml and run `foreman init` again" % ", ".join(clash))
    stale = [e for e in entries(project=slug) if e.get("kind") == "codex-hook" and e.get("state") == "applied"
             and (e["value"] not in keys or (e.get("values") or {}).get("trusted_hash") != keys[e["value"]])]
    for line in _remove_entries(stale):
        out["changes"].append("Codex: " + line)
    wrote = codex_add(real, slug, wanted, fm)
    if wrote:
        out["changes"].append("Codex: trusted %s and approved %d foreman hooks in config.toml (trust_level = trusted "
                              "turns on the project's whole .codex config layer; foreman's recorded entries)"
                              % (real, sum(1 for w in wrote if w.startswith("hook"))))
    try:
        mine, _ = real_hooks(real, fm)
    except CodexError as exc:
        out["notes"].append("Codex: could not confirm the result (%s)" % exc)
        return None
    bad = sorted(e for e, h in mine.items() if not working(h))
    if bad or len(mine) != len(initproj.CODEX_EVENTS):
        raise TrustRefused("Codex does not report foreman's hooks trusted afterwards (%s): approve the hook in Codex "
                           "/hooks from the main checkout, or delete that table from config.toml and run `foreman init` "
                           "again" % (", ".join(bad) or "some are not listed"))
    return None


def run_init(project, path, agents, fm):
    """The trust step of `foreman init`. Returns (changes, notes, failures). Consent is set or withdrawn per agent."""
    out = {"changes": [], "notes": [], "failures": []}
    slug = project["slug"]
    ok, why = active()
    targets = [a for a in agents if a in AGENTS and initproj._has_foreman_hooks(path, a)]
    if not ok:
        if targets:
            out["notes"].append("trust not written: %s; %s" % (why, "; ".join(MANUAL[a] for a in targets)))
        return out
    try:
        load_record()
    except RecordError as exc:
        out["failures"].append(str(exc))
        return out
    for agent in targets:
        try:
            waiver = (_init_agy if agent == "antigravity" else _init_codex)(slug, path, fm, out)
            set_consent(slug, agent, True, waiver)
        except TrustRefused as exc:
            label = "Antigravity" if agent == "antigravity" else "Codex"
            if exc.skip:
                out["notes"].append("%s: trust not written: %s" % (label, exc.reason))
            else:
                out["failures"].append("%s: trust refused: %s (by hand: %s)" % (label, exc.reason, MANUAL[agent]))
            try:
                set_consent(slug, agent, False)
            except Exception:
                pass
        except (TrustEditError, CodexError, OSError, ValueError) as exc:
            out["failures"].append("%s: trust not written: %s (by hand: %s)" % (agent, exc, MANUAL[agent]))
    # `init` repairs the entries of live worktrees too
    for rec in sessions.list_sessions(slug):
        wt = rec.get("worktree")
        if wt and sessions.is_open(rec) and consent(slug, "antigravity"):
            try:
                note = ensure_worktree(slug, wt["path"], "antigravity", fm, launched=False)
                if note:
                    out["changes"].append(note)
            except TrustRefused as exc:
                out["notes"].append("Antigravity: worktree %s not trusted: %s" % (wt["path"], exc.reason))
    return out


# ------------------------------------------------------------ worktrees: open and rotate

def has_agy_hooks(folder):
    try:
        return initproj._has_foreman_hooks(folder, "antigravity")
    except Exception:
        return False


def ensure_worktree(slug, wt_path, agent, fm, launched, expect=None):
    """Trust the worktree for Antigravity (add-only, idempotent). Returns a one-line note when something was added,
    "" when nothing changed. TrustRefused when it may not or cannot be done."""
    real = check_path(wt_path, worktree_of=slug, expect=expect or wt_path)
    if not has_agy_hooks(real):
        return ""
    proj = projects.load_project(slug) or {}
    units = foreign_agy(real, fm)
    why = content_allowed(slug, "antigravity", real, units, fm, proj.get("path") or "")
    if why:
        raise TrustRefused(why)
    if agy_add(real, slug, "worktree", worktree=real) == "added":
        return "Antigravity: trusted the worktree %s" % real
    return ""


class Launch:
    """What `open` or `rotate` added for one launch: taken back when the launch fails after the write."""

    def __init__(self):
        self.notes = []
        self.added = []

    def rollback(self):
        for path in self.added:
            try:
                remove_worktree(path)
            except Exception:
                pass


def prepare_launch(slug, agent, cwd, worktree=None, expect=None):
    """The trust step of `open` and `rotate`, before the agent is launched in `cwd` (a worktree, or the project folder).
    Checks consent and the folder's content for the launched agent, adds the worktree's Antigravity entry. Returns a
    Launch; raises TrustRefused for the launched agent's problem (a problem of another agent is a note)."""
    launch = Launch()
    ok, _ = active()
    fm = machine.foreman_bin(None)
    proj = projects.load_project(slug) or {}
    base = proj.get("path") or cwd
    if not ok:
        # a trial instance writes no trust and asks for no consent, but the content check costs nothing: foreign content
        # means no session for the launched agent whatever consent says (4.2, 5.5)
        if agent in AGENTS:
            why = content_allowed(slug, agent, cwd, foreign(agent, cwd, fm), fm, base, trial=True)
            if why:
                raise TrustRefused(why)
        return launch
    if agent in AGENTS:
        try:
            load_record()  # an unreadable record: nothing is added, and nothing is opened that needs trust
        except RecordError as exc:
            raise TrustRefused(str(exc)) from None
        if not consent(slug, agent):
            raise TrustRefused("the project has no consent for %s trust: run `foreman init` in %s" % (agent, base))
        units = foreign(agent, cwd, fm)
        why = content_allowed(slug, agent, cwd, units, fm, base)
        if why:
            raise TrustRefused(why)
        if agent == "codex":
            try:
                mine, _ = real_hooks(cwd, fm)
            except CodexError as exc:
                raise TrustRefused("Codex's hook trust could not be checked (%s)" % exc)
            if len(mine) != len(initproj.CODEX_EVENTS) or not all(working(h) for h in mine.values()):
                raise TrustRefused("Codex does not report foreman's hooks trusted for %s: run `foreman init` again in %s"
                                   % (cwd, base))
    if worktree:
        launched = agent == "antigravity"
        if launched or consent(slug, "antigravity"):
            try:
                note = ensure_worktree(slug, worktree["path"], "antigravity", fm, launched, expect=expect)
                if note:
                    launch.notes.append(note)
                    launch.added.append(worktree["path"])
            except TrustRefused as exc:
                if launched:
                    raise
                launch.notes.append("Antigravity: the worktree is not trusted (%s)" % exc.reason)
        elif has_agy_hooks(worktree["path"]):
            launch.notes.append("Antigravity: the project has no consent recorded; run `foreman init` (workers of "
                                "that agent are not held up)")
    return launch


# ------------------------------------------------------------ the scheduled check

def drift(slug, fm=None):
    """Messages about trust that is not working for a project, read only."""
    fm = fm or machine.foreman_bin(None)
    proj = projects.load_project(slug) or {}
    path = proj.get("path") or ""
    msgs = []
    if not path or not os.path.isdir(path):
        return msgs
    folders = [path] + [r["worktree"]["path"] for r in sessions.list_sessions(slug)
                        if r.get("worktree") and sessions.is_open(r) and os.path.isdir(r["worktree"]["path"])]
    for e in entries(project=slug, state="applied"):
        if e.get("kind") == "agy-workspace":
            try:
                still = agy_state(e["value"]) != "absent"
            except TrustRefused:
                continue
            if not still:
                msgs.append("Antigravity dropped the trust entry for %s (run `foreman init` to put it back)" % e["value"])
    for agent in AGENTS:
        c = consent(slug, agent)
        if not c:
            continue
        for folder in folders:
            try:
                units = foreign(agent, folder, fm)
            except TrustRefused:
                continue
            why = content_allowed(slug, agent, folder, units, fm, path) if units else None
            if why:
                msgs.append("%s: %s" % (agent, why))
        if agent == "codex" and codex_binary():
            try:
                mine, _ = real_hooks(path, fm)
            except CodexError:
                continue
            bad = sorted(e for e, h in mine.items() if not working(h))
            if bad or len(mine) != len(initproj.CODEX_EVENTS):
                msgs.append("Codex does not report foreman's hooks trusted for %s (%s); run `foreman init`" %
                            (path, ", ".join(bad) or "not listed"))
    return msgs


def scheduled_check(state, now=None):
    """Once an hour per project: repeat the checks, report each drift once per occurrence. `state` is the watch state
    (mutated). Returns [(slug, message)] that are new."""
    ok, _ = active()
    if not ok:
        return []
    now = now or time.time()
    seen = state.setdefault("trust_check", {})
    fresh = []
    for proj in projects.list_projects():
        slug = proj["slug"]
        if not any(consent(slug, a) for a in AGENTS):
            continue
        row = seen.setdefault(slug, {})
        if now - float(row.get("at") or 0) < CHECK_EVERY_S:
            continue
        row["at"] = now
        try:
            msgs = drift(slug)
        except Exception as exc:
            msgs = ["trust check failed: %s" % exc]
        old = set(row.get("reported") or [])
        fresh += [(slug, m) for m in msgs if m not in old]
        row["reported"] = sorted(msgs)
    return fresh
