# Model API route through Windows

The GPU host's text-model configuration uses `http://127.0.0.1:18082` as its
HTTPS proxy. `windows_llm_relay.py` provides that route through the existing
Windows machine, independently of an operator laptop or logged-in desktop.

The `CloudPathModelRelay` Windows scheduled task runs at startup and reconnects
to the pinned GPU host. It exposes only GPU-host loopback port 18082 and accepts
CONNECT to the four provider hostnames and the exact MiniMax video asset CDN
hostname in the script on TCP 443. The asset hostname was verified from the
official file metadata response; other OSS buckets and arbitrary hosts remain
blocked. TLS remains
between the GPU application's client and the provider. Request bodies, headers,
and credentials are not logged.

The deployed runtime is `C:/CloudPath/runtime/llm-relay`. Paramiko dependencies
are isolated in its `lib` subdirectory. The task starts `start-llm-relay.ps1` and
runs without an interactive desktop session.

Initialize credentials by sending a JSON object containing `username` and
`password` on stdin to `windows_llm_relay.py --initialize --credential-file ...`.
Do not place credentials in command arguments or source code. The script stores
them using machine DPAPI and a protected SYSTEM/Administrators file ACL. The
runtime script and dependency directories must also be restricted to these
principals; ordinary users must not be able to replace task code.

Use Task Scheduler or `Get-ScheduledTask -TaskName CloudPathModelRelay` to inspect
the task and `relay.log` for connection status. Verify the GPU host's actual
listener remains `127.0.0.1:18082`, and verify a real model request after route
changes. Windows must remain running with access to both the GPU host and the
provider. A disconnect can fail an in-flight request; application retry and
validation rules remain in force.

The OCR API itself stays on the GPU host at `127.0.0.1:18200` and does not send
PDFs through this relay. Text-model summary and question-generation requests use
the relay under the application's existing provider selection.
