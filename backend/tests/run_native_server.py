"""Explicit, loopback-only fixture adapter server for XCTest. Never production."""

import os
import tempfile
from pathlib import Path

import uvicorn
from native_harness import configure, textbook_pdf
from pytest import MonkeyPatch

if os.getenv("NATIVE_E2E_TEST_SERVER") != "1":
    raise SystemExit("Set NATIVE_E2E_TEST_SERVER=1 to run the isolated test adapter server")

root = Path(tempfile.mkdtemp(prefix="cloudpath-native-e2e-"))
patch = MonkeyPatch()
app = configure(root, patch)
if os.getenv("NATIVE_E2E_LOCAL_LOGIN") == "1":
    patch.setenv("NATIVE_LOCAL_LOGIN_ENABLED", "1")


@app.get("/api/native-test/health")
def health():
    return {
        "ok": True, "fixture_adapters": True,
        "local_default": os.getenv("NATIVE_E2E_LOCAL_LOGIN") == "1",
    }


@app.get("/api/native-test/pdf")
def pdf():
    from fastapi import Response

    return Response(textbook_pdf(), media_type="application/pdf")


uvicorn.run(app, host="127.0.0.1", port=18101, log_level="warning")
