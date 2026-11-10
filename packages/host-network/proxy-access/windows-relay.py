"""Credential-free stdio transport to the Windows loopback ADO proxy."""

import socket
import sys
import threading


def main():
    with socket.create_connection(("127.0.0.1", 5480), timeout=10) as remote:
        remote.settimeout(None)

        def send():
            try:
                while data := sys.stdin.buffer.read1(65536):
                    remote.sendall(data)
                remote.shutdown(socket.SHUT_WR)
            except OSError:
                pass

        threading.Thread(target=send, daemon=True).start()
        try:
            while data := remote.recv(65536):
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
        except (OSError, BrokenPipeError):
            pass


if __name__ == "__main__":
    main()
