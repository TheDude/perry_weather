"""Streaming decoder for the Sensor A wire format.

Wire format (9 bytes, little-endian):

    [0..1] sync 0xAA 0x55
    [2]    ptype 0x01 = temp+humidity, 0x02 = wind+rain
    [3..4] measurement 1, int16
    [5..6] measurement 2, int16
    [7..8] CRC-16/MODBUS over bytes 0..6

Raw measurements are int16 tenths of a unit (per the mock: degC, %RH, m/s,
mm; rain is accumulated since the previous wind/rain packet).
"""

from __future__ import annotations

import enum
import struct
from dataclasses import dataclass
from typing import Callable, Optional, Union

FRAME_SIZE = 9
SYNC = b"\xaa\x55"

PTYPE_TEMP_HUMIDITY = 0x01
PTYPE_WIND_RAIN = 0x02

_BODY = struct.Struct("<Bhh")  # ptype, m1, m2 (bytes 2..6)
_CRC = struct.Struct("<H")


def crc16_modbus(data: bytes) -> int:
    """CRC-16/MODBUS: reflected poly 0x8005 (0xA001), init 0xFFFF, no xorout."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


@dataclass(frozen=True)
class TempHumidity:
    temp_dC: int        # 0.1 degC
    humidity_dPct: int  # 0.1 %RH


@dataclass(frozen=True)
class WindRain:
    wind_dMps: int  # 0.1 m/s
    rain_dMm: int   # 0.1 mm since previous wind/rain packet


Observation = Union[TempHumidity, WindRain]


class FrameError(enum.Enum):
    BAD_CRC = "crc_mismatch"          # sync found, CRC mismatch
    UNKNOWN_TYPE = "unknown_ptype"    # CRC valid, ptype not 0x01/0x02


@dataclass(frozen=True)
class FrameErrorEvent:
    error: FrameError
    raw: bytes


@dataclass
class ParserStats:
    frames_ok: int = 0
    crc_errors: int = 0
    unknown_type: int = 0
    bytes_discarded: int = 0  # bytes skipped while hunting for sync
    truncated: int = 0        # partial frames dropped by reset()

    def __str__(self) -> str:
        return (f"frames_ok={self.frames_ok} crc_errors={self.crc_errors} "
                f"unknown_type={self.unknown_type} truncated={self.truncated} "
                f"bytes_discarded={self.bytes_discarded}")


class FrameParser:
    """Byte-stream decoder. Feed it whatever read() returned; it never
    assumes a read boundary coincides with a frame boundary."""

    def __init__(self,
                 on_observation: Optional[Callable[[Observation], None]] = None,
                 on_error: Optional[Callable[[FrameErrorEvent], None]] = None):
        self._on_obs = on_observation
        self._on_err = on_error
        self._buf = bytearray()
        self.stats = ParserStats()

    @property
    def has_pending(self) -> bool:
        return bool(self._buf)

    def reset(self) -> None:
        """Drop a partially received frame (inter-frame gap, reconnect)."""
        if self._buf:
            self.stats.truncated += 1
            self._buf.clear()

    def feed(self, data: bytes) -> None:
        self._buf += data
        self._process()

    def _process(self) -> None:
        buf = self._buf
        while True:
            # 1. Hunt for the sync word; anything before it is noise.
            pos = buf.find(SYNC)
            if pos < 0:
                # Keep a trailing 0xAA: it may be the first half of a sync.
                keep = 1 if buf.endswith(SYNC[:1]) else 0
                self.stats.bytes_discarded += len(buf) - keep
                del buf[:len(buf) - keep]
                return
            if pos:
                self.stats.bytes_discarded += pos
                del buf[:pos]

            # 2. Wait for a whole frame.
            if len(buf) < FRAME_SIZE:
                return

            frame = bytes(buf[:FRAME_SIZE])
            ptype, m1, m2 = _BODY.unpack_from(frame, 2)
            (crc_rx,) = _CRC.unpack_from(frame, 7)
            crc_ok = crc16_modbus(frame[:7]) == crc_rx
            known = ptype in (PTYPE_TEMP_HUMIDITY, PTYPE_WIND_RAIN)

            # 3. On failure skip only the first sync byte and re-hunt, so a
            #    false sync in noise can't swallow a real frame that starts
            #    inside this 9-byte window. A flipped ptype bit is corruption,
            #    so CRC failure wins over "unknown type".
            if not (crc_ok and known):
                if crc_ok:
                    err = FrameError.UNKNOWN_TYPE
                    self.stats.unknown_type += 1
                else:
                    err = FrameError.BAD_CRC
                    self.stats.crc_errors += 1
                self.stats.bytes_discarded += 1
                del buf[:1]
                if self._on_err:
                    self._on_err(FrameErrorEvent(err, frame))
                continue

            del buf[:FRAME_SIZE]
            self.stats.frames_ok += 1
            if self._on_obs:
                if ptype == PTYPE_TEMP_HUMIDITY:
                    self._on_obs(TempHumidity(m1, m2))
                else:
                    self._on_obs(WindRain(m1, m2))
