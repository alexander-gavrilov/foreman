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


# Shapes that are code or terminal output even as a single line.
_CODE_STRONG = re.compile(
    r"^\s*(def \w+\(|class \w+\s*[(:]|import [\w.]+\s*$|from \S+ import \S|function \w*\(|(?:const|let|var) \w+\s*=|"
    r"#include|#!/|package [\w.]+;|SELECT .+ FROM |INSERT INTO |UPDATE \w+ SET |DELETE FROM|<\?|"
    r"@@ |diff --git|--- a/|\+\+\+ b/|Traceback \(|File \".*\", line \d+|\$ |\"[^\"]+\"\s*:\s*\S)")
# Shapes that are only content when several lines in a row have them (a lone `x = 1 is odd` is prose).
_CODE_WEAK = re.compile(r"(^\s*[\[{}\]]|[{}]\s*$|[=()\[\]{}][^\n]*;\s*$|^\s*[A-Za-z_][\w.]*\s*=\s*\S|^\s*\+ \S)")
_PROSE_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+[^\W\d_]\S*(?:\s+\S+){2,}[^;{}]*$")
_CODE_CHARS = re.compile(r"[(){}=;]")


def _line_kind(line):
    """'strong' (code even alone), 'weak' (code only inside a run of such lines) or None (prose)."""
    if not line.strip():
        return None
    if _PROSE_BULLET.match(line):
        return None
    if _CODE_STRONG.match(line):
        return "strong"
    if line.startswith(("    ", "\t")):
        return "strong" if _CODE_CHARS.search(line) else "weak"
    return "weak" if _CODE_WEAK.search(line) else None


def _omit_content(text):
    marker = _tok("content omitted")
    text = re.sub(r"(```|~~~).*?(\1|\Z)", marker, text, flags=re.S)
    lines = text.split("\n")
    kinds = [None if ln.strip() == marker else _line_kind(ln) for ln in lines]
    out, i = [], 0
    while i < len(lines):
        if kinds[i] is None:
            out.append(lines[i])
            i += 1
            continue
        j = i
        while j < len(lines) and kinds[j] is not None:
            j += 1
        run = kinds[i:j]
        for k in range(i, j):  # a run of 2+ code-shaped lines, or any strong line, is content
            out.append(marker if (kinds[k] == "strong" or j - i >= 2) else lines[k])
        i = j
    merged = []
    for line in out:
        if line == marker and merged and merged[-1] == marker:
            continue
        merged.append(line)
    return "\n".join(merged)


_TLDS = ("com org net io ai co me tv xyz dev app cloud tech online site store info biz edu gov mil int "
         "uk de fr ru ua us nl se no fi it es cz jp cn in br au ca eu ch at be dk ie il kr tr tw hk sg nz za mx ar cl "
         "lan local internal corp arpa example test invalid localhost home").split()
# A two-label name whose last label is also an everyday code/English word (`log.info`, `self.id`, `x.in`) is not
# taken for a host unless it has three or more labels; the unambiguous TLDs always count.
_AMBIGUOUS_TLDS = frozenset("info in it no is at be me us tv ai co app dev site store online tech int mil gov edu "
                            "biz cl ar ch se fi es tr tw hk sg nz za mx au ca eu dk ie il kr cn br cz jp".split())
_FQDN = re.compile(r"(?<![\w@/.:-])(?:[A-Za-z0-9][A-Za-z0-9-]*\.)+(?:%s)(?![\w-]|\.[A-Za-z0-9])" % "|".join(_TLDS), re.I)
_HOSTY_LABEL = re.compile(r"(?i)^(?:www|mail|smtp|db\d*|git|api|ns\d*|vpn|wiki|jira|ci|[a-z]+\d+|[\w]+-[\w-]+)$")
_WILD_HOST = re.compile(r"\*\.(?:[A-Za-z0-9-]+\.)*(?:%s)(?![\w-])" % "|".join(_TLDS), re.I)
_HOST_PORT = re.compile(r"(?<![\w./@:-])([A-Za-z][A-Za-z0-9-]*):(\d{2,5})(?![\w:])")
_COMMON_PORTS = frozenset("22 25 53 80 110 143 389 443 465 587 636 993 995 1433 1521 2181 2375 2376 3000 3306 4000 "
                          "5000 5432 5672 6379 7000 8000 8008 8080 8081 8443 8888 9000 9092 9200 11211 27017".split())
_IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w]|\.\d)")
_VERSION_BEFORE = re.compile(r"(?:\bv|\bversion\s*|\bver\.?\s*|\bpython\s*|\bnode\s*|==|>=|<=|~=|\bbuild\s*|\b(?:foreman|release|claude|codex|orca|herdr|gemini|agy|cli)\s+)$", re.I)
_IPV6 = re.compile(r"(?<![\w:])(?:(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}"
                   r"|(?:[0-9a-fA-F]{1,4}:){1,7}:(?:[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{1,4}){0,6})?"
                   r"|::(?:[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{1,4}){0,6}))(?![\w:])")
