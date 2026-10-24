"""Optional TCP tunnel for verified implicit-TLS SMTP, without credential forwarding."""

from __future__ import annotations

import os
import smtplib
import socket
import ssl


def tunnel_endpoint(security: str) -> tuple[str, int] | None:
    """Fail closed for partial/invalid configuration; never fall back to direct SMTP."""
    host = os.getenv("SMTP_TUNNEL_HOST", "").strip()
    port = os.getenv("SMTP_TUNNEL_PORT", "").strip()
    if not host and not port:
        return None
    if security != "ssl" or not host or not port:
        raise ValueError("SMTP_TUNNEL requires ssl security and both host and port")
    if any(c.isspace() for c in host) or any(c in host for c in "/\\@"):
        raise ValueError("SMTP_TUNNEL_HOST must be a TCP host, not a URL")
    try:
        number = int(port)
    except ValueError:
        raise ValueError("SMTP_TUNNEL_PORT must be an integer TCP port") from None
    if not 1 <= number <= 65535:
        raise ValueError("SMTP_TUNNEL_PORT must be in 1..65535")
    return host, number


class TunnelSMTP_SSL(smtplib.SMTP_SSL):
    """Change only the TCP endpoint; SMTP_HOST remains the TLS identity.

    SMTP_SSL stores the supplied SSLContext in ``self.context``. Keep its normal
    SMTP connection/greeting/authentication/QUIT handling and override only the
    socket factory, including cleanup when certificate verification fails.
    """

    def __init__(self, host: str, port: int, *, tunnel_host: str, tunnel_port: int,
                 timeout: float, context: ssl.SSLContext) -> None:
        if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
            raise ValueError("SMTP_TUNNEL requires verified TLS and hostname checking")
        self._tunnel_address = (tunnel_host, tunnel_port)
        super().__init__(host, port, timeout=timeout, context=context)

    def _get_socket(self, host: str, port: int, timeout: float | None) -> ssl.SSLSocket:
        if timeout == 0:
            raise ValueError("Non-blocking SMTP_TUNNEL sockets are not supported")
        raw = socket.create_connection(self._tunnel_address, timeout, self.source_address)
        try:
            # Never use the loopback tunnel address as SNI/certificate hostname.
            return self.context.wrap_socket(raw, server_hostname=self._host)
        except BaseException:  # noqa: BLE001 - release raw transport, then preserve the original failure
            raw.close()
            raise
