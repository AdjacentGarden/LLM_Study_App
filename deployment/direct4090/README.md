# Main backend: direct 4090

Authoritative backend: `connect.bjb1.seetacloud.com:36243`, deployment root
`/root/autodl-tmp/adaptive-book-ocr`. The API listens on loopback port 18126.
The existing resident OCR uses 18201, with local BGE embedding/reranking and
faster-whisper-small model directories. Reuse these environments and models.

The Mac simulator uses `http://127.0.0.1:18100`:

```text
App -> Mac local port 18100 -> direct SSH to the 4090 -> API port 18126
```

Run `mac-direct4090.sh` through the persistent Mac LaunchAgent. Its dedicated
SSH key allows only the API forward and does not allow an interactive shell.
Keep the key and verified host records under `~/.ssh`, outside the repository.
The old Windows and Germany VPS business relays are retired for this deployment.
Physical devices need an HTTP API entry reachable from that device; the Mac
loopback entry is the delivered simulator configuration.

For automated acceptance, enable only the dedicated testing identity in the
server runtime configuration:

```text
CLOUDPATH_TEST_AUTH_ENABLED=1
CLOUDPATH_TEST_AUTH_EMAIL=<dedicated tester email>
CLOUDPATH_TEST_AUTH_CODE=<six digits>
```

The fixed code is restricted to that email. Other accounts retain SMTP and
normal verification. Challenges still expire, limit incorrect attempts and
produce revocable, account-scoped sessions. Test credentials belong in private
runtime configuration and acceptance receipts.

Work and publish only on main. A deployment is accepted only after real
simulator upload, OCR, generation, browsing, AI conversation and note flows
pass. Mock authentication does not permit mocking model results. Preserve
existing data and resident services, record source revisions and request IDs,
and query uncertain operations before creating another paid request.
