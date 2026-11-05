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
         "uk de fr ru ua us nl se no fi it es cz jp cn in br au ca eu ch at be dk ie il kr tr tw hk sg nz za mx ar cl pl "
         "lan local internal corp arpa example test invalid localhost home").split()
# A two-label name whose last label is also an everyday code/English word (`log.info`, `self.id`, `x.in`) is not
# taken for a host unless it has three or more labels; the unambiguous TLDs always count.
_AMBIGUOUS_TLDS = frozenset("info in it no is at be me us id".split())  # R5-2: real TLDs (.ai .dev .app .ch ..) are hosts
_FQDN = re.compile(r"(?<![\w@/.:-])(?:[A-Za-z0-9][A-Za-z0-9-]*\.)+(?:%s)(?![\w-]|\.[A-Za-z0-9])" % "|".join(_TLDS), re.I)
_HOSTY_LABEL = re.compile(r"(?i)^(?:www|mail|smtp|db\d*|git|api|ns\d*|vpn|wiki|jira|ci|[a-z]+\d+|[\w]+-[\w-]+)$")
_WILD_HOST = re.compile(r"\*\.(?:[A-Za-z0-9-]+\.)*(?:%s)(?![\w-])" % "|".join(_TLDS), re.I)
_HOST_PORT = re.compile(r"(?<![\w./@:-])([A-Za-z][A-Za-z0-9-]*):(\d{2,5})(?![\w:])")
_COMMON_PORTS = frozenset("22 25 53 80 110 143 389 443 465 587 636 993 995 1433 1521 2181 2375 2376 3000 3306 4000 "
                          "5000 5432 5672 6379 7000 8000 8008 8080 8081 8443 8888 9000 9092 9200 11211 27017".split())
_IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w]|\.\d)")
_VERSION_STRICT = re.compile(r"(?:\bv|\bversion\s*|\bver\.?\s*|\bpython\s*|==|>=|<=|~=|\bbuild\s*)$", re.I)
_VERSION_PRODUCT = re.compile(r"\b(?:foreman|release|claude|codex|orca|herdr|gemini|agy|cli|node)\s+$", re.I)


def _private_ip(text):
    a, b = (int(x) for x in text.split(".")[:2])
    return a in (0, 10, 127) or a >= 224 or (a, b) == (192, 168) or (a == 172 and 16 <= b <= 31) \
        or (a, b) == (169, 254) or (a == 100 and 64 <= b <= 127)


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
    r"(?<=://)[^\s/:@]+:[^\s/@]+(?=@)",              # user:password@ in a URL
)
# words that follow "the token is ..." in plain prose; anything else after is/was is taken for a value (R5-2)
PLAIN_AFTER_IS = frozenset(
    "a an the not no yes none null nil unset empty blank missing required wrong incorrect correct invalid valid set "
    "too stored expired revoked rotated unknown stale old new used unused leaked exposed visible hidden shown "
    "printed logged sent saved written read ok fine fixed deleted created refreshed always never also only still "
    "already now then passed failed rejected accepted allowed denied found lost long short value val string str "
    "int true false x y xxx redacted there here this that it in on at to of for from by with without and or but "
    "is was be being been so if when after before because until while as like just very really".split())
_KNOWN_ANGLE = frozenset("<value> <val> <secret> <redacted> <token> <password> <key> <string> <id> <name> "
                         "<your-token> <your-key> <api-key> <path> <project> <email> <host> <url> <repo>".split())
_IS_SECRET = re.compile(r"(?i)\b((?:password|passwd|passphrase|pwd|secret|token|api[_-]?key|private[_-]?key|"
                        r"credentials?)\s+(?:is|was)\s+)([^\s,;]+)")


def _known_placeholder(value):
    v = value.strip().rstrip(".,;)}]").lower()
    return v.startswith(_L) or v in _KNOWN_ANGLE or v in {"<%s>" % k for k in PLACEHOLDERS}


def _is_secret(m):
    value = m.group(2)
    if _known_placeholder(value) or value.strip(".,;:!?)\"'").lower() in PLAIN_AFTER_IS:
        return m.group(0)
    return m.group(1) + _tok("secret")


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
    if not bare or bare.lower() in _NOT_A_VALUE or _known_placeholder(bare):
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


def _class(ch):
    return "l" if ch.islower() else "u" if ch.isupper() else "d" if ch.isdigit() else "o"


def _transition_density(seg):
    """Share of adjacent characters that change class (lower / upper / digit): `aBcDeF` and `Ab12Cd34` are high,
    words and CamelCase names are low."""
    if len(seg) < 2:
        return 0.0
    return sum(1 for a, b in zip(seg, seg[1:]) if _class(a) != _class(b)) / (len(seg) - 1)


