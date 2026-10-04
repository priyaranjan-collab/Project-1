"""Run separately with `python socket_server.py` to observe event messages."""
import json
import socketserver


class EventHandler(socketserver.StreamRequestHandler):
    def handle(self):
        line = self.rfile.readline(65536)
        try:
            event = json.loads(line.decode("utf-8"))
            print(f"Received event: {event}", flush=True)
        except (UnicodeDecodeError, json.JSONDecodeError):
            print("Received an invalid event message", flush=True)


if __name__ == "__main__":
    with socketserver.ThreadingTCPServer(("127.0.0.1", 5055), EventHandler) as server:
        server.daemon_threads = True
        print("Event socket listening on 127.0.0.1:5055", flush=True)
        server.serve_forever()