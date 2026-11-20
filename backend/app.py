from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json

SERVICE = "autonomous-cfo-backend"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/", "/health"):
            body = json.dumps({"service": SERVICE, "error": "not found"}).encode()
            self._send(404, body)
            return
        body = json.dumps({"service": SERVICE, "status": "ok"}).encode()
        self._send(200, body)

    def _send(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)


if __name__ == "__main__":
    print("%s listening on 8080" % SERVICE, flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