def _random_segments(token):
    return any(len(seg) >= 8 and _transition_density(seg) >= 0.45 for seg in re.split(r"[_\-/.+=]+", token))


def _looks_random(token, min_len=24, min_entropy=3.6):
    if len(token) < min_len or not any(c.isalpha() for c in token):
        return False
    if _random_segments(token):
        return True  # mixed case / letter-digit flips inside a segment (R5-2)
    has_digit = any(c.isdigit() for c in token)
    if not (has_digit or any(c in "+/=" for c in token)):
        return False
    segs = [x for x in re.split(r"[_/-]+", token) if x]
    if len(segs) > 1 and all(_READABLE_SEG.match(x) and len(x) <= 14 for x in segs):
        return False  # snake_case / kebab-case identifier made of short words
    if token.startswith(_L):
        return False
    return _entropy(token) >= min_entropy


def _generic_token(m):
    return _tok("secret") if _looks_random(m.group(0)) else m.group(0)


_WIN_PATH = re.compile(r"(?<![\w])(?:[A-Za-z]:[\\/]|\\\\[\w.$-]+[\\/])[^\s'\"`)\]>,;]*")
_ABS_PATH = re.compile(r"(?<![\w./\\<>~-])/(?:[\w.@+~%-]+/)*[\w.@+~%-]+(?:/[^\s'\"`)\]>,;:]*)?")
_DOT_PATH = re.compile(r"(?<![\w./\\~:@-])(?:\.{1,2}/)+[\w.@+%-]+(?:/[\w.@+%-]+)*/?")
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
    text = _IS_SECRET.sub(_is_secret, text)
    text = _KV.sub(_kv_secret, text)

    text = _SSH_REMOTE.sub(_tok("repo"), text)
    text = re.sub(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", _tok("email"), text)
    def ipv4(m):  # a version number (`v1.2.3.4`, `python 3.12.1.2`) is not an address
        before = m.string[max(0, m.start() - 12):m.start()]
        if any(int(x) > 255 for x in m.group(0).split(".")) or m.group(0).split(".")[0] in ("0", "00"):
            return m.group(0)
        if _VERSION_STRICT.search(before):
            return m.group(0)
        if _VERSION_PRODUCT.search(before) and not _private_ip(m.group(0)):
            return m.group(0)  # `foreman 3.12.1.2`; a private address after a product name is a host
        return _tok("host")
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
    text = _DOT_PATH.sub(_tok("path"), text)
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


# ---------------------------------------------------------------- the gate (D56, D58)
# The gate is the safety boundary, the scrubber only makes reports readable (D58): after taking away what is known
# to be harmless (foreman's own paths, slug and URLs, placeholders, plain version numbers) every remaining token
# must look like ordinary prose. A false draft is cheap, a false public issue is not.

MIN_USEFUL_CHARS = 20
_RESIDUAL_SECRET = (
    re.compile(r"-----BEGIN"),
    re.compile(r"(?i)\bauthorization\s*[:=]"),
    re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"://[^\s/@]+:[^\s/@]+@"),
)
_SECRET_PHRASE = re.compile(r"(?i)(?<![\w])(?:password|passwd|passphrase|pwd|secret|token|api[_-]?key|private[_-]?key|"
                            r"credential|key)s?[\"']?\s*(is|was|[:=])\s*[\"'`]?([^\s,;\"'`)]*)")
_ANY_URL = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s<>\"'`]+")
_VERSION_PLAIN = re.compile(r"(?<![\w.])v?\d+(?:\.\d+){1,2}(?:-?(?:rc|alpha|beta|a|b|dev)\d*)?(?![\w]|\.\d)")
_VERSION_WORDED = re.compile(r"(?i)\b(?:v|version\s+|ver\.?\s*|python\s+)\d+(?:\.\d+){3}(?![\w.])")
_GATE_SPLIT = re.compile(r"[\s,;()\[\]{}\"'`|<>*=!?\u00ab\u00bb\u2192\u00b7\u2014\u2013]+")
_HOST_PORT_GATE = re.compile(r"(?<![\w./@:-])([A-Za-z][A-Za-z0-9._-]*):(\d{2,5})(?![\w:])")
_PORT_LABELS = frozenset("line lines col column pid port ports exit code rc step status".split())
_PROSE_SLASH = frozenset(
    "and/or read/write on/off yes/no input/output true/false either/or he/she pass/fail start/stop client/server i/o "
    "ci/cd tcp/ip w/o n/a up/down open/close enable/disable add/remove push/pull before/after request/response "
    "stdin/stdout stdout/stderr s/he lead/orchestrator orchestrator/worker worker/orchestrator user/web".split())
