"""Main loop: open the tty, decode frames, log them, survive disconnects."""

from __future__ import annotations

import argparse
import errno
import os
import select
import signal
import socket
import time
from typing import List, Optional

from .frame_parser import FrameErrorEvent, FrameParser, Observation
from .serial_port import open_serial
from .sinks import LOG_ERR, LOG_INFO, LOG_NOTICE, LOG_WARNING, Sink

POLL_MS = 50
BACKOFF_MIN_S = 0.5
BACKOFF_MAX_S = 30.0


class Service:
    def __init__(self, device: str, baud: int, sink: Sink,
                 stale_timeout_s: float, frame_gap_s: float):
        self.device = device
        self.baud = baud
        self.sink = sink
        self.stale_timeout_s = stale_timeout_s
        self.frame_gap_s = frame_gap_s
        self.parser = FrameParser(self._on_observation, self._on_frame_error)

        self._fd: Optional[int] = None
        self._running = True
        self._stale = False
        self._last_good = self._last_rx = time.monotonic()

        # Signals: handlers only record the signal number; the wakeup fd makes
        # poll() return immediately so they're handled in the main loop.
        self._pending_signals: List[int] = []
        self._sig_r, sig_w = socket.socketpair()
        self._sig_r.setblocking(False)
        sig_w.setblocking(False)
        self._sig_w = sig_w  # keep alive
        signal.set_wakeup_fd(sig_w.fileno(), warn_on_full_buffer=False)
        for s in (signal.SIGTERM, signal.SIGINT, signal.SIGUSR1):
            signal.signal(s, lambda signo, _frame: self._pending_signals.append(signo))

    # ---- callbacks -------------------------------------------------------

    def _on_observation(self, obs: Observation) -> None:
        self._last_good = time.monotonic()
        if self._stale:
            self._stale = False
            self.sink.message(LOG_NOTICE, "Sensor data resumed")
        self.sink.observation(obs)

    def _on_frame_error(self, ev: FrameErrorEvent) -> None:
        self.sink.frame_error(ev)

    # ---- main loop -------------------------------------------------------

    def run(self) -> int:
        backoff = BACKOFF_MIN_S
        reported_open_failure = False

        while self._running:
            try:
                self._fd = open_serial(self.device, self.baud)
            except (OSError, ValueError) as e:
                # Log the first failure of an outage loudly, then retry quietly
                # so an unplugged sensor doesn't flood the journal.
                if not reported_open_failure:
                    self.sink.message(LOG_ERR, f"Cannot open {self.device}: {e} (retrying)")
                    reported_open_failure = True
                self._sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX_S)
                continue

            reported_open_failure = False
            backoff = BACKOFF_MIN_S
            self.sink.message(LOG_NOTICE, f"Opened {self.device} at {self.baud} 8N1")
            try:
                self._read_loop()
            finally:
                os.close(self._fd)
                self._fd = None
                self.parser.reset()

        self.sink.message(LOG_NOTICE, f"Shutting down; stats: {self.parser.stats}")
        return 0

    def _read_loop(self) -> None:
        """Returns when the port must be reopened or shutdown was requested."""
        assert self._fd is not None
        self._last_good = self._last_rx = time.monotonic()
        self._stale = False

        poller = select.poll()
        poller.register(self._fd, select.POLLIN)
        poller.register(self._sig_r.fileno(), select.POLLIN)

        while self._running:
            events = dict(poller.poll(POLL_MS))
            now = time.monotonic()
            self._handle_signals()
            if not self._running:
                return

            ev = events.get(self._fd, 0)
            if ev & select.POLLIN:
                if not self._drain():
                    return
                self._last_rx = now
            elif ev & (select.POLLHUP | select.POLLERR | select.POLLNVAL):
                self.sink.message(LOG_ERR, f"Lost {self.device} (hangup/error); reopening")
                return

            # Bytes stopped arriving mid-frame: the rest isn't coming.
            if self.parser.has_pending and now - self._last_rx > self.frame_gap_s:
                self.parser.reset()
                self.sink.message(LOG_WARNING, "Dropped partial frame after inter-frame gap")

            if not self._stale and now - self._last_good > self.stale_timeout_s:
                self._stale = True
                self.sink.message(LOG_WARNING,
                                  f"No valid observation for {self.stale_timeout_s:g} s")

    def _drain(self) -> bool:
        """Read everything available. Returns False if the port went away."""
        while True:
            try:
                data = os.read(self._fd, 256)
            except BlockingIOError:
                return True
            except InterruptedError:
                continue
            except OSError as e:
                # EIO is what a tty returns after the other side hangs up.
                self.sink.message(LOG_ERR, f"Read from {self.device} failed: "
                                           f"{errno.errorcode.get(e.errno, e.errno)}; reopening")
                return False
            if not data:
                self.sink.message(LOG_ERR, f"Read from {self.device} failed: EOF; reopening")
                return False
            self.parser.feed(data)

    def _handle_signals(self) -> None:
        try:
            while self._sig_r.recv(64):
                pass
        except BlockingIOError:
            pass
        while self._pending_signals:
            signo = self._pending_signals.pop(0)
            if signo == signal.SIGUSR1:
                self.sink.message(LOG_INFO, f"stats: {self.parser.stats}")
            else:
                self._running = False

    def _sleep(self, seconds: float) -> None:
        """Sleep, waking early for signals."""
        deadline = time.monotonic() + seconds
        poller = select.poll()
        poller.register(self._sig_r.fileno(), select.POLLIN)
        while self._running:
            now = time.monotonic()
            if now >= deadline:
                return
            poller.poll(int((deadline - now) * 1000) + 1)
            self._handle_signals()


def _positive_ms(s: str) -> float:
    v = int(s)
    if v <= 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return v / 1000.0


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="weather-sensor",
        description="Read weather observations from a serial sensor and log them to stdout (the journal, under systemd).")
    p.add_argument("device", nargs="?", default=os.environ.get("WEATHER_TTY"),
                   help="serial tty (default: $WEATHER_TTY)")
    p.add_argument("-b", "--baud", type=int, default=9600, help="baud rate (default 9600)")
    p.add_argument("-s", "--stale-timeout", type=_positive_ms, default=10.0, metavar="MS",
                   help="warn if no valid frame for MS (default 10000)")
    p.add_argument("-g", "--frame-gap", type=_positive_ms, default=0.1, metavar="MS",
                   help="drop a partial frame after MS of silence (default 100)")
    args = p.parse_args(argv)
    if not args.device:
        p.error("no device given (argument or $WEATHER_TTY)")

    return Service(args.device, args.baud, Sink(), args.stale_timeout, args.frame_gap).run()
