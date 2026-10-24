from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

import pytest

from adaptive_learning.accounts import Accounts
from adaptive_learning.smtp_transport import TunnelSMTP_SSL


class FakeSMTP:
    instances: list[FakeSMTP] = []

    def __init__(self, host: str, port: int, **kwargs: object) -> None:
        self.host = host
        self.port = port
        self.kwargs = kwargs
        self.events: list[object] = []
        self.__class__.instances.append(self)

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def ehlo(self) -> None:
        self.events.append("ehlo")

    def starttls(self, **kwargs: object) -> None:
        self.events.append(("starttls", kwargs))

    def login(self, user: str, password: str) -> None:
        self.events.append(("login", user, password))

    def send_message(self, message: EmailMessage) -> None:
        self.events.append(("send", message))


@pytest.fixture(autouse=True)
def smtp_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSMTP.instances.clear()
    for name in (
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_SECURITY",
        "SMTP_FROM",
        "SMTP_USER",
        "SMTP_PASSWORD",
        "SMTP_TUNNEL_HOST",
        "SMTP_TUNNEL_PORT",
    ):
        monkeypatch.delenv(name, raising=False)


def test_smtp_ready_allows_loopback_test_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SMTP_FROM", "cloudpath@test.invalid")
    assert Accounts.smtp_ready()


def test_smtp_ready_rejects_half_configured_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_FROM", "cloudpath@example.com")
    monkeypatch.setenv("SMTP_USER", "cloudpath@example.com")
    assert not Accounts.smtp_ready()


def test_plain_smtp_sends_without_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SMTP_PORT", "1025")
    monkeypatch.setenv("SMTP_SECURITY", "plain")
    monkeypatch.setenv("SMTP_FROM", "cloudpath@test.invalid")
    monkeypatch.setattr("adaptive_learning.accounts.smtplib.SMTP", FakeSMTP)

    Accounts._email("student@example.com", "123456")

    smtp = FakeSMTP.instances[0]
    assert (smtp.host, smtp.port) == ("127.0.0.1", 1025)
    assert not any(isinstance(event, tuple) and event[0] == "login" for event in smtp.events)
    sent = next(event[1] for event in smtp.events if isinstance(event, tuple) and event[0] == "send")
    assert sent["To"] == "student@example.com"
    assert "123456" in sent.get_content()


