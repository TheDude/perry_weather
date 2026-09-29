"""Minimal journald + sd_notify clients, standard library only.

Both protocols are simple datagrams on a Unix socket, so this avoids a
dependency on python3-systemd (a compiled extension not always available
on a small embedded image).

Journal native protocol:  https://systemd.io/JOURNAL_NATIVE_PROTOCOL/
sd_notify protocol:       man 3 sd_notify
"""

from __future__ import annotations

import os
import socket
import struct
import sys
from typing import Mapping, Optional

JOURNAL_SOCKET = "/run/systemd/journal/socket"


def _encode_field(key: str, value: object) -> bytes:
    k = key.encode("ascii")
    v = str(value).encode("utf-8")
    if b"\n" in v:
        # Binary-safe form: KEY\n<le64 length><value>\n
        return k + b"\n" + struct.pack("<Q", len(v)) + v + b"\n"
    return k + b"=" + v + b"\n"


class Journal:
    def __init__(self, identifier: str, path: str = JOURNAL_SOCKET):
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self._sock.setblocking(True)
        self._path = path
        self._identifier = identifier

    def send(self, message: str, priority: int, fields: Optional[Mapping[str, object]] = None) -> None:
        payload = [
            _encode_field("MESSAGE", message),
            _encode_field("PRIORITY", priority),
            _encode_field("SYSLOG_IDENTIFIER", self._identifier),
        ]
        for k, v in (fields or {}).items():
            payload.append(_encode_field(k, v))
        data = b"".join(payload)
        try:
            self._sock.sendto(data, self._path)
        except OSError:
            # Journald down or datagram too large: don't let logging kill the
            # reader. Fall back to stderr, which systemd also captures.
            print(message, file=sys.stderr, flush=True)

    def close(self) -> None:
        self._sock.close()


def notify(state: str) -> bool:
    """sd_notify(). No-op (returns False) when not run by systemd."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False
    if addr.startswith("@"):  # abstract namespace
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_CLOEXEC) as s:
            s.sendto(state.encode(), addr)
        return True
    except OSError:
        return False


def watchdog_interval() -> Optional[float]:
    """Seconds between WATCHDOG=1 pings (half of WatchdogSec), or None."""
    usec = os.environ.get("WATCHDOG_USEC")
    pid = os.environ.get("WATCHDOG_PID")
    if not usec or (pid and int(pid) != os.getpid()):
        return None
    try:
        return int(usec) / 1e6 / 2
    except ValueError:
        return None
