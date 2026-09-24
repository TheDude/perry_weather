#!/usr/bin/env python3
"""Mock weather-sensor data generator that publishes over a serial TTY,
gated by two GPIO lines.

The script emulates ONE of two sensors (chosen randomly at startup):

  Sensor A: 9600 baud, little-endian, CRC-16/MODBUS
            Publishes every 2 seconds, ALTERNATING between
            (temperature, humidity) and (wind speed, rain).

  Sensor B: 4800 baud, big-endian, CRC-16/CCITT-FALSE
            Publishes every 10 seconds, ALL FOUR measurements in
            one packet.

A small fraction of packets have a single random bit flipped AFTER the
CRC is computed, to exercise CRC-failure paths on the receiver.

All measurements are 16-bit signed integers in tenths of the unit
(e.g. 235 == 23.5 degC).  Units are metric: degC, %RH, m/s, mm.

Standard library only. Requires Linux >= 5.10 for the GPIO uAPI v2.
"""

import argparse
import fcntl
import os
import random
import struct
import sys
import termios
import time

# ---------------------------------------------------------------------------
# Serial TTY setup via termios
# ---------------------------------------------------------------------------

_BAUD_MAP = {
    1200: termios.B1200,   2400: termios.B2400,   4800: termios.B4800,
    9600: termios.B9600,  19200: termios.B19200, 38400: termios.B38400,
   57600: termios.B57600, 115200: termios.B115200,
}


def open_serial(path, baud):
    """Open `path` as an 8N1 raw TTY at the given baud rate."""
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_CLOEXEC)
    try:
        attrs = termios.tcgetattr(fd)
    except termios.error:
        # Not a tty (e.g. a FIFO used for testing). Just write bytes through.
        return fd

    b = _BAUD_MAP.get(baud)
    if b is None:
        os.close(fd)
        raise ValueError(f"unsupported baud rate {baud}")

    # [iflag, oflag, cflag, lflag, ispeed, ospeed, cc]
    iflag  = 0
    oflag  = 0
    cflag  = termios.CS8 | termios.CREAD | termios.CLOCAL
    lflag  = 0
    ispeed = b
    ospeed = b
    cc = list(attrs[6])
    cc[termios.VMIN]  = 1
    cc[termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW,
                      [iflag, oflag, cflag, lflag, ispeed, ospeed, cc])
    termios.tcflush(fd, termios.TCIOFLUSH)
    return fd


# ---------------------------------------------------------------------------
# CRC algorithms
# ---------------------------------------------------------------------------

