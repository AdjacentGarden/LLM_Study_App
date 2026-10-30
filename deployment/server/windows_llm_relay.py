"""Windows outbound HTTPS relay for the pinned study server.

Initialize once with a JSON object on stdin: {"username": ..., "password": ...}.
Credentials use Windows DPAPI LocalMachine and an Admin/SYSTEM-only file ACL.
The serving process exposes only server-loopback TCP 18082, accepts CONNECT
to the named model providers on port 443, and reconnects after SSH disruption.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import json
import logging
import os
import select
import signal
import socket
import sys
import threading
import time
import uuid
from ctypes import wintypes
from logging.handlers import RotatingFileHandler
from pathlib import Path

SERVER = "10.249.186.201"
REMOTE_BIND = "127.0.0.1"
REMOTE_PORT = 18082
HOST_KEY = "AAAAC3NzaC1lZDI1NTE5AAAAIIApynbwM96ii/gPkdeHfXt3ZiTbOGCxOOcTkdf9GX6u"
ALLOWED_HOSTS = frozenset({
    "pucoding.com", "api.deepseek.com", "api.minimaxi.com", "api.minimax.io",
    # Exact public asset hosts returned by MiniMax V1 file metadata and V2
    # task results; do not allow arbitrary OSS buckets or domains.
    "public-cdn-video-data-algeng.oss-cn-wulanchabu.aliyuncs.com",
    "algeng-video-infer.oss-cn-shanghai.aliyuncs.com",
})
ENTROPY = b"CloudPath LLM relay credentials v1"
STOP = threading.Event()
LOG = logging.getLogger("cloudpath.llm-relay")


class DataBlob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def blob(data: bytes):
    backing = ctypes.create_string_buffer(data)
    return DataBlob(len(data), ctypes.cast(backing, ctypes.POINTER(ctypes.c_ubyte))), backing


def dpapi(data: bytes, *, decrypt: bool) -> bytes:
    if os.name != "nt":
        raise RuntimeError("DPAPI credential operations require Windows")
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    source, _source_backing = blob(data)
    entropy, _entropy_backing = blob(ENTROPY)
    result = DataBlob()
    if decrypt:
        operation = crypt.CryptUnprotectData
        operation.argtypes = [ctypes.POINTER(DataBlob), ctypes.c_void_p,
                              ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.c_void_p,
                              wintypes.DWORD, ctypes.POINTER(DataBlob)]
        ok = operation(ctypes.byref(source), None, ctypes.byref(entropy), None, None,
                       1, ctypes.byref(result))
    else:
        operation = crypt.CryptProtectData
        operation.argtypes = [ctypes.POINTER(DataBlob), wintypes.LPCWSTR,
                              ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.c_void_p,
                              wintypes.DWORD, ctypes.POINTER(DataBlob)]
        ok = operation(ctypes.byref(source), "CloudPath LLM relay", ctypes.byref(entropy),
                       None, None, 1 | 4, ctypes.byref(result))
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(ctypes.cast(result.data, ctypes.c_void_p))


def restrict_acl(path: Path) -> None:
    """Protected DACL: only SYSTEM and the local Administrators group."""
    if os.name != "nt":
        raise RuntimeError("Credential ACL operations require Windows")
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    descriptor = ctypes.c_void_p()
    convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    if not convert("D:P(A;;FA;;;SY)(A;;FA;;;BA)", 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        present, defaulted, dacl = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
        get_dacl = advapi.GetSecurityDescriptorDacl
        get_dacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL),
                            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
        if not get_dacl(descriptor, ctypes.byref(present), ctypes.byref(dacl),
                        ctypes.byref(defaulted)):
            raise ctypes.WinError(ctypes.get_last_error())
        set_security = advapi.SetNamedSecurityInfoW
        set_security.argtypes = [wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
                                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                ctypes.c_void_p]
        set_security.restype = wintypes.DWORD
        error = set_security(str(path), 1, 4 | 0x80000000, None, None, dacl, None)
        if error:
            raise ctypes.WinError(error)
    finally:
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(descriptor)


def validate_credentials(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise TypeError("Expected a credential object")
    if any(not isinstance(value.get(field), str) or not value[field]
           for field in ("username", "password")):
        raise ValueError("Username and password are required")
    return {field: value[field] for field in ("username", "password")}


def initialize(path: Path) -> None:
    raw = sys.stdin.buffer.read(16385)
    if len(raw) > 16384:
        raise ValueError("Credential input exceeds its size limit")
    credentials = validate_credentials(json.loads(raw.decode("utf-8-sig")))
    encrypted = dpapi(json.dumps(credentials).encode("utf-8"), decrypt=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            restrict_acl(temporary)
            stream.write(encrypted)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        restrict_acl(path)
    finally:
        temporary.unlink(missing_ok=True)
    print("Encrypted relay credentials initialized; file access is Admin/SYSTEM only.", flush=True)


def load_credentials(path: Path) -> dict[str, str]:
    restrict_acl(path)
    encrypted = path.read_bytes()
    if len(encrypted) > 65536:
        raise ValueError("Credential file exceeds its size limit")
    return validate_credentials(json.loads(dpapi(encrypted, decrypt=True)))


class RequestError(Exception):
    def __init__(self, status: int):
        self.status = status


def parse_connect(header: bytes) -> str:
    try:
        line = header.split(b"\r\n", 1)[0].decode("ascii")
        method, target, version = line.split()
    except (ValueError, UnicodeError) as error:
        raise RequestError(400) from error
    if method != "CONNECT":
        raise RequestError(405)
    if version not in {"HTTP/1.0", "HTTP/1.1"}:
        raise RequestError(400)
    parts = target.lower().split(":")
    if len(parts) != 2 or parts[1] != "443" or parts[0] not in ALLOWED_HOSTS:
        raise RequestError(403)
    return parts[0]


def send_error(channel, status: int) -> None:
    names = {400: "Bad Request", 403: "Forbidden", 405: "Method Not Allowed",
             502: "Bad Gateway", 503: "Service Unavailable"}
    try:
        channel.sendall(f"HTTP/1.1 {status} {names[status]}\r\nContent-Length: 0\r\n"
                        "Connection: close\r\n\r\n".encode("ascii"))
    except Exception as error:  # noqa: BLE001 - peer may have already disconnected.
        LOG.debug("Proxy error response could not be delivered (%s)", type(error).__name__)


def relay(channel, session_stop: threading.Event, slots: threading.BoundedSemaphore) -> None:
    upstream = None
    connected = False
    try:
        channel.settimeout(15)
        data = b""
        while b"\r\n\r\n" not in data:
            fragment = channel.recv(4096)
            if not fragment:
                return
            data += fragment
            if len(data) > 16384:
                raise RequestError(400)
        header, pending = data.split(b"\r\n\r\n", 1)
        host = parse_connect(header)
        upstream = socket.create_connection((host, 443), timeout=15)
        upstream.settimeout(30)
        channel.settimeout(30)
        channel.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        connected = True
        if pending:
            upstream.sendall(pending)
        last_activity = time.monotonic()
        while not STOP.is_set() and not session_stop.is_set():
            readable, _, _ = select.select([channel, upstream], [], [], 1)
            if not readable and time.monotonic() - last_activity > 300:
                break
            for source in readable:
                block = source.recv(65536)
                if not block:
                    return
                (upstream if source is channel else channel).sendall(block)
                last_activity = time.monotonic()
    except RequestError as error:
        send_error(channel, error.status)
    except Exception as error:  # noqa: BLE001 - isolate an untrusted proxy connection.
        if not connected:
            send_error(channel, 502)
        LOG.debug("CONNECT channel ended: %s", type(error).__name__)
    finally:
        if upstream is not None:
            upstream.close()
        channel.close()
        slots.release()


def bounded_request(transport, operation, timeout: float = 15):
    finished = threading.Event()
    result = []

    def request():
        try:
            result.append((True, operation()))
        except Exception as error:  # noqa: BLE001 - transport exceptions are returned to its owner.
            result.append((False, error))
        finally:
            finished.set()

    threading.Thread(target=request, daemon=True).start()
    if not finished.wait(timeout):
        transport.close()
        raise TimeoutError("SSH control request timed out")
    success, value = result[0]
    if not success:
        raise value
    return value


def serve(credentials: dict[str, str]) -> None:
    import paramiko

    pinned = paramiko.Ed25519Key(data=base64.b64decode(HOST_KEY))
    backoff = 2
    slots = threading.BoundedSemaphore(16)
    while not STOP.is_set():
        client = paramiko.SSHClient()
        client.get_host_keys().add(SERVER, "ssh-ed25519", pinned)
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        session_stop = threading.Event()
        started = time.monotonic()
        try:
            client.connect(SERVER, port=22, username=credentials["username"],
                           password=credentials["password"], timeout=15, banner_timeout=15,
                           auth_timeout=20, look_for_keys=False, allow_agent=False)
            transport = client.get_transport()
            if transport is None or not transport.is_active():
                raise ConnectionError("SSH transport unavailable")
            port = bounded_request(transport,
                                   lambda current=transport: current.request_port_forward(
                                       REMOTE_BIND, REMOTE_PORT))
            if port != REMOTE_PORT:
                raise ConnectionError("Unexpected SSH bind port")
            LOG.info("Relay active on server loopback port %s; pinned SSH host verified", port)
            heartbeat_at = time.monotonic() + 20
            while not STOP.is_set() and transport.is_active():
                channel = transport.accept(timeout=1)
                if channel is not None:
                    if slots.acquire(blocking=False):
                        threading.Thread(target=relay, args=(channel, session_stop, slots),
                                         daemon=True).start()
                    else:
                        channel.settimeout(1)
                        send_error(channel, 503)
                        channel.close()
                if time.monotonic() >= heartbeat_at:
                    bounded_request(transport,
                                    lambda current=transport: current.global_request(
                                        "keepalive@openssh.com", wait=True), timeout=10)
                    heartbeat_at = time.monotonic() + 20
            if not STOP.is_set():
                raise ConnectionError("SSH connection ended")
        except Exception as error:  # noqa: BLE001 - retry all failures without exposing credentials.
            # Do not log exception text, credentials, headers, request URLs, or TLS data.
            LOG.warning("SSH relay reconnect required (%s)", type(error).__name__)
        finally:
            session_stop.set()
            client.close()
        if time.monotonic() - started > 60:
            backoff = 2
        if not STOP.is_set():
            STOP.wait(backoff)
            backoff = min(backoff * 2, 60)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--initialize", action="store_true")
    action.add_argument("--serve", action="store_true")
    parser.add_argument("--credential-file", type=Path,
                        default=Path("C:/CloudPath/config/llm-relay.dpapi"))
    parser.add_argument("--log-file", type=Path,
                        default=Path("C:/CloudPath/runtime/llm-relay/relay.log"))
    args = parser.parse_args()
    if args.initialize:
        initialize(args.credential_file)
        return 0
    args.log_file.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(args.log_file, maxBytes=1024 * 1024, backupCount=2,
                                  encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)
    # Paramiko debug logs are intentionally disabled: operational logs never need
    # authentication details, SSH packets, HTTP headers, or tunneled TLS bytes.
    logging.getLogger("paramiko").setLevel(logging.CRITICAL)
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), lambda *_: STOP.set())
    serve(load_credentials(args.credential_file))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:  # noqa: BLE001 - prevent sensitive exception messages/tracebacks.
        # Generic diagnostics deliberately exclude possibly sensitive exception text.
        print(f"Relay failed: {type(error).__name__}; Windows error "
              f"{getattr(error, 'winerror', None)}", file=sys.stderr, flush=True)
        raise SystemExit(1)
