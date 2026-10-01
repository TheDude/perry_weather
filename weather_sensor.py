#!/usr/bin/env python3
"""Read observations from the weather sensor on a serial port and print them.

Under systemd, stdout goes to the journal. The "<N>" prefix on each line is a
syslog priority that journald strips and records as PRIORITY, so CRC errors
show up as warnings (journalctl -p warning).

On a serial error the program exits non-zero and systemd restarts it
(Restart=on-failure in the unit file).
"""

import argparse
import sys

import serial

from frame_parser import BadFrame, FrameParser, TempHumidity, WindRain

LOG_ERR, LOG_WARNING, LOG_INFO = 3, 4, 6


def log(priority: int, text: str) -> None:
    # flush: under systemd stdout is a pipe, so it's block-buffered and lines
    # would otherwise reach the journal late and in batches.
    print(f"<{priority}>{text}", flush=True)


def tenths(v: int) -> str:
    """235 -> '23.5', -5 -> '-0.5' (integer math, no float rounding)."""
    return f"{'-' if v < 0 else ''}{abs(v) // 10}.{abs(v) % 10}"


def report(result) -> None:
    if isinstance(result, TempHumidity):
        log(LOG_INFO, f"temperature={tenths(result.temp)}C humidity={tenths(result.humidity)}%RH")
    elif isinstance(result, WindRain):
        log(LOG_INFO, f"wind={tenths(result.wind)}m/s rain={tenths(result.rain)}mm")
    elif isinstance(result, BadFrame):
        log(LOG_WARNING, f"dropped frame ({result.reason}): {result.raw.hex(' ')}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("device", help="serial port, e.g. /dev/ttyUSB0")
    args = ap.parse_args()

    parser = FrameParser()
    try:
        # pyserial defaults are 8N1, no flow control; it puts the tty in raw mode.
        with serial.Serial(args.device, baudrate=9600) as port:
            log(LOG_INFO, f"reading {args.device} at 9600 8N1")
            while True:
                # Block for at least one byte, then take whatever else is waiting.
                data = port.read(port.in_waiting or 1)
                for result in parser.feed(data):
                    report(result)
    except serial.SerialException as e:
        log(LOG_ERR, f"serial error on {args.device}: {e}")
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