def test_starttls_smtp_authenticates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_SECURITY", "starttls")
    monkeypatch.setenv("SMTP_FROM", "cloudpath@example.com")
    monkeypatch.setenv("SMTP_USER", "cloudpath@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "app-password")
    monkeypatch.setattr("adaptive_learning.accounts.smtplib.SMTP", FakeSMTP)

    Accounts._email("student@example.com", "654321")

    events = FakeSMTP.instances[0].events
    assert events[0] == "ehlo"
    assert events[1][0] == "starttls"
    assert events[2] == "ehlo"
    assert events[3] == ("login", "cloudpath@example.com", "app-password")


def test_invalid_smtp_security_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_SECURITY", "invalid")
    monkeypatch.setenv("SMTP_FROM", "cloudpath@example.com")
    with pytest.raises(ValueError, match="SMTP_SECURITY"):
        Accounts._email("student@example.com", "123456")


def test_direct_ssl_smtp_remains_verified_and_authenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_FROM", "cloudpath@example.com")
    monkeypatch.setenv("SMTP_USER", "cloudpath@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "unit-test-password")
    monkeypatch.setattr("adaptive_learning.accounts.smtplib.SMTP_SSL", FakeSMTP)

    Accounts._email("student@example.com", "123456")

    smtp = FakeSMTP.instances[0]
    assert (smtp.host, smtp.port) == ("smtp.example.com", 465)
    context = smtp.kwargs["context"]
    assert isinstance(context, ssl.SSLContext)
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    assert smtp.events[0] == ("login", "cloudpath@example.com", "unit-test-password")
    assert smtp.events[-1][0] == "send"
    assert "tunnel_host" not in smtp.kwargs


def test_tunnel_changes_only_tcp_destination_and_keeps_mail_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_FROM", "cloudpath@example.com")
    monkeypatch.setenv("SMTP_USER", "cloudpath@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "unit-test-password")
    monkeypatch.setenv("SMTP_TUNNEL_HOST", "127.0.0.1")
    monkeypatch.setenv("SMTP_TUNNEL_PORT", "18465")
    monkeypatch.setattr("adaptive_learning.accounts.TunnelSMTP_SSL", FakeSMTP)
    assert Accounts.smtp_ready()

    Accounts._email("student@example.com", "123456")

    smtp = FakeSMTP.instances[0]
    assert (smtp.host, smtp.port) == ("smtp.example.com", 465)
    assert (smtp.kwargs["tunnel_host"], smtp.kwargs["tunnel_port"]) == ("127.0.0.1", 18465)
    context = smtp.kwargs["context"]
    assert isinstance(context, ssl.SSLContext)
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    assert smtp.events[0] == ("login", "cloudpath@example.com", "unit-test-password")
    assert smtp.events[-1][0] == "send"


@pytest.mark.parametrize("host,port,security", [
    ("127.0.0.1", "", "ssl"),
    ("", "18465", "ssl"),
    ("127.0.0.1", "not-a-port", "ssl"),
    ("127.0.0.1", "0", "ssl"),
    ("127.0.0.1", "65536", "ssl"),
    ("127.0.0.1", "18465", "starttls"),
    ("127.0.0.1", "18465", "plain"),
    ("http://127.0.0.1", "18465", "ssl"),
    ("invalid host", "18465", "ssl"),
])
def test_tunnel_misconfiguration_never_falls_back_to_direct_smtp(
    monkeypatch: pytest.MonkeyPatch, host: str, port: str, security: str
) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_FROM", "cloudpath@example.com")
    monkeypatch.setenv("SMTP_SECURITY", security)
    monkeypatch.setenv("SMTP_TUNNEL_HOST", host)
    monkeypatch.setenv("SMTP_TUNNEL_PORT", port)
    monkeypatch.setattr("adaptive_learning.accounts.smtplib.SMTP_SSL", FakeSMTP)
    monkeypatch.setattr("adaptive_learning.accounts.smtplib.SMTP", FakeSMTP)
    monkeypatch.setattr("adaptive_learning.accounts.TunnelSMTP_SSL", FakeSMTP)
    assert not Accounts.smtp_ready()
    with pytest.raises(ValueError, match="SMTP_TUNNEL"):
        Accounts._email("student@example.com", "123456")
    assert not FakeSMTP.instances


class TransportSocket:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_smtp_ssl_constructor_keeps_original_tls_hostname_over_tunnel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = ssl.create_default_context()
    raw, wrapped = TransportSocket(), TransportSocket()
    connections = []
    handshakes = []

    def connect(address, timeout, source_address):
        connections.append((address, timeout, source_address))
        return raw

    def wrap(supplied_context, raw_socket, *, server_hostname):
        handshakes.append((supplied_context, raw_socket, server_hostname))
        return wrapped

    monkeypatch.setattr("adaptive_learning.smtp_transport.socket.create_connection", connect)
    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", wrap)
    monkeypatch.setattr(smtplib.SMTP, "getreply", lambda self: (220, b"unit-test SMTP ready"))

    client = TunnelSMTP_SSL("smtp.example.com", 465, tunnel_host="127.0.0.1",
                            tunnel_port=18465, timeout=12, context=context)

    assert connections == [(("127.0.0.1", 18465), 12, None)]
    assert handshakes == [(context, raw, "smtp.example.com")]
    assert client.context is context and client.sock is wrapped
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    assert not raw.closed


def test_tunnel_certificate_failure_closes_raw_socket_and_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = TransportSocket()
    context = ssl.create_default_context()
    rejection = ssl.SSLCertVerificationError("unit-test certificate hostname mismatch")

    def reject(*args, **kwargs):
        raise rejection

    monkeypatch.setattr("adaptive_learning.smtp_transport.socket.create_connection", lambda *args: raw)
    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", reject)
    with pytest.raises(ssl.SSLCertVerificationError) as caught:
        TunnelSMTP_SSL("smtp.example.com", 465, tunnel_host="127.0.0.1",
                       tunnel_port=18465, timeout=12, context=context)
    assert caught.value is rejection and raw.closed


@pytest.mark.parametrize("verify_mode", [ssl.CERT_REQUIRED, ssl.CERT_NONE])
def test_tunnel_refuses_disabled_hostname_or_certificate_validation(
    monkeypatch: pytest.MonkeyPatch, verify_mode: ssl.VerifyMode,
) -> None:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = verify_mode
    connections = []
    monkeypatch.setattr("adaptive_learning.smtp_transport.socket.create_connection",
                        lambda *args: connections.append(args))
    with pytest.raises(ValueError, match="verified TLS"):
        TunnelSMTP_SSL("smtp.example.com", 465, tunnel_host="127.0.0.1",
                       tunnel_port=18465, timeout=12, context=context)
    assert connections == []
