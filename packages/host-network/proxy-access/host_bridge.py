"""Mutually authenticated host bridge; never logs payloads or credentials."""

import argparse
import concurrent.futures
import http.client
import http.server
import json
import re
import socket
import socketserver
import ssl
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit


HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade",
}
NATIVE_ROUTES = {
    ("GET", "/v1/models"), ("POST", "/v1/responses"),
    ("POST", "/v1/chat/completions"),
    ("POST", "/agency/mcp"), ("DELETE", "/agency/mcp"),
    ("POST", "/agency/policy"), ("POST", "/agency/plugins"),
    ("GET", "/metadata/identity/oauth2/token"),
}
IMDS_ROUTE = ("GET", "/metadata/identity/oauth2/token")
IMDS_API_VERSION = "2019-08-01"
IMDS_RESOURCES = {
    "499b84ac-1321-427f-aa17-267ca6975798",
    "https://management.azure.com/",
}
BEARER = re.compile(r"Bearer [A-Za-z0-9._~+/-]+=*", re.IGNORECASE)


def reject_json_constant(value):
    raise ValueError("Invalid JSON constant.")


class ModelHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.relay()

    def do_POST(self):
        self.relay()

    def read_chunked(self, limit):
        body = bytearray()
        while True:
            line = self.rfile.readline(8193)
            if len(line) > 8192 or not line.endswith(b"\r\n"):
                raise ValueError("Invalid chunk framing.")
            size_text = line[:-2].split(b";", 1)[0]
            if not size_text or len(size_text) > 16 or any(
                    byte not in b"0123456789abcdefABCDEF" for byte in size_text):
                raise ValueError("Invalid chunk size.")
            size = int(size_text, 16)
            if size > limit - len(body):
                raise OverflowError("Request body limit exceeded.")
            if size == 0:
                if self.rfile.readline(8193) != b"\r\n":
                    raise ValueError("Request trailers are not supported.")
                return bytes(body)
            chunk = self.rfile.read(size + 2)
            if len(chunk) != size + 2 or not chunk.endswith(b"\r\n"):
                raise ValueError("Invalid chunk framing.")
            body.extend(chunk[:-2])

    def relay(self):
        allowed = (self.command, self.path) in {
            ("GET", "/health"), ("GET", "/v1/models"), ("POST", "/v1/responses"),
        }
        if not allowed:
            self.send_error(403)
            return
        lengths = self.headers.get_all("Content-Length", [])
        encodings = self.headers.get_all("Transfer-Encoding", [])
        if len(lengths) > 1 or len(encodings) > 1 or (lengths and encodings):
            self.send_error(400)
            return
        if encodings and encodings[0].strip().lower() != "chunked":
            self.send_error(400)
            return
        try:
            length = int(lengths[0]) if lengths else 0
        except ValueError:
            self.send_error(400)
            return
        limit = self.server.settings.get("max_request_bytes", 30000000)
        if length < 0 or length > limit:
            self.send_error(413)
            return
        if self.command == "POST" and not lengths and not encodings:
            self.send_error(411)
            return
        try:
            body = self.read_chunked(limit) if encodings else self.rfile.read(length)
        except ValueError:
            self.send_error(400)
            return
        except OverflowError:
            self.send_error(413)
            return
        if not encodings and len(body) != length:
            self.close_connection = True
            return
        length = len(body)
        settings = self.server.settings
        upstream = http.client.HTTPConnection(
            settings["llm_host"], settings.get("llm_port", 4141), timeout=1800,
        )
        started = False
        try:
            headers = {
                "Authorization": "Bearer " + self.server.gateway_key,
                "Content-Length": str(length),
            }
            if self.headers.get("Content-Type"):
                headers["Content-Type"] = self.headers["Content-Type"]
            upstream.request(self.command, self.path, body=body, headers=headers)
            response = upstream.getresponse()
            self.send_response(response.status)
            for name, value in response.getheaders():
                if name.lower() not in HOP_HEADERS | {"content-length", "server", "date"}:
                    self.send_header(name, value)
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
            self.end_headers()
            started = True
            while chunk := response.read1(65536):
                self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (OSError, http.client.HTTPException):
            if not started:
                self.send_error(502)
        finally:
            self.close_connection = True
            upstream.close()


