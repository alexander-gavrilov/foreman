"""User notifications (owner: WP6). Interface stub."""


def notify(project, text, msg_id=None, question=False, options=None):
    """Notify the user about a chat message of `project` (None = machine-level, Telegram General topic)
    through the configured channels. The link is <web.base_url>/#/p/<project>/m/<msg_id>. Returns list of
    channel results."""
    return []
