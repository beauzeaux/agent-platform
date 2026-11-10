import argparse
import http.server
import json
import os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--state", type=Path, required=True)
parser.add_argument("--port", type=int, required=True)
args, _ = parser.parse_known_args()
(args.state / "owned-child.pid").write_text(str(os.getpid()))


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        body = json.dumps({"ready": True, "brokerPid": os.getpid(), "sessions": 0,
                           "sessionsOpened": 0, "sessionsClosed": 0, "sessionsExpired": 0}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
