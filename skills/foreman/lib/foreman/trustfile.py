"""Narrow, locked, atomic edits of an agent's settings file (spec 5.5 "How a file is edited").

Used for Antigravity's settings.json (JSON) and Codex's config.toml (TOML), and for the statusline wrapping of the
same files. Nothing here decides *what* may be trusted (that is trust.py); this is how a file is touched:

* the target is the file the agent really reads (a link is followed), a regular file owned by the user;
* one lock per target, kept in the state folder, taken from the read to the end of the rename and the record update;
* the new text is the old text plus the added lines (a text edit, never a re-dump), and the old and the new text are
  parsed and compared before anything is written;
* the temporary file and the backup are created exclusively, with the original's permissions set before any content;
* the file is compared with what was read just before the rename, and read back after it; up to three tries.
"""
import contextlib
import hashlib
import json
import os
import stat

from foreman import paths, store

BACKUP_SUFFIX = ".foreman-bak"
MAX_TRIES = 3


class TrustEditError(Exception):
    """A file could not be edited; the message is the reason shown to the user. The file was left as it was."""


class TrustEditWritten(TrustEditError):
    """The new text IS in the file, but the read-back or the record update failed afterwards. The caller must keep the
    record as it is (a pending entry stays, so uninstall reports it) and never drop it."""


# ------------------------------------------------------------ target, lock, read

def resolve_target(path):
    """The file an edit lands on: links followed, so a managed dotfile is not replaced by a plain copy."""
    return os.path.realpath(path)


