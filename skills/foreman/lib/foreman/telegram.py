"""Telegram Bot API channel (owner: WP6). Interface stub."""


def create_topic(name):
    """Create a forum topic in the machine's group; return its message_thread_id or None if not configured."""
    return None


def poll_once(timeout=0):
    """One getUpdates long-poll round: route topic messages / button presses to project chats. Returns count."""
    return 0