_PEM = re.compile(r"-----BEGIN [A-Z0-9 ]+-----.*?(?:-----END [A-Z0-9 ]+-----|\Z)", re.S)
_SECRET_PATTERNS = (
    r"\bsk-[A-Za-z0-9_-]{16,}",                      # Anthropic / OpenAI style
    r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{8,}",        # Stripe
    r"\bAIza[0-9A-Za-z_-]{20,}",                     # Google API keys
    r"\bglpat-[A-Za-z0-9_-]{10,}",                   # GitLab
    r"\bnpm_[A-Za-z0-9]{20,}",                       # npm
    r"\bhf_[A-Za-z0-9]{30,}",                        # Hugging Face
    r"\bdop_v1_[A-Za-z0-9]{20,}",
    r"\bSG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}",
    r"\bgh[pousr]_[A-Za-z0-9]{20,}",                 # GitHub tokens
    r"\bgithub_pat_[A-Za-z0-9_]{20,}",
    r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",               # AWS key ids
    r"\bxox[abprs]-[A-Za-z0-9-]{10,}",               # Slack
    r"\beyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]*",           # JWT
    r"(?<![\w:])\d{6,}:[A-Za-z0-9_-]{30,}",          # Telegram bot token
    r"(?i)\bauthorization\s*[:=]\s*(?:basic|bearer|token|digest)?\s*[A-Za-z0-9._~+/=:-]{6,}",
    r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}",
    r"(?i)\b(?:password|passwd|passphrase|pwd)\s+(?:is|was|=|:)\s+(?!(?:wrong|empty|required|missing|incorrect|"
    r"correct|invalid|set|not|the|a|an|too)\b)\S+",   # "the password is hunter2"
    r"(?i)\b(?:secret|token|api[_-]?key)\s+(?:is|was)\s+(?=\S*\d)\S{6,}",
    r"(?<=://)[^\s/:@]+:[^\s/@]+(?=@)",              # user:password@ in a URL
)
# key=value / key: value / --flag=value / "key": "value" where the key names a secret
_KV = re.compile(r"""(?i)(?<![\w])(--)?([\w-]*?(?:password|passwd|pwd|secret|token|api[_-]?key|private[_-]?key|credential|key)[\w-]*)
                     (["']?)\s*[=:]\s*(?:"([^"\n]*)"|'([^'\n]*)'|(\S+))""", re.X)
_STRONG_KEY = re.compile(r"(?i)password|passwd|pwd|secret|credential|api[_-]?key|private[_-]?key|(?:^|[_-])token$|^token")
_KEY_NOT_SECRET = re.compile(r"(?i)hotkey|keybind|keyboard|monkey|turkey|keymap|keyframe|keyword|shortcut|"
                             r"(?:context|max|min|input|output|prompt|total|num|n|used|limit|budget|count)[_-]?tokens?$|tokens$")
_NOT_A_VALUE = frozenset("value val <value> true false none null nil yes no on off x y xxx ... *** <secret> "
                         "<redacted> redacted string str int".split())


def _kv_secret(m):
    name, quote = m.group(2), m.group(3)
    value = next((g for g in (m.group(4), m.group(5), m.group(6)) if g is not None), "")
    bare = value.strip().rstrip(",;.)}]")
    if not bare or bare.lower() in _NOT_A_VALUE or bare.startswith(("<", _L)):
        return m.group(0)
    if _KEY_NOT_SECRET.search(name):
        return m.group(0)
    strong = bool(_STRONG_KEY.search(name))
    if not strong:  # a plain `key`: only a value that looks like a credential counts
        if not (len(bare) >= 16 or (len(bare) >= 8 and any(c.isdigit() for c in bare) and any(c.isalpha() for c in bare))):
            return m.group(0)
    elif bare.isdigit() and len(bare) < 8 and not re.search(r"(?i)password|passwd|pwd|pin", name):
        return m.group(0)  # `token_count: 150` style numbers
    elif m.group(6) is not None and not quote and len(bare) < 3:
        return m.group(0)
    return _tok("secret")


def _entropy(token):
    from math import log2
    counts = {}
    for ch in token:
        counts[ch] = counts.get(ch, 0) + 1
    return -sum(n / len(token) * log2(n / len(token)) for n in counts.values())


_TOKENISH = re.compile(r"(?<![\w/.-])[A-Za-z0-9+_=-]{24,}(?![\w/])")
_READABLE_SEG = re.compile(r"^[A-Za-z]+\d{0,2}$")


