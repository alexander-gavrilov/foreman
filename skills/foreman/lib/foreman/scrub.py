"""Strip project data from a text before it can leave the machine (spec 4.11, architecture 11).

Replaced: every absolute or relative file path (home and registered projects first, then any other absolute
posix / Windows / UNC path, multi-segment relative paths) -> <path>; project names/slugs -> <project>; session
ids and hex ids -> <id>; emails -> <email>; machine name, hostnames, FQDNs and IP addresses -> <host>; git
remotes and owner/repo slugs (except the foreman repo) -> <repo>; API tokens, passwords and `key=value`
secrets -> <secret>; URLs -> <url> (only the foreman repo's issues/pull/blob/tree pages stay, without query,
fragment or `..`); fenced code blocks and lines that look like file content -> <content omitted>.
Internally every placeholder is a private-use sentinel, so a project named "id" cannot corrupt a placeholder.
"""
import os
import re
import socket

ALLOWED_SLUG = "alexander-gavrilov/foreman"
ALLOWED_URL_PREFIX = "https://github.com/" + ALLOWED_SLUG
_ALLOWED_URL_PATH = re.compile(r"^/(?:issues|pull|blob|tree)(?:/[\w./-]*)?$")
PRODUCT_NAME = "foreman"  # a project called "foreman" is the public product itself: its name is not data

_L, _R = "\ue000", "\ue001"
PLACEHOLDERS = {k: "<%s>" % k for k in ("path", "project", "id", "email", "host", "url", "content omitted",
                                          "repo", "secret")}


_NAMES = list(PLACEHOLDERS)


def _tok(name):
    # private-use characters only, so no project/host name can ever match inside a sentinel
    return _L + chr(0xE100 + _NAMES.index(name)) + _R


def _finalize(text):
    for name, shown in PLACEHOLDERS.items():
        text = text.replace(_tok(name), shown)
    return text


def default_context():
    """What is private on this machine: home, registered project paths/names/slugs, machine name, hostnames."""
    from foreman import config, projects
    homes = {os.path.expanduser("~")}
    if os.environ.get("HOME"):
        homes.add(os.environ["HOME"])
    names, pths = set(), set()
    for proj in projects.list_projects():
        for key in ("name", "slug"):
            if proj.get(key):
                names.add(proj[key])
        if proj.get("path"):
            pths.add(proj["path"])
    hosts = set()
    try:
        hosts.add(config.load()["machine"]["name"])
    except Exception:
        pass
    for fn in (socket.gethostname, socket.getfqdn):
        try:
            hosts.add(fn())
        except Exception:
            pass
    return {"homes": sorted(h for h in homes if h and h != os.sep),
            "paths": sorted(pths), "names": sorted(names), "hosts": sorted(h for h in hosts if h)}


_CODE_START = re.compile(
    r"^\s*(def |class |import |from \S+ import |function |const |let |var |return[ ;]|if \(|for \(|while \(|"
    r"#include|#!/|package |public |private |SELECT |INSERT |UPDATE |DELETE FROM|<\?|</?[a-z][^>]*>\s*$|"
    r"@@ |diff --git|--- a/|\+\+\+ b/|Traceback \(|File \".*\", line \d+|\$ )")
_CODE_SHAPE = re.compile(r"(^\s*[\[{}\]]|[{};]\s*$|^\s*\"[^\"]+\"\s*:\s*\S|^\s*[A-Za-z_][\w.]*\s*=\s*\S|^\s*[-+] \S.*\S$)")


def _looks_like_content(line):
    if not line.strip():
        return False
    if line.startswith(("    ", "\t")):
        return True
    if len(line) > 240:
        return True
    return bool(_CODE_START.match(line) or _CODE_SHAPE.search(line))


def _omit_content(text):
    marker = _tok("content omitted")
    text = re.sub(r"(```|~~~).*?(\1|\Z)", marker, text, flags=re.S)
    out = []
    for line in text.split("\n"):
        if marker in line and line.strip() == marker:
            out.append(marker)
        elif _looks_like_content(line):
            out.append(marker)
        else:
            out.append(line)
    merged = []
    for line in out:
        if line == marker and merged and merged[-1] == marker:
            continue
        merged.append(line)
    return "\n".join(merged)


_TLDS = ("com org net io ai co me tv xyz dev app cloud tech online site store info biz edu gov mil int "
         "uk de fr ru ua us nl se no fi it es cz jp cn in br au ca eu ch at be dk ie il kr tr tw hk sg nz za mx ar cl "
         "lan local internal corp arpa").split()
_FQDN = re.compile(r"(?<![\w@/.:-])(?:[A-Za-z0-9][A-Za-z0-9-]*\.)+(?:%s)(?![\w-]|\.[A-Za-z0-9])" % "|".join(_TLDS), re.I)
_IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w]|\.\d)")
_IPV6 = re.compile(r"(?<![\w:])(?:(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}"
                   r"|(?:[0-9a-fA-F]{1,4}:){1,7}:(?:[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{1,4}){0,6})?"
                   r"|::(?:[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{1,4}){0,6}))(?![\w:])")
