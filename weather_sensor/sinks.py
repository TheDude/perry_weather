"""Where observations and diagnostics go.

The journal sink attaches structured fields so records can be filtered and
extracted with `journalctl -o json` instead of parsing MESSAGE text.
"""

from __future__ import annotations

import sys
from typing import Protocol

from .frame_parser import FrameErrorEvent, Observation, TempHumidity, WindRain
from .systemd import Journal

# syslog priorities
LOG_ERR, LOG_WARNING, LOG_NOTICE, LOG_INFO = 3, 4, 5, 6


def tenths(v: int) -> str:
    """Fixed-point tenths -> '23.5' / '-0.5' without going through float."""
    mag = abs(v)
    return f"{'-' if v < 0 else ''}{mag // 10}.{mag % 10}"


def describe(obs: Observation) -> str:
    if isinstance(obs, TempHumidity):
        return f"temperature={tenths(obs.temp_dC)}C humidity={tenths(obs.humidity_dPct)}%RH"
    return f"wind={tenths(obs.wind_dMps)}m/s rain={tenths(obs.rain_dMm)}mm"


class Sink(Protocol):
    def observation(self, obs: Observation) -> None: ...
    def frame_error(self, ev: FrameErrorEvent) -> None: ...
    def message(self, priority: int, text: str) -> None: ...


class JournalSink:
    def __init__(self, device: str, identifier: str = "weather-sensor"):
        self._device = device
        self._j = Journal(identifier)

    def observation(self, obs: Observation) -> None:
        fields: dict = {"WEATHER_DEVICE": self._device}
        if isinstance(obs, TempHumidity):
            fields.update(WEATHER_PTYPE="temp_humidity",
                          WEATHER_TEMP_C=tenths(obs.temp_dC),
                          WEATHER_HUMIDITY_PCT=tenths(obs.humidity_dPct))
        elif isinstance(obs, WindRain):
            fields.update(WEATHER_PTYPE="wind_rain",
                          WEATHER_WIND_MPS=tenths(obs.wind_dMps),
                          WEATHER_RAIN_MM=tenths(obs.rain_dMm))
        self._j.send(describe(obs), LOG_INFO, fields)

    def frame_error(self, ev: FrameErrorEvent) -> None:
        raw = ev.raw.hex(" ")
        self._j.send(f"Dropped frame ({ev.error.value}): {raw}", LOG_WARNING, {
            "WEATHER_DEVICE": self._device,
            "WEATHER_FRAME_ERROR": ev.error.value,
            "WEATHER_RAW": raw,
        })

    def message(self, priority: int, text: str) -> None:
        self._j.send(text, priority, {"WEATHER_DEVICE": self._device})


class StderrSink:
    """For running by hand / in CI where there is no journald."""

    _LEVEL = {LOG_ERR: "error", LOG_WARNING: "warning", LOG_NOTICE: "notice", LOG_INFO: "info"}

    def _emit(self, priority: int, text: str) -> None:
        level = "error" if priority <= LOG_ERR else self._LEVEL.get(priority, "info")
        print(f"{level}: {text}", file=sys.stderr, flush=True)

    def observation(self, obs: Observation) -> None:
        self._emit(LOG_INFO, describe(obs))

    def frame_error(self, ev: FrameErrorEvent) -> None:
        self._emit(LOG_WARNING, f"Dropped frame ({ev.error.value}): {ev.raw.hex(' ')}")

    def message(self, priority: int, text: str) -> None:
        self._emit(priority, text)