_DOT_PROSE = frozenset("e.g i.e a.m p.m etc vs github.com".split())
_PLAIN_DOTTED = re.compile(r"^\d+(?:\.\d+)+[%a-z]{0,3}$")
_GENERIC_FILES = frozenset("events.jsonl settings.json settings.local.json hooks.json inbox.jsonl".split())


def _foreman_modules():
    root = os.path.dirname(os.path.abspath(__file__))
    names = set()
    for base, _dirs, files in os.walk(root):
        for f in files:
            if f.endswith(".py"):
                names.add(f[:-3])
        names.add(os.path.basename(base))
    names.discard("__init__")
    return frozenset(n for n in names if n)


_MODULES = _foreman_modules()


def _prose_dotted(tok):
    if tok.lower() in _DOT_PROSE or tok in _GENERIC_FILES or _PLAIN_DOTTED.match(tok):
        return True
    parts = tok.split(".")
    if parts[0] == "foreman" and len(parts) > 1 and parts[1] in _MODULES and all(re.match(r"^\w+$", x) for x in parts):
        return True
    return len(parts) == 2 and parts[1] == "py" and parts[0] in _MODULES  # `watch.py`


def _unsafe_token(raw):
    """Reason a single whitespace-free token is not ordinary prose, else None."""
    tok = raw.strip(".:;-_#\u2026")
    if raw.startswith(".") and len(raw) > 1 and not set(raw) <= {"."}:
        tok = raw.rstrip(".:;")  # `.env`, `.claude`: a hidden name, not an ellipsis
    if not tok or set(tok) <= set("./-_:#"):
        return None
    if _FOREMAN_OWN_PATH.match(tok):
        return None
    if "/" in tok or "\\" in tok:
        low = tok.strip("/").lower()
        if low in _PROSE_SLASH or re.match(r"^\d+(?:/\d+)+$", low):
            return None
        return "path-like token remains after scrubbing"
    if "." in tok and not _prose_dotted(tok):
        return "dotted name remains after scrubbing"
    if len(tok) >= 16 and (_random_segments(tok) or _looks_random(tok, min_len=16, min_entropy=3.4)):
        return "high-entropy token remains after scrubbing"
    return None


def gate(scrubbed):
    """Fail-safe check of the text AFTER scrubbing (D56, D58). Returns None when it may leave the machine on its own,
    else a short reason: after removing what is known to be harmless every remaining token must look like plain
    prose (no path separator, host, port, IP address, dotted name, secret-like phrase or random-looking token), and
    enough useful text must survive (placeholders and punctuation do not count). The caller keeps such a finding
    as a local draft. Known limit: an unregistered project name in plain prose cannot be recognised."""
    text = scrubbed or ""
    for pat in _RESIDUAL_SECRET:
        if pat.search(text):
            return "secret-like text remains after scrubbing"
    # take away what is allowed to stay
    def allowed_url(m):
        u = m.group(0).rstrip(".,;:!?)]}>'\"")
        return " " if _keep_url(u) else m.group(0)
    text = _ANY_URL.sub(allowed_url, text)
    if "://" in text:
        return "url remains after scrubbing"
    text = re.sub(r"(?<![\w./-])" + re.escape(ALLOWED_SLUG) + r"(?![\w./-])", " ", text)
    text = re.sub(r"<(?:%s)>" % "|".join(re.escape(k) for k in PLACEHOLDERS), " ", text)
    for m in _SECRET_PHRASE.finditer(text):
        value = m.group(2)
        v = value.strip(".!?").lower()
        if v and not _known_placeholder(value) and v not in PLAIN_AFTER_IS:
            return "secret-like text remains after scrubbing"
    text = re.sub(r"<(?:%s)>" % "|".join(re.escape(k.strip("<>")) for k in _KNOWN_ANGLE), " ", text)
    plain = text
    text = _VERSION_WORDED.sub(" ", _VERSION_PLAIN.sub(" ", text))
    if _IPV4.search(text) or _IPV6.search(text):
        return "IP address remains after scrubbing"
    for m in _HOST_PORT_GATE.finditer(text):
        if m.group(1).lower() not in _PORT_LABELS:
            return "host:port remains after scrubbing"
    for raw in _GATE_SPLIT.split(text):
        reason = _unsafe_token(raw)
        if reason:
            return reason
    useful = re.sub(r"[\W_]+", "", re.sub(r"<(?:%s)>" % "|".join(re.escape(k) for k in PLACEHOLDERS), " ", plain))
    if len(useful) < MIN_USEFUL_CHARS:
        return "too little useful text survived scrubbing (%d characters)" % len(useful)
    return None
