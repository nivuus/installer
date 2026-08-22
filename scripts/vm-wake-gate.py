#!/usr/bin/env python3
# Wake gate for the Windows VM (systemd socket activation, Accept=false).
# systemd hands us the LISTENING socket as fd 3. We accept the pending
# connection(s), peek at the first bytes, and only chain to
# handle-vm-start.sh when the client actually speaks like Moonlight.
#
# Why these ports behave differently:
#   - 47989 (plain HTTP): "GET /serverinfo..." is a Moonlight-specific request.
#     Proven discriminator: 42 scanner probes rejected, 0 false positive.
#   - 47984 (HTTPS): NEVER wakes. The former test ("first record byte is 0x16",
#     i.e. any TLS ClientHello) matched every mass scanner on the internet.
#     Over 30 days every single wake it triggered came from a scanner (Driftnet,
#     Linode, Akamai, Datacamp...) and not one came from a real client. TLS
#     carries no Moonlight signature readable without terminating the
#     handshake, and Moonlight pins the server certificate, so we cannot
#     terminate it here. Moonlight also probes 47989, which is the wake path.
#     Probes are still logged here for visibility.
#
# The host sockets are internet-reachable whenever the VM is off: the libvirt
# "stopped" hook removes the runtime forward-ports (47984/47989 -> 192.168.3.2),
# so WAN packets stop being DNAT'ed to the VM and land on these sockets.
import os
import socket
import sys
import syslog
import time

WAKE_SCRIPT = "/usr/local/sbin/handle-vm-start.sh"
ACCEPT_WINDOW_S = 5   # keep draining pending connections for this long
PEEK_TIMEOUT_S = 2    # how long a client may take to send its first bytes


def is_legit(data: bytes, port: str) -> bool:
    """True only when the payload is a genuine Moonlight wake request."""
    if not data:
        return False
    if port == "47989":
        return data.startswith(b"GET /serverinfo")
    # 47984 and anything else: out of the wake path, see header comment.
    return False


def preview(data: bytes) -> str:
    if not data:
        return "<empty>"
    text = data[:60]
    if all(32 <= b < 127 for b in text):
        return text.decode("ascii", "replace")
    return "hex:" + text[:20].hex()


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "?"
    syslog.openlog(f"vm-wake-gate-{port}")

    if os.environ.get("LISTEN_FDS") != "1":
        syslog.syslog(syslog.LOG_ERR, "no socket passed by systemd - aborting")
        return 1

    listener = socket.socket(fileno=3)
    listener.settimeout(1)
    deadline = time.time() + ACCEPT_WINDOW_S

    while time.time() < deadline:
        try:
            conn, addr = listener.accept()
        except socket.timeout:
            continue
        except OSError:
            break
        src = addr[0]
        try:
            conn.settimeout(PEEK_TIMEOUT_S)
            try:
                data = conn.recv(256)
            except (socket.timeout, OSError):
                data = b""
        finally:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()
        if is_legit(data, port):
            syslog.syslog(f"wake accepted from {src}: {preview(data)}")
            os.execv(WAKE_SCRIPT, [WAKE_SCRIPT])  # never returns
        syslog.syslog(syslog.LOG_WARNING, f"wake REJECTED from {src}: {preview(data)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
