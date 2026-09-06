from __future__ import annotations

from email.message import EmailMessage

import pytest

from adaptive_learning.accounts import Accounts


class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host: str, port: int, **kwargs: object) -> None:
        self.host = host
        self.port = port
        self.kwargs = kwargs
        self.events: list[object] = []
        self.__class__.instances.append(self)

    def __enter__(self) -> "FakeSMTP":
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
