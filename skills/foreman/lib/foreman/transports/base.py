"""Transport interface (architecture §5): how foreman reaches a terminal that hosts an agent session."""
from dataclasses import dataclass
from typing import List, Optional


class TerminalGone(Exception):
    """The terminal (pane/tab) behind a handle no longer exists."""


@dataclass
class TermInfo:
    handle: str
    title: str = ""
    cwd: str = ""
    agent: Optional[str] = None
    alive: bool = True
    idle: Optional[bool] = None  # None = the transport cannot tell


class Transport:
    """Implementations: orca, herdr, fake. All methods may raise TerminalGone for a dead handle
    (except list/alive/close)."""

    kind = "base"

    def list(self) -> List[TermInfo]:
        raise NotImplementedError

    def alive(self, handle: str) -> bool:
        raise NotImplementedError

    def launch(self, agent, model, effort, cwd, prompt, title) -> str:
        """Start an agent in a new terminal; returns the handle."""
        raise NotImplementedError

    def send(self, handle: str, text: str) -> None:
        """Type `text` and submit it. Raises TerminalGone."""
        raise NotImplementedError

    def read(self, handle: str, lines: int) -> str:
        raise NotImplementedError

    def wait_idle(self, handle: str, timeout: float) -> bool:
        """True when the terminal became idle within `timeout` seconds."""
        raise NotImplementedError

    def close(self, handle: str) -> None:
        raise NotImplementedError

    def rename(self, handle: str, title: str) -> None:
        raise NotImplementedError
