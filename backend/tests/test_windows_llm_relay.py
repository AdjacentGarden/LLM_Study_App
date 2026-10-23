"""The model relay may download the verified video asset without opening an egress proxy."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "deployment/server/windows_llm_relay.py"
SPEC = importlib.util.spec_from_file_location("tested_windows_llm_relay", SOURCE)
assert SPEC and SPEC.loader
relay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(relay)
CDN = "public-cdn-video-data-algeng.oss-cn-wulanchabu.aliyuncs.com"


def test_official_asset_and_existing_providers_remain_exactly_allowlisted():
    assert relay.ALLOWED_HOSTS == {
        "pucoding.com", "api.deepseek.com", "api.minimaxi.com", "api.minimax.io", CDN,
    }
    for host in relay.ALLOWED_HOSTS:
        assert relay.parse_connect(f"CONNECT {host}:443 HTTP/1.1\r\n".encode()) == host


@pytest.mark.parametrize("target", [
    "127.0.0.1:443", "10.249.186.201:443", "[::1]:443",
    "untrusted.oss-cn-wulanchabu.aliyuncs.com:443",
    f"{CDN}.untrusted.example:443", f"untrusted@{CDN}:443",
    f"{CDN}:80", f"{CDN}:444", f"{CDN}.:443",
])
def test_asset_allowlist_rejects_private_targets_other_buckets_and_host_confusion(target):
    with pytest.raises(relay.RequestError) as caught:
        relay.parse_connect(f"CONNECT {target} HTTP/1.1\r\n".encode())
    assert caught.value.status == 403
