"""Strip project data from a text before it can leave the machine (spec 4.11, architecture 11).

Replaced: home dir and registered project paths -> <path>; project names/slugs -> <project>; session ids and
hex ids -> <id>; emails -> <email>; machine name and hostnames -> <host>; URLs (except the foreman repo)
-> <url>; fenced code blocks and lines that look like file content -> <content omitted>.
Internally every placeholder is a private-use sentinel, so a project named "id" cannot corrupt a placeholder.
"""
import os
import re
import socket

ALLOWED_URL_PREFIX = "https://github.com/alexander-gavrilov/foreman"
PRODUCT_NAME = "foreman"  # a project called "foreman" is the public product itself: its name is not data

_L, _R = "\ue000", "\ue001"
PLACEHOLDERS = {k: "<%s>" % k for k in ("path", "project", "id", "email", "host", "url", "content omitted")}


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


def _word(name):
    return r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])"


def scrub(text, ctx=None):
    """Return `text` with project data removed. `ctx` (see default_context) defaults to this machine's."""
    if ctx is None:
        ctx = default_context()
    text = _omit_content(text.replace("\r\n", "\n"))

    # URLs first: a path or host inside a URL goes with it; the foreman repo stays readable.
    keep = []

    def url(m):
        u = m.group(0).rstrip(".,;:!?)]}>'\"")
        tail = m.group(0)[len(u):]
        if u == ALLOWED_URL_PREFIX or u.startswith(ALLOWED_URL_PREFIX + "/"):
            keep.append(u)
            return _L + chr(0xE400 + len(keep) - 1) + _R + tail
        return _tok("url") + tail
    text = re.sub(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s<>\"'`]+", url, text)

    text = re.sub(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", _tok("email"), text)

    # paths: swallow the rest of the path too (sub-paths and file names are project data)
    roots = sorted(set(ctx.get("paths", [])), key=len, reverse=True) + \
        sorted(set(ctx.get("homes", [])), key=len, reverse=True)
    for root in roots:
        root = root.rstrip("/")
        if root:
            text = re.sub(re.escape(root) + r"(?![A-Za-z0-9_.-])(?:/[^\s'\"`)\]>,;:]*)?", _tok("path"), text)
    text = re.sub(r"(?<![\w/])~/[^\s'\"`)\]>,;:]*", _tok("path"), text)

    for name in sorted(set(ctx.get("names", [])), key=len, reverse=True):
        if name and name.lower() != PRODUCT_NAME:
            text = re.sub(_word(name), _tok("project"), text, flags=re.I)

    text = re.sub(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
                  _tok("id"), text)
    text = re.sub(r"(?<![A-Za-z0-9])s-[0-9a-f]{6}(?![A-Za-z0-9])", _tok("id"), text)
    text = re.sub(r"(?<![A-Za-z0-9])[0-9a-fA-F]{8,}(?![A-Za-z0-9])", _tok("id"), text)

    text = re.sub(r"(?<![A-Za-z0-9])[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.(?:lan|local|internal|corp|home\.arpa)"
                  r"(?![A-Za-z0-9])", _tok("host"), text, flags=re.I)

    for host in sorted(set(ctx.get("hosts", [])), key=len, reverse=True):
        if host and len(host) > 1:
            text = re.sub(_word(host), _tok("host"), text, flags=re.I)
    for i, u in enumerate(keep):
        text = text.replace(_L + chr(0xE400 + i) + _R, u)
    return _finalize(text)
