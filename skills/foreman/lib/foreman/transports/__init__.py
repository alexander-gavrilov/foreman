"""Transports: `get_transport(kind)` returns a Transport instance from an explicit registry.

Each kind maps to (module, class). A module that does not exist yet (another work package) gives a clear
ValueError instead of an ImportError, and unregistered names can never trigger an arbitrary import."""
import importlib

REGISTRY = {
    "orca": ("foreman.transports.orca", "OrcaTransport"),
    "herdr": ("foreman.transports.herdr", "HerdrTransport"),
    "claude-msg": ("foreman.transports.claude_msg", "ClaudeMsgTransport"),
    "fake": ("foreman.transports.fake", "FakeTransport"),
}


def get_transport(kind):
    entry = REGISTRY.get(kind) if isinstance(kind, str) else None
    if entry is None:
        raise ValueError("unknown transport %r (known: %s)" % (kind, ", ".join(sorted(REGISTRY))))
    modname, clsname = entry
    try:
        mod = importlib.import_module(modname)
    except ModuleNotFoundError as exc:
        if exc.name and (modname == exc.name or modname.startswith(exc.name + ".")):
            raise ValueError("transport %r is not available: module %s does not exist yet" % (kind, modname))
        raise  # a dependency of the transport module is missing: a real bug, show it
    cls = getattr(mod, clsname, None)
    if cls is None:
        raise ValueError("transport %r is not available: %s has no %s" % (kind, modname, clsname))
    return cls()
