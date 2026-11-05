"""Machine configuration. Defaults live in code; config.json stores only overrides (architecture §2.1)."""
import copy
import sys

from foreman import paths, store

DEFAULTS = {
    # kind=None -> derived from the platform; name is filled with the hostname when unset
    "machine": {"name": None, "kind": None},
    "web": {"bind": ["127.0.0.1"], "port": 8765, "base_url": None},
    # channels / mode=None -> derived from machine.kind
    "notify": {"channels": None},
    "telegram": {"chat_id": None, "api_base": "https://api.telegram.org", "allowed_user_ids": []},
    "upstream": {"mode": None, "repo": "alexander-gavrilov/foreman"},
    "thresholds": {"context_soft": 150000, "context_hard": 250000, "stuck_after_s": 1200,
                   "waiting_after_s": 900, "limit_warn": 70, "limit_stop": 90, "work_ttl_days": 14},
    # model routing tables are owned by the open/orchestrator work packages; empty until they fill them
    "routing": {"routine": [], "design": [], "review": [], "research": [], "orchestrator": []},
}


def default_machine_kind(platform=None):
    return "work" if (platform or sys.platform) == "darwin" else "home"


def deep_merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load(platform=None):
    """Defaults + overrides from config.json, with derived defaults filled in. A fresh dict every call."""
    overrides = store.read_json(paths.config_file(), {})
    if not isinstance(overrides, dict):
        store.quarantine(paths.config_file(), "not a JSON object")
    cfg = deep_merge(DEFAULTS, overrides)
    machine = cfg["machine"]
    if not machine.get("kind"):
        machine["kind"] = default_machine_kind(platform)
    if not machine.get("name"):
        import socket
        machine["name"] = socket.gethostname().split(".")[0] or "machine"
    home = machine["kind"] == "home"
    if not cfg["upstream"].get("mode"):
        cfg["upstream"]["mode"] = "auto" if home else "draft"
    if not cfg["notify"].get("channels"):
        cfg["notify"]["channels"] = ["telegram"] if home else ["macos"]
    return cfg


def get(key, default=None, platform=None):
    """Dotted lookup, e.g. get('thresholds.context_soft')."""
    node = load(platform)
    for part in key.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return default
    return node
