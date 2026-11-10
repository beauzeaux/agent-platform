import socket
import ssl
import sys

try:
    context = ssl.create_default_context(cafile=sys.argv[2])
    version = ssl.TLSVersion.TLSv1_2 if sys.argv[5] == "12" else ssl.TLSVersion.TLSv1_3
    context.minimum_version = version
    context.maximum_version = version
    context.load_cert_chain(sys.argv[3], sys.argv[4])
    context.set_alpn_protocols(["h2"])
    with socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=10) as raw:
        with context.wrap_socket(raw, server_hostname="localhost") as remote:
            assert remote.selected_alpn_protocol() == "h2"
            remote.sendall(b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n" + b"\x00\x00\x00\x04\x00\x00\x00\x00\x00")
            header = b""
            while len(header) < 9:
                data = remote.recv(9 - len(header))
                if not data:
                    raise ConnectionError()
                header += data
            assert header[3] == 4
            print("TLS_OK", flush=True)
except ssl.SSLError as error:
    status = "TLS_UNSUPPORTED_CERTIFICATE" if error.reason == "SSLV3_ALERT_UNSUPPORTED_CERTIFICATE" else "TLS_FAILURE"
    print(status, flush=True)
    sys.exit(2)
except Exception:
    print("TLS_FAILURE", flush=True)
    sys.exit(2)