def crc16_modbus(data):
    """CRC-16/MODBUS: poly 0xA001 (reflection of 0x8005), init 0xFFFF."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if (crc & 1) else (crc >> 1)
    return crc & 0xFFFF


def crc16_ccitt_false(data):
    """CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, no reflection, no xorout."""
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


# ---------------------------------------------------------------------------
# Realistic sensor state (smooth random walk)
# ---------------------------------------------------------------------------

class _Walker:
    """Random walk on [lo, hi], stepping by up to `step` per call.

    Produces a more realistic time-series than independent uniform draws.
    """
    def __init__(self, start, lo, hi, step):
        self.v    = float(start)
        self.lo   = lo
        self.hi   = hi
        self.step = step

    def next(self):
        self.v += random.uniform(-self.step, self.step)
        if self.v < self.lo:
            self.v = self.lo + random.uniform(0, self.step)
        if self.v > self.hi:
            self.v = self.hi - random.uniform(0, self.step)
        return int(round(self.v))


class SensorState:
    """All quantities in tenths of a unit, fitting comfortably in int16."""
    def __init__(self):
        # Temperature: -50 .. 350      -> -5.0 .. 35.0 degC
        self.temp  = _Walker(220, -50, 350, 5)
        # Humidity:    200 .. 950      -> 20.0 .. 95.0 %RH
        self.humid = _Walker(550, 200, 950, 10)
        # Wind speed:    0 .. 200      ->  0.0 .. 20.0 m/s
        self.wind  = _Walker(30,    0, 200, 3)
        # Rain accumulator (tenths of mm) since last wind/rain emission.
        self.rain_accum = 0

    def tick_rain(self):
        # Mostly dry; occasional drizzle nudges the accumulator.
        if random.random() < 0.15:
            self.rain_accum += random.randint(0, 8)
        # Belt-and-braces clamp to keep us inside int16.
        if self.rain_accum > 32000:
            self.rain_accum = 0


# ---------------------------------------------------------------------------
# Framing
# ---------------------------------------------------------------------------

def maybe_corrupt(frame, p):
    """With probability `p`, flip a single random bit after the sync header.

    Returns (possibly_corrupted_frame, was_corrupted).  We deliberately leave
    the 2-byte sync prefix alone so the receiver can still frame the packet
    and detect the CRC mismatch, rather than just losing sync entirely.
    """
    if len(frame) > 2 and random.random() < p:
        ba = bytearray(frame)
        idx = random.randrange(2, len(ba))
        ba[idx] ^= 1 << random.randrange(8)
        return bytes(ba), True
    return frame, False


# Sensor A wire format (9 bytes, little-endian, CRC-16/MODBUS):
#
#   off  size  field
#    0    2    sync   = 0xAA 0x55
#    2    1    ptype  = 0x01 (temp+humid)  |  0x02 (wind+rain)
#    3    2    measurement 1 (int16, LE)
#    5    2    measurement 2 (int16, LE)
#    7    2    CRC-16/MODBUS over bytes [0..7], LE

_SENSOR_A_SYNC = b'\xAA\x55'

def build_a_temp_humid(temp10, humid10):
    body  = struct.pack('<Bhh', 0x01, temp10, humid10)
    frame = _SENSOR_A_SYNC + body
    return frame + struct.pack('<H', crc16_modbus(frame))

def build_a_wind_rain(wind10, rain10):
    body  = struct.pack('<Bhh', 0x02, wind10, rain10)
    frame = _SENSOR_A_SYNC + body
    return frame + struct.pack('<H', crc16_modbus(frame))


# Sensor B wire format (12 bytes, big-endian, CRC-16/CCITT-FALSE):
#
#   off  size  field
#    0    2    sync   = 0x55 0xAA
#    2    2    temperature (int16, BE)
#    4    2    humidity    (int16, BE)
#    6    2    wind speed  (int16, BE)
#    8    2    rain accum  (int16, BE)
#   10    2    CRC-16/CCITT-FALSE over bytes [0..9], BE

_SENSOR_B_SYNC = b'\x55\xAA'

def build_b_all(temp10, humid10, wind10, rain10):
    body  = struct.pack('>hhhh', temp10, humid10, wind10, rain10)
    frame = _SENSOR_B_SYNC + body
    return frame + struct.pack('>H', crc16_ccitt_false(frame))


# ---------------------------------------------------------------------------
# Per-sensor run loops
# ---------------------------------------------------------------------------

def _sleep_until(t_target):
    delay = t_target - time.monotonic()
    if delay > 0:
        time.sleep(delay)


def run_sensor_a(serial_fd, state, corrupt_prob, log):
    """9600 baud, every 2 s, alternating between (T,H) and (W,R)."""
    toggle = 0
    next_t = time.monotonic() + 2.0
    while True:
        # The physical world keeps happening even when we're gated off.
        state.tick_rain()

        if toggle == 0:
            t = state.temp.next()
            h = state.humid.next()
            frame = build_a_temp_humid(t, h)
            desc  = f"T={t/10:+6.1f}C H={h/10:5.1f}%"
        else:
            w = state.wind.next()
            r = state.rain_accum
            state.rain_accum = 0
            frame = build_a_wind_rain(w, r)
            desc  = f"W={w/10:5.1f}m/s R={r/10:5.1f}mm"
        out, corrupted = maybe_corrupt(frame, corrupt_prob)
        os.write(serial_fd, out)
        log(f"[A 9600] {desc}  {len(out)}B "
            f"{'CRC-CORRUPT' if corrupted else 'ok'}")
        toggle ^= 1

        _sleep_until(next_t)
        next_t += 2.0


def run_sensor_b(serial_fd, state, corrupt_prob, log):
    """4800 baud, every 10 s, all four measurements in one packet."""
    next_t = time.monotonic() + 10.0
    while True:
        # Tick the rain a few times across the 10-second window.
        for _ in range(5):
            state.tick_rain()

        t = state.temp.next()
        h = state.humid.next()
        w = state.wind.next()
        r = state.rain_accum
        state.rain_accum = 0
        frame = build_b_all(t, h, w, r)
        out, corrupted = maybe_corrupt(frame, corrupt_prob)
        os.write(serial_fd, out)
        log(f"[B 4800] T={t/10:+6.1f}C H={h/10:5.1f}% "
            f"W={w/10:5.1f}m/s R={r/10:5.1f}mm  "
            f"{len(out)}B {'CRC-CORRUPT' if corrupted else 'ok'}")

        _sleep_until(next_t)
        next_t += 10.0


def main():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument('--serial', default='/dev/ttyUSB0',
                   help='serial tty device path (default: %(default)s)')
    p.add_argument('--sensor', choices=['a', 'b', 'random'], default='random',
                   help='which sensor to emulate (default: random at startup)')
    p.add_argument('--seed', type=int, default=None,
                   help='random seed (default: nondeterministic)')
    p.add_argument('--corrupt-prob', type=float, default=0.05,
                   help='per-packet probability of a single bit flip '
                        '(default: %(default)s)')
    p.add_argument('--verbose', '-v', action='store_true',
                   help='log each packet to stderr')
    args = p.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    sensor = args.sensor
    if sensor == 'random':
        sensor = random.choice(['a', 'b'])
    baud = 9600 if sensor == 'a' else 4800

    print(f"mock-sensor: emulating sensor {sensor.upper()} at {baud} baud "
          f"on {args.serial}", file=sys.stderr)

    serial_fd = open_serial(args.serial, baud)

    def log(msg):
        if args.verbose:
            print(msg, file=sys.stderr)

    state = SensorState()
    try:
        if sensor == 'a':
            run_sensor_a(serial_fd, state, args.corrupt_prob, log)
        else:
            run_sensor_b(serial_fd, state, args.corrupt_prob, log)
    except KeyboardInterrupt:
        print("\nmock-sensor: interrupted, exiting", file=sys.stderr)
    finally:
        try: os.close(serial_fd)
        except OSError: pass


if __name__ == '__main__':
    main()