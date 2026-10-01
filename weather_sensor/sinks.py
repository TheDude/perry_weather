"""Log output.

Under systemd, stdout goes to the journal. The "<N>" prefix is a syslog
priority that journald strips and records as PRIORITY (SyslogLevelPrefix=,
on by default), so warnings and errors stay distinguishable from readings.
"""

from __future__ import annotations

from .frame_parser import FrameErrorEvent, Observation, TempHumidity

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


class Sink:
    def message(self, priority: int, text: str) -> None:
        # flush: stdout is a pipe under systemd, so it's block-buffered and
        # lines would otherwise reach the journal late and in batches.
        print(f"<{priority}>{text}", flush=True)

    def observation(self, obs: Observation) -> None:
        self.message(LOG_INFO, describe(obs))

    def frame_error(self, ev: FrameErrorEvent) -> None:
        self.message(LOG_WARNING, f"Dropped frame ({ev.error.value}): {ev.raw.hex(' ')}")
