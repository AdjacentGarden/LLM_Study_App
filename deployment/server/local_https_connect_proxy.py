"""Tiny localhost-only HTTPS CONNECT proxy for a private SSH reverse tunnel.

The GPU host has no direct outbound route in the preview environment. Run this
on the operator machine and reverse-forward its localhost port over SSH. Only
CONNECT requests to TCP 443 are accepted; regular HTTP requests are rejected.
"""

from __future__ import annotations

import argparse
import socket
import socketserver
import threading


class ConnectHandler(socketserver.BaseRequestHandler):
    timeout = 180

    @staticmethod
    def relay(source: socket.socket, destination: socket.socket) -> None:
        """Copy one direction with blocking backpressure and a bounded idle timeout."""
        try:
            while block := source.recv(65_536):
                destination.sendall(block)
        except (OSError, TimeoutError):
            pass
        finally:
            try:
                destination.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    def handle(self) -> None:
        self.request.settimeout(self.timeout)
        request = bytearray()
        while b"\r\n\r\n" not in request and len(request) < 16_384:
            block = self.request.recv(4096)
            if not block:
                return
            request.extend(block)
        try:
            line = bytes(request).split(b"\r\n", 1)[0].decode("ascii")
            method, authority, _ = line.split(" ", 2)
            host, port_text = authority.rsplit(":", 1)
            port = int(port_text)
        except (UnicodeDecodeError, ValueError):
            self.request.sendall(
                b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n"
            )
            return
        if method != "CONNECT" or port != 443 or not host or len(host) > 253:
            self.request.sendall(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
            return
        try:
            upstream = socket.create_connection((host, port), timeout=self.timeout)
        except OSError:
            self.request.sendall(
                b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n"
            )
            return
        with upstream:
            self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            upstream.settimeout(self.timeout)
            outgoing = threading.Thread(
                target=self.relay,
                args=(self.request, upstream),
                daemon=True,
            )
            outgoing.start()
            self.relay(upstream, self.request)
            outgoing.join(timeout=1)


class ThreadedServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18081)
    args = parser.parse_args()
    with ThreadedServer(("127.0.0.1", args.port), ConnectHandler) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
