"""Best-effort event message sender. The web app works if no server is running."""
import json
import socket


def send_event(payload, host="127.0.0.1", port=5055, timeout=0.25):
    try:
        message = json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"
        with socket.create_connection((host, port), timeout=timeout) as connection:
            connection.sendall(message)
        return True
    except (OSError, TypeError, ValueError):
        return False