_SECRET_PATTERNS = (
    r"\bsk-[A-Za-z0-9_-]{16,}",                      # Anthropic / OpenAI style
    r"\bgh[pousr]_[A-Za-z0-9]{20,}",                 # GitHub tokens
    r"\bgithub_pat_[A-Za-z0-9_]{20,}",
    r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",               # AWS key ids
    r"\bxox[abprs]-[A-Za-z0-9-]{10,}",               # Slack
    r"\beyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]*",           # JWT
    r"(?<![\w:])\d{6,}:[A-Za-z0-9_-]{30,}",          # Telegram bot token
    r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}",
    # key=value / key: value / --flag=value where the key names a secret
    r"(?i)(?<![\w])(?:--)?[\w-]*(?:password|passwd|secret|token|api[_-]?key|private[_-]?key|credential|key)"
    r"[\w-]*\s*[=:]\s*\S+",
)
_WIN_PATH = re.compile(r"(?<![\w])(?:[A-Za-z]:[\\/]|\\\\[\w.$-]+[\\/])[^\s'\"`)\]>,;]*")
_ABS_PATH = re.compile(r"(?<![\w.:/\\<>~-])/(?:[\w.@+~%-]+/)*[\w.@+~%-]+(?:/[^\s'\"`)\]>,;:]*)?")
_REL_PATH = re.compile(r"(?<![\w./\\~:@-])[\w@+%-][\w.@+%-]*(?:/[\w.@+%-]+)+/?(?![\w])")
_SSH_REMOTE = re.compile(r"(?<![\w.-])[\w.-]+@[\w.-]+:(?!//)[^\s'\"`)\]>,;]+")


def _word(name):
    return r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])"


def _keep_url(u):
    """True for a page of the foreman repo that cannot carry project data: https://github.com/<owner>/foreman
    optionally followed by /issues|pull|blob|tree[/...], with no query, fragment or `..`."""
    if u == ALLOWED_URL_PREFIX:
        return True
    if not u.startswith(ALLOWED_URL_PREFIX + "/"):
        return False
    tail = u[len(ALLOWED_URL_PREFIX):]
    return bool(_ALLOWED_URL_PATH.match(tail)) and ".." not in tail


def _relative_path(m):
    """A multi-segment relative path or owner/repo slug -> <path>; ordinary prose with a slash is kept
    (`and/or`, `read/write`, `24/7`, `3/4`): three or more segments, or two with a `-`, `_`, `.` or digit."""
    token = m.group(0)
    segs = [x for x in token.strip("/").split("/") if x]
    if len(segs) < 2 or not any(c.isalpha() for c in token):
        return token
    if len(segs) == 2 and not any(c in "-_." or c.isdigit() for c in token):
        return token
    if len(segs) == 2 and all(x.isdigit() for x in segs):
        return token
    return _tok("path")


def scrub(text, ctx=None):
    """Return `text` with project data removed. `ctx` (see default_context) defaults to this machine's."""
    if ctx is None:
        ctx = default_context()
    text = _omit_content(text.replace("\r\n", "\n"))

    # URLs first: a path or host inside a URL goes with it; only plain foreman repo pages stay readable.
    keep = []

    def hold(value):
        keep.append(value)
        return _L + chr(0xE400 + len(keep) - 1) + _R

    def url(m):
        u = m.group(0).rstrip(".,;:!?)]}>'\"")
        tail = m.group(0)[len(u):]
        if _keep_url(u):
            return hold(u) + tail
        return _tok("url") + tail
    text = re.sub(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s<>\"'`]+", url, text)
    text = re.sub(r"(?<![\w./-])" + re.escape(ALLOWED_SLUG) + r"(?![\w./-])", lambda m: hold(m.group(0)), text)

    # secrets before anything that could split them (paths, ids, hosts)
    for pat in _SECRET_PATTERNS:
        text = re.sub(pat, _tok("secret"), text)

    text = _SSH_REMOTE.sub(_tok("repo"), text)
    text = re.sub(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", _tok("email"), text)
    text = _IPV4.sub(_tok("host"), text)
    text = _IPV6.sub(_tok("host"), text)

    # paths: known roots first (they may contain spaces and swallow the rest of the path), then any other
    # absolute path (posix, Windows drive, UNC), ~/..., and multi-segment relative paths / owner-repo slugs
    roots = sorted(set(ctx.get("paths", [])), key=len, reverse=True) + \
        sorted(set(ctx.get("homes", [])), key=len, reverse=True)
    for root in roots:
        root = root.rstrip("/")
        if root:
            text = re.sub(re.escape(root) + r"(?![A-Za-z0-9_.-])(?:/[^\s'\"`)\]>,;:]*)?", _tok("path"), text)
    text = _WIN_PATH.sub(_tok("path"), text)
    text = _ABS_PATH.sub(_tok("path"), text)
    text = re.sub(r"(?<![\w/])~[\w.-]*/[^\s'\"`)\]>,;:]*", _tok("path"), text)
    text = _REL_PATH.sub(_relative_path, text)

    for name in sorted(set(ctx.get("names", [])), key=len, reverse=True):
        if name and name.lower() != PRODUCT_NAME:
            text = re.sub(_word(name), _tok("project"), text, flags=re.I)

    text = re.sub(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
                  _tok("id"), text)
    text = re.sub(r"(?<![A-Za-z0-9])s-[0-9a-f]{6}(?![A-Za-z0-9])", _tok("id"), text)
    text = re.sub(r"(?<![A-Za-z0-9])[0-9a-fA-F]{8,}(?![A-Za-z0-9])", _tok("id"), text)

    def fqdn(m):
        return m.group(0) if m.group(0).lower() == "github.com" else _tok("host")
    text = _FQDN.sub(fqdn, text)

    for host in sorted(set(ctx.get("hosts", [])), key=len, reverse=True):
        if host and len(host) > 1:
            text = re.sub(_word(host), _tok("host"), text, flags=re.I)
    for i, u in enumerate(keep):
        text = text.replace(_L + chr(0xE400 + i) + _R, u)
    return _finalize(text)
