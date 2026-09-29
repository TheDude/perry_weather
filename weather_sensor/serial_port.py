"""Raw, non-blocking 8N1 serial tty setup via termios."""

from __future__ import annotations

import fcntl
import os
import termios

_BAUD = {
    1200: termios.B1200, 2400: termios.B2400, 4800: termios.B4800,
    9600: termios.B9600, 19200: termios.B19200, 38400: termios.B38400,
    57600: termios.B57600, 115200: termios.B115200,
}

# termios attribute list indices
_IFLAG, _OFLAG, _CFLAG, _LFLAG, _ISPEED, _OSPEED, _CC = range(7)


def open_serial(path: str, baud: int) -> int:
    """Open and configure `path`. Returns a non-blocking fd; raises OSError
    or ValueError on failure."""
    speed = _BAUD.get(baud)
    if speed is None:
        raise ValueError(f"unsupported baud rate {baud}")

    # O_NOCTTY: never become our controlling terminal.
    # O_NONBLOCK: don't hang in open() waiting for DCD; reads are poll()-driven.
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        # Exclusive access: a second reader would silently steal bytes and
        # look like line noise.
        fcntl.ioctl(fd, termios.TIOCEXCL)

        attrs = termios.tcgetattr(fd)  # raises termios.error if not a tty

        # Raw mode (equivalent of cfmakeraw): no line discipline, echo, or
        # CR/LF / XON/XOFF translation, which would mangle binary frames.
        attrs[_IFLAG] &= ~(termios.IGNBRK | termios.BRKINT | termios.PARMRK |
                           termios.ISTRIP | termios.INLCR | termios.IGNCR |
                           termios.ICRNL | termios.IXON | termios.IXOFF)
        attrs[_OFLAG] &= ~termios.OPOST
        attrs[_LFLAG] &= ~(termios.ECHO | termios.ECHONL | termios.ICANON |
                           termios.ISIG | termios.IEXTEN)

        # 8N1, receiver on, ignore modem control lines, no HW flow control.
        attrs[_CFLAG] &= ~(termios.CSIZE | termios.PARENB | termios.CSTOPB |
                           termios.CRTSCTS)
        attrs[_CFLAG] |= termios.CS8 | termios.CREAD | termios.CLOCAL
        attrs[_ISPEED] = attrs[_OSPEED] = speed

        # VMIN must be >= 1: with VMIN=0/VTIME=0, n_tty returns 0 bytes (not
        # EAGAIN) on an empty buffer, indistinguishable from EOF/hangup.
        # O_NONBLOCK keeps read() from blocking regardless.
        cc = attrs[_CC]
        cc[termios.VMIN] = 1
        cc[termios.VTIME] = 0

        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        termios.tcflush(fd, termios.TCIFLUSH)  # drop pre-configuration input
    except BaseException:
        os.close(fd)
        raise
    return fd
