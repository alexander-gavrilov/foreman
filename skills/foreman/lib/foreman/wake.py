"""Wake the project's orchestrator (owner: WP1). Interface stub."""


def wake(project, text, key=None):
    """Queue a one-line event for the orchestrator of `project` and try to deliver it if it is idle.
    `key` deduplicates: the same key is queued at most once until delivered. Returns True if queued."""
    return False