def check_target(path):
    """Raise TrustEditError unless `path` is a regular file owned by the user, or does not exist (then its folder
    must). Returns True when the file exists."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        folder = os.path.dirname(path)
        if not os.path.isdir(folder):
            raise TrustEditError("%s does not exist and neither does its folder" % path)
        if not os.access(folder, os.W_OK):
            raise TrustEditError("%s is not writable" % folder)
        return False
    except OSError as exc:
        raise TrustEditError("%s cannot be read (%s)" % (path, exc))
    if not stat.S_ISREG(st.st_mode):
        raise TrustEditError("%s is not a regular file" % path)
    if st.st_uid != os.getuid():
        raise TrustEditError("%s is not owned by you" % path)
    if not os.access(path, os.R_OK | os.W_OK) or not os.access(os.path.dirname(path), os.W_OK):
        raise TrustEditError("%s (or its folder) is not writable" % path)
    return True


@contextlib.contextmanager
def file_lock(target):
    """The one lock every foreman edit of `target` takes (trust writes and the statusline wrapping alike)."""
    name = hashlib.sha256(resolve_target(target).encode("utf-8", "surrogateescape")).hexdigest()[:24]
    with store.locked(os.path.join(paths.home(), "locks", "edit-" + name)):
        yield


def read_bytes(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None


def decode(raw, path):
    if raw is None:
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise TrustEditError("%s is not valid UTF-8" % path)


# ------------------------------------------------------------ write, backup

def _write_exclusive(path, data, mode):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(fd, mode)  # before any content: a settings file can hold keys
        with os.fdopen(fd, "wb") as f:
            fd = None
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    finally:
        if fd is not None:
            os.close(fd)


def make_backup(target, raw):
    """The file as it was before foreman's first edit, never overwritten. Returns True when it was made. Written to a
    temporary name and linked into place, so a failure never leaves a truncated backup that would then stay for good."""
    backup = target + BACKUP_SUFFIX
    if raw is None or os.path.lexists(backup):
        return False
    tmp = "%s.tmp-%d" % (backup, os.getpid())
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    try:
        _write_exclusive(tmp, raw, stat.S_IMODE(os.stat(target).st_mode))
        try:
            os.link(tmp, backup)  # exclusive: fails when a backup appeared meanwhile
        except FileExistsError:
            return False
        except OSError:  # no hard links here (some FUSE, exFAT, SMB): create it exclusively instead
            try:
                _write_exclusive(backup, raw, stat.S_IMODE(os.stat(target).st_mode))
            except FileExistsError:
                return False
            except OSError:
                try:
                    os.unlink(backup)  # never leave a truncated backup that would then stay for good
                except OSError:
                    pass
                raise
        return True
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _tmp_name(target):
    return os.path.join(os.path.dirname(target), ".%s.foreman-%d.tmp" % (os.path.basename(target), os.getpid()))


def atomic_write(target, data, mode=None):
    """Write `data` (bytes) over `target` through a temporary file in the same folder and a rename."""
    tmp = _tmp_name(target)
    if mode is None:
        mode = stat.S_IMODE(os.stat(target).st_mode) if os.path.exists(target) else 0o600
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    _write_exclusive(tmp, data, mode)
    try:
        os.replace(tmp, target)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def edit(target, build, verify, present, before_write=None, after_applied=None, on_abort=None):
    """Run one narrow edit of `target` under its lock.

    build(old_text, existed) -> new_text, or None when there is nothing to do (everything is already there).
        It may raise TrustEditError (the file is not what is expected).
    verify(old_text, new_text) raises TrustEditError unless the difference is exactly the intended one.
    present(text) -> True when the added entries are in `text` (the read-back check).
    before_write() / after_applied() / on_abort() update the record (pending before the edit, applied after the
        read-back, dropped when the edit finally fails).
    Returns "unchanged", "created" or "edited". Raises TrustEditError after MAX_TRIES lost races."""
    target = resolve_target(target)
    recorded = [False]
    aborted = [False]
    renamed = [False]  # the new text is in the file: from here on nothing may be dropped from the record

    def abort():
        """Drop what was recorded as pending: nothing was, or will be, written by foreman."""
        if on_abort and not aborted[0]:
            aborted[0] = True
            try:
                on_abort()
            except Exception:
                pass
    try:
        with file_lock(target):
            existed = check_target(target)
            for _ in range(MAX_TRIES):
                renamed[0] = False
                raw = read_bytes(target)
                text = decode(raw, target)
                new_text = build(text, raw is not None)
                if new_text is None:
                    abort()  # what an earlier try recorded as pending was written by somebody else meanwhile
                    return "unchanged"
                verify(text, new_text)
                if before_write:
                    before_write()  # pending before the edit; again if the text changed and we start over
                    recorded[0] = True
                if raw is not None:
                    make_backup(target, raw)
                # the agents cannot be locked: compare with what was read, just before the rename
                if read_bytes(target) != raw:
                    continue
                mode = stat.S_IMODE(os.stat(target).st_mode) if raw is not None else 0o600
                atomic_write(target, new_text.encode("utf-8"), mode)
                renamed[0] = True
                if present(decode(read_bytes(target), target)):
                    if after_applied:
                        after_applied()
                    recorded[0] = False
                    return "edited" if existed else "created"
            abort()
            raise TrustEditError("%s kept changing while it was edited (tried %d times); nothing was lost, run "
                                 "again" % (target, MAX_TRIES))
    except TrustEditWritten:
        raise
    except TrustEditError as exc:
        if renamed[0]:  # e.g. the read-back found a file somebody else rewrote and that no longer parses
            raise TrustEditWritten("%s was edited but the result could not be checked or recorded (%s); the record "
                                   "was left as it was and uninstall will report the entry" % (target, exc))
        abort()
        raise
    except (OSError, UnicodeError) as exc:  # a full disk, an I/O error, a path that is not UTF-8: a refusal, not a crash
        if renamed[0]:
            # the entry is in the file but the read-back or the record update failed: the pending record stays, so
            # uninstall reports it (spec 5.5) instead of leaving an unrecorded entry
            raise TrustEditWritten("%s was edited but the result could not be checked or recorded (%s); the record "
                                   "was left as it was and uninstall will report the entry" % (target, exc))
        abort()
        raise TrustEditError("%s could not be written (%s); nothing was changed" % (target, exc))


# ------------------------------------------------------------ strict JSON text scanning

def parse_json_strict(text, path="the file"):
    """Strict JSON: no BOM, no comments, no NaN/Infinity, no trailing text."""
    def refuse(name):
        raise ValueError("%s is not allowed" % name)
    try:
        if text.startswith("﻿"):
            raise ValueError("a BOM")
        return json.loads(text, parse_constant=refuse)
    except (ValueError, RecursionError) as exc:
        raise TrustEditError("%s is not valid JSON (%s)" % (path, exc))


_WS = " \t\r\n"


def _skip_ws(t, i):
    while i < len(t) and t[i] in _WS:
        i += 1
    return i


def _scan_string(t, i):
    """`i` is at the opening quote; returns the index just after the closing one."""
    i += 1
    while i < len(t):
        c = t[i]
        if c == "\\":
            i += 2
        elif c == '"':
            return i + 1
        else:
            i += 1
    raise TrustEditError("unterminated string in JSON")


def _scan_value(t, i):
    """End (exclusive) of the JSON value that starts at `i` (whitespace already skipped)."""
    c = t[i]
    if c == '"':
        return _scan_string(t, i)
    if c in "{[":
        depth = 0
        while i < len(t):
            c = t[i]
            if c == '"':
                i = _scan_string(t, i)
                continue
            if c in "{[":
                depth += 1
            elif c in "}]":
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        raise TrustEditError("unterminated container in JSON")
    j = i
    while j < len(t) and t[j] not in _WS + ",]}":
        j += 1
    return j


def _members(t, open_idx):
    """The members of the object whose `{` is at `open_idx`: ([(key, key_start, value_start, value_end)], close)."""
    out, i = [], _skip_ws(t, open_idx + 1)
    while t[i] != "}":
        k_start = i
        k_end = _scan_string(t, i)
        key = json.loads(t[k_start:k_end])
        i = _skip_ws(t, k_end)
        i = _skip_ws(t, i + 1)  # the colon
        v_end = _scan_value(t, i)
        out.append((key, k_start, i, v_end))
        i = _skip_ws(t, v_end)
        if t[i] == ",":
            i = _skip_ws(t, i + 1)
    return out, i


def _elements(t, open_idx):
    """The elements of the array whose `[` is at `open_idx`: ([(start, end)], close)."""
    out, i = [], _skip_ws(t, open_idx + 1)
    while t[i] != "]":
        end = _scan_value(t, i)
        out.append((i, end))
        i = _skip_ws(t, end)
        if t[i] == ",":
            i = _skip_ws(t, i + 1)
    return out, i


def _top(t):
    start = _skip_ws(t, 0)
    if start >= len(t) or t[start] != "{":
        raise TrustEditError("the JSON file does not hold an object")
    return start


def _indent_of(t, pos):
    """The whitespace between the start of the line and `pos` (only when that is all whitespace), else None."""
    line_start = t.rfind("\n", 0, pos) + 1
    seg = t[line_start:pos]
    return seg if seg.strip() == "" else None


def json_add_to_list(text, key, value):
    """`text` (a JSON object) with the string `value` appended to its top-level list `key` (the list is made when
    there is none). Everything else in the text stays byte for byte."""
    obj = _top(text)
    members, close = _members(text, obj)
    for name, k_start, v_start, v_end in members:
        if name != key:
            continue
        elems, arr_close = _elements(text, v_start)
        enc = json.dumps(value, ensure_ascii=False)
        if not elems:
            return text[:v_start + 1] + enc + text[arr_close:]
        last_start, last_end = elems[-1]
        if len(elems) > 1:
            gap = text[elems[-2][1]:last_start]
            sep = "," + gap.split(",", 1)[1] if "," in gap else ", "
        else:
            lead = text[v_start + 1:last_start]
            sep = "," + lead if "\n" in lead and lead.strip() == "" else ", "
        return text[:last_end] + sep + enc + text[last_end:]
    enc = json.dumps(value, ensure_ascii=False)
    if not members:
        inner = text[obj + 1:close]
        nl = "\n" if "\n" in inner or inner == "" else ""
        body = '%s  %s: [%s]%s' % (nl, json.dumps(key), enc, nl) if nl else '%s: [%s]' % (json.dumps(key), enc)
        return text[:obj + 1] + body + text[close:]
    lead = text[obj + 1:members[0][1]]
    sep = lead if "\n" in lead and lead.strip() == "" else ""
    if sep:
        unit = _indent_of(text, members[0][1]) or "  "
        item = "%s: [\n%s  %s\n%s]" % (json.dumps(key), unit, enc, unit)
    else:
        item = "%s: [%s]" % (json.dumps(key), enc)
    last_end = members[-1][3]
    return text[:last_end] + "," + (sep or " ") + item + text[last_end:]


def json_remove_from_list(text, key, value, drop_key_if_empty=False):
    """`text` without the string `value` in its top-level list `key`: the inverse of json_add_to_list (the key goes
    too when that empties a list foreman made). Returns the text, or None when `value` is not in the list."""
    obj = _top(text)
    members, close = _members(text, obj)
    for mi, (name, k_start, v_start, v_end) in enumerate(members):
        if name != key:
            continue
        elems, arr_close = _elements(text, v_start)
        found = []
        for ei, (s, e) in enumerate(elems):
            try:
                if json.loads(text[s:e]) == value:
                    found.append(ei)
            except ValueError:
                continue
        if found:
            ei = found[-1]  # a copy of the same path that somebody else added stays; foreman's append is the last
            s, e = elems[ei]
            if len(elems) == 1 and drop_key_if_empty:
                if len(members) == 1:
                    return text[:obj + 1] + text[close:]
                if mi > 0:
                    return text[:members[mi - 1][3]] + text[v_end:]
                return text[:k_start] + text[members[1][1]:]
            if ei > 0:
                return text[:elems[ei - 1][1]] + text[e:]
            if len(elems) > 1:
                return text[:s] + text[elems[1][0]:]
            return text[:v_start + 1] + text[arr_close:]
        return None
    return None


def json_list(data, key):
    """The strings of the top-level list `key` of a parsed object; TrustEditError when it has the wrong shape."""
    if not isinstance(data, dict):
        raise TrustEditError("the JSON file does not hold an object")
    if key not in data:
        return None
    value = data[key]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise TrustEditError("%s is not a list of strings" % key)
    return value


# ------------------------------------------------------------ TOML

def toml_module():
    """tomllib (Python 3.11+), else None."""
    try:
        import tomllib
        return tomllib
    except ImportError:
        return None


def parse_toml(text, path="the file"):
    tomllib = toml_module()
    if tomllib is None:
        raise TrustEditError("Python 3.11 or newer is needed to read %s (tomllib)" % path)
    try:
        return tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError) as exc:
        raise TrustEditError("%s is not valid TOML (%s)" % (path, exc))


def toml_string(value):
    """A TOML basic string for `value`; a control character (DEL included) is refused."""
    if any(ord(c) < 0x20 or ord(c) == 0x7f for c in value):
        raise TrustEditError("a path with a control character cannot be written: %r" % value)
    return json.dumps(value, ensure_ascii=False)


def toml_table_text(header_keys, values):
    """`[a."b"]` and its `key = value` lines, values being strings or booleans."""
    lines = ["[%s]" % ".".join(toml_string(k) if k_needs_quote(k) else k for k in header_keys)]
    for k, v in values.items():
        lines.append("%s = %s" % (k, "true" if v is True else "false" if v is False else toml_string(v)))
    return "\n".join(lines) + "\n"


def k_needs_quote(key):
    return not (key and all(c.isalnum() and c.isascii() or c in "_-" for c in key))


def toml_append_tables(text, blocks):
    """Append each table text of `blocks` to `text`; returns (new_text, [text added for each block]). Each addition
    carries its own leading newline(s), so a recorded addition is removed exactly."""
    added = []
    for block in blocks:
        pre = ""
        if text and not text.endswith("\n"):
            pre += "\n"
        if text:
            pre += "\n"
        added.append(pre + block)
        text += pre + block
    return text, added


def toml_remove_text(text, added):
    """`text` without the exact `added` text (last occurrence), or None when it is not there as written."""
    at = text.rfind(added)
    if at < 0:
        return None
    return text[:at] + text[at + len(added):]
