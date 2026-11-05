"""Findings (spec 4.11, architecture 11): project findings go to the project's lessons.md; foreman findings are
scrubbed, then sent as a GitHub issue / saved as a draft / kept locally, depending on `upstream.mode`.

A finding is findings/<id>.json: {id, ts, title, body, status, url} (+ kind, error). status:
sent | draft | off | failed. The `gh` call goes through an injectable runner: runner(argv) -> (rc, out, err)."""
import os
import secrets
import subprocess

from foreman import config, paths, scrub, store, timeutil

DEFAULT_REPO = "alexander-gavrilov/foreman"


def default_runner(argv):
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr


def _title(body):
    for line in body.splitlines():
        line = line.strip()
        if line and line != "<content omitted>":
            return line[:80]
    return "foreman finding"


def save(finding):
    store.write_json(paths.finding_file(finding["id"]), finding)
    return finding


def load(fid):
    data = store.read_json(paths.finding_file(fid))
    return data if isinstance(data, dict) else None


def list_findings():
    out = []
    try:
        names = sorted(os.listdir(paths.findings_dir()))
    except OSError:
        return out
    for name in names:
        if name.endswith(".json"):
            data = store.read_json(os.path.join(paths.findings_dir(), name))
            if isinstance(data, dict) and data.get("id"):
                out.append(data)
    out.sort(key=lambda f: (f.get("ts") or "", f["id"]))
    return out


def _gh_create(finding, repo, runner):
    rc, out, err = (runner or default_runner)(
        ["gh", "issue", "create", "-R", repo, "--title", finding["title"], "--body", finding["body"]])
    if rc == 0:
        lines = [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
        finding.update(status="sent", url=lines[-1] if lines else None)
        finding.pop("error", None)
    else:
        finding.update(status="failed", error=((err or out or "gh failed").strip().splitlines() or ["gh failed"])[-1])
    return finding


def report_foreman(text, runner=None, ctx=None):
    """Scrub, store and (mode auto) send. Returns the finding dict."""
    body = scrub.scrub(text, ctx)
    cfg = config.load()
    mode = cfg["upstream"]["mode"]
    finding = {"id": "f-" + secrets.token_hex(4), "ts": timeutil.now_iso(), "title": _title(body), "body": body,
               "status": {"draft": "draft", "off": "off"}.get(mode, "draft"), "url": None, "kind": "foreman"}
    if mode == "auto":
        _gh_create(finding, cfg["upstream"].get("repo") or DEFAULT_REPO, runner)
    return save(finding)


def send(fid, runner=None):
    """The user sends a draft (or retries a failed one). Returns the finding, or None if unknown."""
    finding = load(fid)
    if finding is None:
        return None
    if finding.get("status") == "sent":
        return finding
    _gh_create(finding, config.load()["upstream"].get("repo") or DEFAULT_REPO, runner)
    return save(finding)


def report_project(slug, text):
    """Append a dated entry to projects/<slug>/lessons.md."""
    path = paths.lessons_file(slug)
    lines = text.strip().splitlines() or [""]
    entry = "- %s %s\n" % (timeutil.now().strftime("%Y-%m-%d"), lines[0])
    entry += "".join("  %s\n" % ln for ln in lines[1:])
    with store.locked(path):
        new = not os.path.exists(path)
        with open(path, "a", encoding="utf-8") as f:
            if new:
                f.write("# Lessons\n\n")
            f.write(entry)
    return path
