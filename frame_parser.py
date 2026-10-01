"""Streaming decoder for the sensor's wire format.

    off  size  field
     0    2    sync 0xAA 0x55
     2    1    ptype 0x01 = temp+humidity, 0x02 = wind+rain
     3    2    measurement 1 (int16, LE)
     5    2    measurement 2 (int16, LE)
     7    2    CRC-16/MODBUS over bytes 0..6 (LE)

Measurements are tenths of a unit (per the mock: degC, %RH, m/s, mm).
"""

import struct
from dataclasses import dataclass

SYNC = b"\xaa\x55"
FRAME_SIZE = 9


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


@dataclass
class TempHumidity:
    temp: int      # 0.1 degC
    humidity: int  # 0.1 %RH


@dataclass
class WindRain:
    wind: int  # 0.1 m/s
    rain: int  # 0.1 mm since the previous wind/rain packet


@dataclass
class BadFrame:
    reason: str  # "crc_mismatch" or "unknown_ptype"
    raw: bytes


class FrameParser:
    """Feed it bytes as they arrive; it returns whatever frames completed.
    Read boundaries don't need to line up with frame boundaries."""

    def __init__(self):
        self.buf = bytearray()

    def feed(self, data: bytes) -> list:
        self.buf += data
        results = []
        while True:
            # Discard noise before the sync word, but keep a trailing 0xAA:
            # it may be the first half of the next sync.
            pos = self.buf.find(SYNC)
            if pos < 0:
                del self.buf[:-1 if self.buf.endswith(b"\xaa") else len(self.buf)]
                return results
            del self.buf[:pos]

            if len(self.buf) < FRAME_SIZE:
                return results

            frame = bytes(self.buf[:FRAME_SIZE])
            ptype, m1, m2, crc = struct.unpack("<BhhH", frame[2:])

            if crc != crc16_modbus(frame[:7]):
                result = BadFrame("crc_mismatch", frame)
            elif ptype == 0x01:
                result = TempHumidity(m1, m2)
            elif ptype == 0x02:
                result = WindRain(m1, m2)
            else:
                result = BadFrame("unknown_ptype", frame)
            results.append(result)

            # On a bad frame, skip only the first sync byte and search again:
            # an "AA 55" in noise must not swallow a real frame starting
            # inside these 9 bytes.
            del self.buf[:1 if isinstance(result, BadFrame) else FRAME_SIZE]
