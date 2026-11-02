#!/usr/bin/env python3
"""Context-window monitor for agent sessions running in Orca terminals.

For every live Claude Code process it resolves:
  pid -> ~/.claude/sessions/<pid>.json (sessionId, name, status)
  pid -> /proc/<pid>/environ (ORCA_TERMINAL_HANDLE, ORCA_WORKTREE_ID)
  sessionId -> ~/.claude/projects/<cwd-slug>/<sessionId>.jsonl
and reads the usage of the last main-chain assistant message, which is the
current context size (input + cache_creation + cache_read + output).

Other agents in Orca terminals (antigravity, codex, ...) are listed with
ctx "?" because their context size is not exposed in a readable form.

Usage:
  ctx_monitor.py                 # table, all sessions
  ctx_monitor.py --json          # machine-readable
  ctx_monitor.py --cwd <path>    # only sessions whose cwd is under <path>
  ctx_monitor.py --threshold 150000
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
SESSIONS_DIR = HOME / ".claude" / "sessions"
PROJECTS_DIR = HOME / ".claude" / "projects"
ORCA = os.environ.get("ORCA_CLI_COMMAND") or str(HOME / ".config/orca/cli/bin/orca")
TAIL_BYTES = 4 * 1024 * 1024
LIMITS_DIR = HOME / ".cache" / "agent-limits"


def read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def limits() -> dict:
    """Subscription usage snapshots written by the Claude / agy statusline scripts."""
    now = time.time()
    out = {}
    c = read_json(LIMITS_DIR / "claude.json")
    if c:
        rl = c.get("rate_limits") or {}
        out["claude"] = {
            "ageSec": int(now - c.get("at", now)),
            "windows": {k: {"usedPct": v.get("used_percentage"),
                            "resetsInMin": int((v["resets_at"] - now) / 60) if v.get("resets_at") else None}
                        for k, v in rl.items() if isinstance(v, dict)},
        }
    a = read_json(LIMITS_DIR / "agy.json")
    if a:
        out["antigravity"] = {
            "ageSec": int(now - a.get("at", now)), "plan": a.get("plan_tier"),
            "windows": {k: {"usedPct": round(100 * (1 - v["remaining_fraction"]), 1)
                            if v.get("remaining_fraction") is not None else None,
                            "resetsInMin": int((v.get("reset_in_seconds") - (now - a.get("at", now))) / 60)
                            if v.get("reset_in_seconds") is not None else None}
                        for k, v in (a.get("quota") or {}).items() if isinstance(v, dict)},
        }
    return out


def proc_alive(pid: int, proc_start: str | None) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    if proc_start:
        # field 22 = starttime; comm (field 2) may contain spaces, split after ')'
        fields = stat.rsplit(")", 1)[1].split()
        if fields[19] != str(proc_start):
            return False
    return True


def proc_env(pid: int) -> dict:
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return {}
    env = {}
    for kv in raw.split(b"\0"):
        if b"=" in kv:
            k, v = kv.split(b"=", 1)
            if k.startswith(b"ORCA_"):
                env[k.decode()] = v.decode(errors="replace")
    return env


def cwd_slug(cwd: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def last_usage(transcript: Path):
    """Return (ctx_tokens, model, timestamp) of the last main-chain assistant turn."""
    try:
        size = transcript.stat().st_size
        with transcript.open("rb") as f:
            f.seek(max(0, size - TAIL_BYTES))
            lines = f.read().splitlines()
    except OSError:
        return None, None, None
    for line in reversed(lines):
        if b'"usage"' not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") != "assistant" or d.get("isSidechain"):
            continue
        msg = d.get("message") or {}
        model = msg.get("model")
        u = msg.get("usage")
        if not u or model == "<synthetic>":
            continue
        ctx = (u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
               + u.get("cache_read_input_tokens", 0) + u.get("output_tokens", 0))
        return ctx, model, d.get("timestamp")
    return None, None, None


def orca_terminals() -> dict:
    try:
        out = subprocess.run([ORCA, "terminal", "list", "--json"], capture_output=True,
                             text=True, timeout=20).stdout
        terms = json.loads(out)["result"]["terminals"]
    except Exception:
        return {}
    return {t["handle"]: t for t in terms}


def collect(cwd_filter: str | None):
    terms = orca_terminals()
    rows, seen_handles = [], set()
    for f in SESSIONS_DIR.glob("*.json"):
        try:
            s = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        pid = s.get("pid")
        if not pid or not proc_alive(pid, s.get("procStart")):
            continue
        cwd = s.get("cwd", "")
        if cwd_filter and not cwd.startswith(cwd_filter):
            continue
        env = proc_env(pid)
        handle = env.get("ORCA_TERMINAL_HANDLE")
        sid = s.get("sessionId")
        transcript = PROJECTS_DIR / cwd_slug(cwd) / f"{sid}.jsonl"
        ctx, model, ts = last_usage(transcript)
        t = terms.get(handle, {}) if handle else {}
        if handle:
            seen_handles.add(handle)
        rows.append({
            "agent": "claude", "pid": pid, "sessionId": sid, "name": s.get("name"),
            "status": s.get("status"), "cwd": cwd, "terminal": handle,
            "title": t.get("title"), "worktree": env.get("ORCA_WORKTREE_ID"),
            "model": model, "ctx": ctx, "lastTurn": ts, "transcript": str(transcript),
            "messagingSocket": s.get("messagingSocketPath"),
        })
    for h, t in terms.items():
        if h in seen_handles or not t.get("agentIdentity") or t.get("agentIdentity") == "claude":
            continue
        if cwd_filter and not (t.get("worktreePath") or "").startswith(cwd_filter):
            continue
        snap = read_json(LIMITS_DIR / "agy-ctx" / f"{h}.json") or {}
        cw = snap.get("context_window") or {}
        ctx = None
        if cw.get("used_percentage") is not None and cw.get("context_window_size"):
            ctx = int(cw["context_window_size"] * float(cw["used_percentage"]) / 100)
        rows.append({"agent": t["agentIdentity"], "terminal": h, "title": t.get("title"),
                     "cwd": t.get("worktreePath"), "ctx": ctx, "status": None,
                     "model": snap.get("model"), "ctxWindow": cw.get("context_window_size"),
                     "lastOutputAt": t.get("lastOutputAt")})
    rows.sort(key=lambda r: -(r.get("ctx") or 0))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--cwd")
    ap.add_argument("--threshold", type=int, default=150_000)
    a = ap.parse_args()
    rows = collect(a.cwd)
    for r in rows:
        r["overThreshold"] = bool(r.get("ctx") and r["ctx"] > a.threshold)
    if a.json:
        json.dump({"threshold": a.threshold, "generatedAt": int(time.time()), "limits": limits(),
                   "sessions": rows},
                  sys.stdout, ensure_ascii=False, indent=1)
        print()
        return
    for prov, l in limits().items():
        parts = [f"{k} {w['usedPct']}% used (reset {w['resetsInMin']}m)" for k, w in l["windows"].items()]
        print(f"LIMITS {prov} [snapshot {l['ageSec'] // 60}m old]: " + "; ".join(parts))
    print(f"{'agent':<11} {'ctx':>8} {'!':1} {'status':<7} {'model':<18} {'session':<9} {'terminal':<14} title")
    for r in rows:
        ctx = f"{r['ctx'] // 1000}k" if r.get("ctx") else "?"
        flag = "!" if r["overThreshold"] else ""
        term = (r.get("terminal") or "-")[:13]
        print(f"{r['agent']:<11} {ctx:>8} {flag:1} {(r.get('status') or '-'):<7} "
              f"{(r.get('model') or '-')[:18]:<18} {(r.get('sessionId') or '-')[:8]:<9} "
              f"{term:<14} {(r.get('title') or r.get('name') or '')[:50]}")


if __name__ == "__main__":
    main()
