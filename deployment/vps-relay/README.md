# Mac simulator through the Germany VPS

The deployed simulator uses this service path:

```text
App -> Mac 127.0.0.1:18100 -> Germany VPS 127.0.0.1:18100
    -> Windows gateway 127.0.0.1:8100 -> existing GPU backend tunnel
```

`CloudAPI` defaults simulator builds to `http://127.0.0.1:18100` and persists
that address. Saved direct Windows addresses are migrated at normal startup.
Explicit test launch addresses and separately configured services remain supported.
The App does not need a test runner or a launch environment to use the relay.

Windows runs `windows-vps-relay.ps1` as the existing startup task
`CloudPath-VpsRelay-18100`, with its dedicated key and verified `known_hosts`
in `C:\CloudPath\runtime\vps-relay`. It opens only a VPS loopback listener.
The dedicated `cprelay` account permits the Windows key to listen on
`127.0.0.1:18100`, and the Mac key to connect to that same port.

On the Mac, `mac-vps-relay.sh` is the existing KeepAlive LaunchAgent
`com.cloudpath.vps-relay`. Its key and host records are
`~/.ssh/cprelay_ed25519` and `~/.ssh/cprelay_known_hosts`.
The scripts retain strict SSH host checks and reconnect after a broken connection.
Credentials and host records stay in the deployment runtime.

Verify `/api/health` from both the VPS and Mac at `http://127.0.0.1:18100`,
then launch and relaunch the simulator App normally. Its saved service address
must remain `http://127.0.0.1:18100`. The physical iPhone build needs a service
address reachable from that phone; the Mac loopback port is specific to this
simulator deployment.
