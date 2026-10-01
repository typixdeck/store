"""Pure action availability; launch never depends on remote installation trust."""

from .catalog import ICON_RE


def icon_name(app):
    value = app.raw.get("icon")
    if isinstance(value, str) and ICON_RE.fullmatch(value):
        return value
    fallback = {"typix-reader": "accessories-text-editor", "typix-gamer": "applications-games", "typix-myai": "audio-input-microphone"}
    return fallback.get(app.package, "application-x-executable")


def actions(*, installed, update, source_error, incompatible, busy, desktop_entry):
    return {
        "launch_visible": bool(installed),
        "launch_enabled": bool(installed and desktop_entry and not busy),
        "install_visible": not installed or bool(update),
        "install_enabled": not busy and not source_error and not incompatible and (not installed or bool(update)),
    }