def _looks_random(token, min_len=24, min_entropy=3.6):
    if len(token) < min_len or not any(c.isalpha() for c in token):
        return False
    has_digit = any(c.isdigit() for c in token)
    if not (has_digit or any(c in "+/=" for c in token)):
        return False
    segs = [x for x in re.split(r"[_-]+", token) if x]
    if len(segs) > 1 and all(_READABLE_SEG.match(x) and len(x) <= 14 for x in segs):
        return False  # snake_case / kebab-case identifier made of short words
    if token.startswith(_L):
        return False
    return _entropy(token) >= min_entropy


def _generic_token(m):
    return _tok("secret") if _looks_random(m.group(0)) else m.group(0)


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


_FOREMAN_OWN_PATH = re.compile(r"^(?:skills/foreman/)?(?:lib/)?foreman/[\w.-]+(?:/[\w.-]+)*$")


def _relative_path(m):
    """A multi-segment relative path or owner/repo slug -> <path>; ordinary prose with a slash is kept
    (`and/or`, `read/write`, `24/7`, `3/4`): three or more segments, or two with a `-`, `_`, `.` or digit."""
    token = m.group(0)
    if _FOREMAN_OWN_PATH.match(token):
        return token  # foreman's own source files name no project
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
    text = _PEM.sub(_tok("secret"), text.replace("\r\n", "\n"))
    text = _omit_content(text)

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
    text = _KV.sub(_kv_secret, text)

    text = _SSH_REMOTE.sub(_tok("repo"), text)
    text = re.sub(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", _tok("email"), text)
    def ipv4(m):  # a version number (`v1.2.3.4`, `python 3.12.1.2`) is not an address
        return m.group(0) if _VERSION_BEFORE.search(m.string[max(0, m.start() - 12):m.start()]) \
            or m.group(0).split(".")[0] in ("0", "00") else _tok("host")
    text = _IPV4.sub(ipv4, text)
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
        low = m.group(0).lower()
        labels = low.split(".")
        if low == "github.com":
            return m.group(0)
        if len(labels) == 2 and labels[1] in _AMBIGUOUS_TLDS and not _HOSTY_LABEL.match(labels[0]):
            return m.group(0)  # `log.info`, `self.id`: attribute access, not a host
        return _tok("host")
    text = _TOKENISH.sub(_generic_token, text)  # after paths/ids/hosts took theirs; what is left and random is a key
    text = _WILD_HOST.sub(_tok("host"), text)
    text = _FQDN.sub(fqdn, text)

    def host_port(m):
        word, port = m.group(1), m.group(2)
        if word.lower() in ("localhost", "port", "ports", "http", "https", "line", "col", "pid", "tcp", "udp"):
            return m.group(0)
        if any(c.isdigit() or c == "-" for c in word) or port in _COMMON_PORTS:
            return _tok("host") + ":" + port
        return m.group(0)
    text = _HOST_PORT.sub(host_port, text)

    for host in sorted(set(ctx.get("hosts", [])), key=len, reverse=True):
        if host and len(host) > 1:
            text = re.sub(_word(host), _tok("host"), text, flags=re.I)
    for i, u in enumerate(keep):
        text = text.replace(_L + chr(0xE400 + i) + _R, u)
    return _finalize(text)


# ---------------------------------------------------------------- the gate (D56)

MIN_USEFUL_CHARS = 20
_RESIDUAL_SECRET = (
    re.compile(r"-----BEGIN"),
    re.compile(r"(?i)\bauthorization\s*[:=]"),
    re.compile(r"(?i)\b(?:password|passwd|passphrase|pwd|secret|api[_-]?key|private[_-]?key|credential)s?[\"']?\s*[:=]\s*"
               r"[\"']?(?!<)[^\s\"'<]{3,}"),
    re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"://[^\s/@]+:[^\s/@]+@"),
)


def gate(scrubbed):
    """Fail-safe check of the text AFTER scrubbing (D56). Returns None when it may leave the machine on its own,
    else a short reason: it still holds high-entropy / secret-like content, or too little useful text survived
    (placeholders and punctuation do not count). The caller keeps such a finding as a local draft."""
    text = scrubbed or ""
    for pat in _RESIDUAL_SECRET:
        if pat.search(text):
            return "secret-like text remains after scrubbing"
    for m in re.finditer(r"[A-Za-z0-9+/_=-]{20,}", text):
        if _looks_random(m.group(0), min_len=20, min_entropy=3.4):
            return "high-entropy token remains after scrubbing"
    plain = re.sub(r"<(?:%s)>" % "|".join(re.escape(k) for k in PLACEHOLDERS), " ", text)
    useful = re.sub(r"[\W_]+", "", plain)
    if len(useful) < MIN_USEFUL_CHARS:
        return "too little useful text survived scrubbing (%d characters)" % len(useful)
    return None