class AdoHandler(socketserver.BaseRequestHandler):
    def handle(self):
        settings = self.server.settings
        process = subprocess.Popen(
            [settings["windows_python"], "-u", settings["windows_relay"]],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        self.request.settimeout(1800)

        def upload():
            try:
                while data := self.request.recv(65536):
                    process.stdin.write(data)
                    process.stdin.flush()
            except (OSError, BrokenPipeError):
                pass
            finally:
                process.stdin.close()

        thread = threading.Thread(target=upload, daemon=True)
        thread.start()
        try:
            while data := process.stdout.read1(65536):
                self.request.sendall(data)
        except (OSError, BrokenPipeError):
            pass
        finally:
            try:
                self.request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            process.stdout.close()
            thread.join(timeout=5)


class AgencyHandler(ModelHandler):
    def do_DELETE(self):
        self.relay()

    def relay(self):
        if (self.path not in {"/agency/plugins", "/agency/mcp", "/agency/policy"}
                or self.command not in {"POST", "DELETE"}):
            return self.send_error(403)
        try:
            length = int(self.headers.get("Content-Length", "-1"))
            if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
                body = self.read_chunked(8_000_000)
            elif 0 <= length <= 8_000_000 and not self.headers.get("Transfer-Encoding"):
                body = self.rfile.read(length)
            else:
                return self.send_error(413)
            message = {"method": self.command, "path": self.path,
                       "body": json.loads(body) if body else {},
                       "actor": self.headers.get("X-Agency-Actor"),
                       "actorUID": self.headers.get("X-Agency-Actor-Uid"),
                       "session": self.headers.get("Mcp-Session-Id")}
            result = subprocess.run([self.server.settings["windows_python"], "-u",
                                     self.server.settings["windows_agency_relay"]],
                                    input=json.dumps(message).encode(), capture_output=True, timeout=200)
            if result.returncode or len(result.stdout) > 8_100_000:
                return self.send_error(502)
            reply = json.loads(result.stdout)
            data = reply["body"].encode()
            self.send_response(reply["status"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            if reply.get("session"):
                self.send_header("Mcp-Session-Id", reply["session"])
            self.end_headers()
            self.wfile.write(data)
        except (ValueError, OSError, subprocess.TimeoutExpired):
            self.send_error(502)
        finally:
            self.close_connection = True


class NativeRequestError(Exception):
    def __init__(self, status):
        self.status = status


class NativeHandler(ModelHandler):
    def setup(self):
        super().setup()
        timeout = self.server.settings.get("request_timeout_seconds", 60)
        self.connection.settimeout(timeout)
        self.request_deadline = time.monotonic() + timeout
        self.request_expired = False
        self.request_timer = threading.Timer(timeout, self.expire_request)
        self.request_timer.daemon = True
        self.request_timer.start()

    def expire_request(self):
        self.request_expired = True
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def finish(self):
        self.request_timer.cancel()
        try:
            super().finish()
        except OSError:
            pass

    def do_DELETE(self):
        self.relay()

    def reject_method(self):
        self.send_error(403)
        self.close_connection = True

    do_CONNECT = do_HEAD = do_OPTIONS = do_PATCH = do_PUT = do_TRACE = reject_method

    def handle_expect_100(self):
        self.send_error(417)
        self.close_connection = True
        return False

    def single_header(self, name, limit):
        values = self.headers.get_all(name, [])
        if len(values) > 1:
            raise NativeRequestError(400)
        if not values:
            return None
        value = values[0]
        if len(value) > limit or any(ord(char) < 32 or ord(char) > 126 for char in value):
            raise NativeRequestError(400)
        return value

    def request_body(self, limit):
        lengths = self.headers.get_all("Content-Length", [])
        encodings = self.headers.get_all("Transfer-Encoding", [])
        if len(lengths) > 1 or len(encodings) > 1 or (lengths and encodings):
            raise NativeRequestError(400)
        if encodings and encodings[0].strip().lower() != "chunked":
            raise NativeRequestError(400)
        if lengths and not re.fullmatch(r"[0-9]+", lengths[0].strip()):
            raise NativeRequestError(400)
        if lengths and len(lengths[0].strip()) > 10:
            raise NativeRequestError(413)
        length = int(lengths[0]) if lengths else 0
        if length > limit:
            raise NativeRequestError(413)
        if self.command != "POST" and (length or encodings):
            raise NativeRequestError(400)
        if self.command == "POST" and not lengths and not encodings:
            raise NativeRequestError(411)
        try:
            body = self.read_chunked(limit) if encodings else self.rfile.read(length)
        except OverflowError:
            raise NativeRequestError(413) from None
        except ValueError:
            raise NativeRequestError(400) from None
        if not encodings and len(body) != length:
            raise NativeRequestError(400)
        return body

    def relay(self):
        self.response_started = False
        request_complete = False
        upstream = None
        try:
            target = urlsplit(self.path)
            route = (self.command, target.path)
            if target.scheme or target.netloc or target.fragment or route not in NATIVE_ROUTES:
                raise NativeRequestError(403)
            if target.query and route != IMDS_ROUTE:
                raise NativeRequestError(403)
            if route == IMDS_ROUTE:
                try:
                    query = parse_qsl(target.query, keep_blank_values=True, strict_parsing=True)
                except ValueError:
                    raise NativeRequestError(403) from None
                values = dict(query)
                if (len(query) != 2 or values.get("api-version") != IMDS_API_VERSION
                        or values.get("resource") not in IMDS_RESOURCES):
                    raise NativeRequestError(403)
            if self.requestline.split()[1] != self.path:
                raise NativeRequestError(403)
            if len(self.headers) > 64 or sum(len(k) + len(v) for k, v in self.headers.items()) > 65536:
                raise NativeRequestError(431)
            imds = route == IMDS_ROUTE
            authorization = self.single_header("Authorization", 8192)
            identity_header = self.single_header("X-IDENTITY-HEADER", 8192)
            credential = identity_header if imds else authorization
            if (not credential or not BEARER.fullmatch(credential)
                    or imds and authorization is not None
                    or not imds and identity_header is not None):
                raise NativeRequestError(401)
            content_type = self.single_header("Content-Type", 128)
            if content_type and not re.fullmatch(r"application/json(?:\s*;\s*charset=utf-8)?", content_type, re.I):
                raise NativeRequestError(415)
            if self.headers.get_all("Content-Encoding") or self.headers.get_all("Trailer"):
                raise NativeRequestError(400)
            traceparent = self.single_header("traceparent", 55)
            tracestate = self.single_header("tracestate", 512)
            if traceparent and not re.fullmatch(r"[0-9a-f]{2}-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}", traceparent):
                raise NativeRequestError(400)
            session = self.single_header("Mcp-Session-Id", 256)
            if session and not re.fullmatch(r"[A-Za-z0-9_.~-]+", session):
                raise NativeRequestError(400)
            if imds and (content_type or session):
                raise NativeRequestError(400)
            agency = target.path.startswith("/agency/")
            limit = min(self.server.settings.get("max_request_bytes", 30_000_000),
                        8_000_000 if agency else 30_000_000)
            body = self.request_body(limit)
            request_complete = True
            self.request_timer.cancel()
            if self.request_expired or time.monotonic() >= self.request_deadline:
                raise NativeRequestError(408)
            if agency or imds:
                self.relay_windows(body, authorization, session, identity_header)
                return
            timeout = self.server.settings.get("response_timeout_seconds", 1800)
            deadline = time.monotonic() + timeout
            upstream = http.client.HTTPConnection(
                self.server.settings["llm_host"], self.server.settings.get("llm_port", 4141), timeout=timeout)
            headers = {"Authorization": authorization, "Content-Length": str(len(body))}
            if content_type:
                headers["Content-Type"] = content_type
            if traceparent:
                headers["traceparent"] = traceparent
            if tracestate:
                headers["tracestate"] = tracestate
            upstream.request(self.command, self.path, body=body, headers=headers)
            upstream_socket = upstream.sock
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Native response deadline exceeded.")
            upstream_socket.settimeout(remaining)
            response = upstream.getresponse()
            if not 200 <= response.status <= 599:
                raise NativeRequestError(502)
            maximum = self.server.settings.get("max_response_bytes", 128_000_000)
            if response.length is not None and response.length > maximum:
                raise NativeRequestError(502)
            self.send_response(response.status)
            safe = {"cache-control", "content-encoding", "content-type", "request-id",
                    "retry-after", "traceparent", "tracestate", "www-authenticate", "x-request-id"}
            hop = HOP_HEADERS | {part.strip().lower() for part in response.getheader("Connection", "").split(",")}
            for name, value in response.getheaders():
                if name.lower() in safe - hop and "\r" not in value and "\n" not in value:
                    self.send_header(name, value)
            if response.status not in {204, 304}:
                self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
            self.response_started = True
            self.end_headers()
            if response.status in {204, 304}:
                return
            total = 0
            while True:
                if response.isclosed():
                    if response.length not in {None, 0}:
                        raise OSError("Truncated native response.")
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Native response deadline exceeded.")
                upstream_socket.settimeout(remaining)
                chunk = response.read1(min(65536, maximum - total + 1))
                if not chunk:
                    if response.length not in {None, 0}:
                        raise OSError("Truncated native response.")
                    break
                total += len(chunk)
                if total > maximum:
                    raise OSError("Native response limit exceeded.")
                self.connection.settimeout(remaining)
                self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except NativeRequestError as error:
            if not self.response_started and not self.request_expired:
                self.send_error(error.status)
        except (OSError, http.client.HTTPException, subprocess.TimeoutExpired) as error:
            if (not request_complete and isinstance(error, TimeoutError)
                    or time.monotonic() >= self.request_deadline):
                self.request_expired = True
            if not self.response_started and not self.request_expired:
                self.send_error(502)
        finally:
            self.close_connection = True
            if upstream:
                upstream.close()

    def relay_windows(self, body, authorization, session, identity_header=None):
        try:
            content = json.loads(body, parse_constant=reject_json_constant) if body else {}
            message = {"method": self.command, "path": self.path, "body": content,
                       "session": session}
            if identity_header is not None:
                message["identityHeader"] = identity_header
            else:
                message["authorization"] = authorization
            encoded = json.dumps(message, allow_nan=False).encode()
        except (ValueError, UnicodeError, RecursionError):
            raise NativeRequestError(400) from None
        if len(encoded) > 8_100_000:
            raise NativeRequestError(413)
        result = subprocess.run(
            [self.server.settings["windows_python"], "-u", self.server.settings["windows_agency_relay"]],
            input=encoded, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=200)
        if result.returncode or len(result.stdout) > 8_100_000:
            raise NativeRequestError(502)
        try:
            reply = json.loads(result.stdout)
            if type(reply["status"]) is not int or not 200 <= reply["status"] <= 599:
                raise ValueError()
            if not isinstance(reply["body"], str):
                raise ValueError()
            data = reply["body"].encode()
            identifier = reply.get("session")
            if len(data) > 8_000_000 or (identifier is not None and
                    (not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_.~-]{1,256}", identifier))):
                raise ValueError()
        except (KeyError, TypeError, ValueError, UnicodeError):
            raise NativeRequestError(502) from None
        self.send_response(reply["status"])
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        if identifier:
            self.send_header("Mcp-Session-Id", identifier)
        self.response_started = True
        self.end_headers()
        self.wfile.write(data)


class TlsServer(socketserver.TCPServer):
    allow_reuse_address = True

    def __init__(self, address, handler, context, settings, gateway_key=""):
        self.context = context
        self.settings = settings
        self.gateway_key = gateway_key
        self.slots = threading.BoundedSemaphore(settings.get("max_connections", 32))
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=settings.get("max_connections", 32))
        super().__init__(address, handler)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        self.pool.submit(self.serve_request, request, address)

    def serve_request(self, request, address):
        try:
            request.settimeout(15)
            with self.context.wrap_socket(request, server_side=True) as secured:
                secured.settimeout(1800)
                self.finish_request(secured, address)
        except (OSError, ssl.SSLError):
            pass
        finally:
            request.close()
            self.slots.release()


def tls_context(certificate, key, client_ca=None):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certificate, key)
    if client_ca:
        context.load_verify_locations(cafile=client_ca)
        context.verify_mode = ssl.CERT_REQUIRED
    return context


def native_server(settings):
    native = settings.get("native_frontend", {})
    if not isinstance(native, dict) or type(native.get("enabled", False)) is not bool:
        raise ValueError("Invalid native frontend configuration.")
    if not native.get("enabled", False):
        return None
    if (native.get("listen_address", "10.82.0.2") != "10.82.0.2"
            or native.get("listen_port", 15445) != 15445 or not settings.get("windows_agency_relay")):
        raise ValueError("Invalid native frontend binding.")
    limits = {"max_connections": (8, 32), "max_request_bytes": (30_000_000, 30_000_000),
              "max_response_bytes": (128_000_000, 128_000_000), "request_timeout_seconds": (60, 60),
              "response_timeout_seconds": (1800, 1800)}
    if set(native) - {"enabled", "managed_by", "listen_address", "listen_port",
                      "server_certificate", "server_key", *limits}:
        raise ValueError("Unsupported native frontend configuration.")
    for name, (default, maximum) in limits.items():
        value = native.get(name, default)
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError("Invalid native frontend limit.")
    context = tls_context(native["server_certificate"], native["server_key"])
    return TlsServer(("10.82.0.2", 15445), NativeHandler, context,
                     {**settings, **{name: native.get(name, default) for name, (default, _) in limits.items()}})


def legacy_ado_server(settings, context):
    enabled = settings.get("legacy_ado_frontend_enabled", True)
    if type(enabled) is not bool:
        raise ValueError("Invalid legacy ADO frontend configuration.")
    if not enabled:
        return None
    return TlsServer((settings["listen_address"], settings.get("ado_listen_port", 15480)),
                     AdoHandler, context, settings)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    settings = json.loads(Path(args.config).read_text())
    context = tls_context(settings["server_certificate"], settings["server_key"], settings["client_ca"])
    key = subprocess.run(
        settings["gateway_key_command"], check=True, capture_output=True, timeout=15,
    ).stdout.decode("utf-8-sig").strip()
    if len(key) < 32 or any(c.isspace() for c in key):
        raise ValueError("Invalid host gateway key.")
    llm = TlsServer((settings["listen_address"], settings.get("llm_listen_port", 15441)),
                    ModelHandler, context, settings, key)
    ado = legacy_ado_server(settings, context)
    if settings.get("windows_agency_relay"):
        broker = TlsServer((settings["listen_address"], 15442), AgencyHandler, context,
                           {**settings, "max_connections": 4})
        threading.Thread(target=broker.serve_forever, daemon=True).start()
    native = native_server(settings)
    if native:
        threading.Thread(target=native.serve_forever, daemon=True).start()
    threading.Thread(target=llm.serve_forever, daemon=True).start()
    print("Authenticated native host bridges ready.", flush=True)
    if ado:
        ado.serve_forever()
    else:
        threading.Event().wait()


if __name__ == "__main__":
    main()
