"""macOS desktop notifications (owner: WP6, architecture §10).
Uses terminal-notifier if present on PATH, else osascript display notification.
No click-through on osascript, so URL is embedded in the text.
Command runner is injectable for tests.
Failures never raise; they return a result dict.
"""
import shutil
import subprocess


def notify(title, text, url=None, runner=None):
    """Deliver a macOS desktop notification.

    Args:
        title: Notification title string.
        text: Notification message body.
        url: Optional click-through URL or reference.
        runner: Optional command runner function (cmd_list -> None).

    Returns:
        dict: {"ok": bool, "backend": str, ...}
    """
    cmd = []
    backend = None
    try:
        bin_path = shutil.which("terminal-notifier")
        if bin_path:
            backend = "terminal-notifier"
            cmd = ["terminal-notifier", "-title", title, "-message", text]
            if url:
                cmd.extend(["-open", url])
        else:
            backend = "osascript"
            full_text = f"{text} ({url})" if url else text
            escaped_msg = full_text.replace("\\", "\\\\").replace('"', '\\"')
            escaped_title = title.replace("\\", "\\\\").replace('"', '\\"')
            script = f'display notification "{escaped_msg}" with title "{escaped_title}"'
            cmd = ["osascript", "-e", script]

        if runner is not None:
            runner(cmd)
        else:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=10)

        return {"ok": True, "backend": backend, "cmd": cmd}
    except Exception as exc:
        return {"ok": False, "backend": backend, "error": str(exc), "cmd": cmd}
