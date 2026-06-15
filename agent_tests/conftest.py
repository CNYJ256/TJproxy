from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading


@contextmanager
def fake_tjproxy(responses):
    queue = list(responses)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/v1/models":
                self.send_error(404)
                return
            self._send_json(
                {
                    "object": "list",
                    "data": [{"id": "tongji-agent", "object": "model"}],
                }
            )

        def do_POST(self):
            if self.path != "/v1/chat/completions":
                self.send_error(404)
                return
            length = int(self.headers["Content-Length"])
            self.server.requests.append(json.loads(self.rfile.read(length)))
            if not queue:
                self.send_error(500, "no queued response")
                return
            content = queue.pop(0)
            self._send_json(
                {
                    "choices": [
                        {"message": {"role": "assistant", "content": content}}
                    ]
                }
            )

        def _send_json(self, value):
            body = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", server
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
