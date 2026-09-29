# weather-sensor

A small C++17 daemon that reads observations from the RS485 weather sensor
("Sensor A": 9600 8N1, 9-byte little-endian frames, CRC-16/MODBUS) on a serial
tty and writes them to the systemd journal, packaged as a templated systemd unit.

## Build

Needs a C++17 compiler, CMake ≥ 3.16 and `libsystemd-dev`.

```bash
cmake -S . -B build
cmake --build build -j
ctest --test-dir build --output-on-failure
sudo cmake --install build          # /usr/local/bin + unit file
```

## Run against the mock

`scripts/demo.sh` creates the socat pty pair, starts `mock.py` (copied from the
exercise gist) with a high corruption rate, runs the service for N seconds,
dumps stats (SIGUSR1) and stops it (SIGTERM):

```bash
CORRUPT=0.2 scripts/demo.sh 20
```

Or by hand, following the exercise:

```bash
socat -d -d pty,raw,echo=0,link=/tmp/ttyMOCK pty,raw,echo=0,link=/tmp/ttyMOCK_PEER
python3 scripts/mock.py --serial /tmp/ttyMOCK --sensor a
./build/weather-sensor /tmp/ttyMOCK_PEER            # journal
./build/weather-sensor --stderr /tmp/ttyMOCK_PEER   # terminal
```

### CLI

```
weather-sensor [options] [DEVICE]
  DEVICE                  serial tty (or $WEATHER_TTY)
  -b, --baud N            default 9600
  -s, --stale-timeout MS  warn if no valid frame for MS (default 10000)
  -g, --frame-gap MS      drop a partial frame after MS of silence (default 100)
  -e, --stderr            log to stderr instead of the journal
```

The device is a positional argument so the systemd template can pass it
straight from the instance name; `$WEATHER_TTY` is a fallback.

## Journal output

Each observation is one journal entry with a human-readable `MESSAGE` and
structured fields, so consumers don't have to parse text:

```
$ journalctl -t weather-sensor -f
weather-sensor[1158]: temperature=21.7C humidity=55.5%RH
weather-sensor[1158]: wind=3.3m/s rain=0.0mm
weather-sensor[1158]: Dropped frame (crc_mismatch): aa 55 82 22 00 00 00 6e b7

$ journalctl -t weather-sensor WEATHER_PTYPE=wind_rain -o json
{ "MESSAGE": "wind=3.3m/s rain=0.0mm", "PRIORITY": "6",
  "WEATHER_PTYPE": "wind_rain", "WEATHER_WIND_MPS": "3.3", "WEATHER_RAIN_MM": "0.0",
  "WEATHER_DEVICE": "/dev/ttyUSB0", ... }
```

| Priority | Event |
|---|---|
| info    | observation, SIGUSR1 stats |
| notice  | port opened, data resumed, shutdown (with final stats) |
| warning | CRC mismatch / unknown ptype (raw bytes included), partial frame dropped, no data for `--stale-timeout` |
| err     | open/read failure (first failure per outage only, then silent retries with backoff) |

## systemd unit

`systemd/weather-sensor@.service` is a template; the instance is the
systemd-escaped device path:

```bash
systemctl enable --now weather-sensor@dev-ttyUSB0.service
systemctl enable --now "weather-sensor@$(systemd-escape --path /dev/serial/by-id/usb-FTDI_...-port0).service"
```

- `Type=notify`: signals `READY=1`, keeps `STATUS=` current (`systemctl status`
  shows "Reading /dev/ttyUSB0" or "Waiting for /dev/ttyUSB0").
- `WatchdogSec=30`: the poll loop pings the watchdog, so a wedged process is
  restarted. This is deliberately separate from "sensor silent", which is a
  logged warning, not a restart: restarting the reader won't fix a cut cable.
- Hardened: `DynamicUser` + `dialout`, `DevicePolicy=closed` with only the
  tty allowed, no network, no capabilities, syscall filter.
  `systemd-analyze security` rates it 0.7 (SAFE).
- `Environment=WEATHER_ARGS=...` via `systemctl edit` for per-site flags.

## Design notes

**Framing.** `FrameParser` is a byte-stream decoder with no OS dependencies
(unit-tested separately). It hunts for `AA 55`, waits for 9 bytes, then checks
CRC and ptype. On any failure it drops **one** byte and re-hunts rather than
the whole 9, so a false sync in noise can't swallow a real frame that starts
inside the window. A trailing lone `AA` is kept in case it's half a sync word.

**Timing as a second framing signal.** A frame takes ~9.4 ms at 9600 baud and
frames are 2 s apart. If bytes stop mid-frame for `--frame-gap` (100 ms), the
partial frame is discarded, so a truncated frame is reported as truncated
instead of being glued onto the next one and showing up as a bogus CRC error.

**CRC vs. unknown ptype.** A frame with a bad ptype *and* bad CRC is reported
as a CRC error (corruption), not an unknown type. Only a CRC-valid frame with an
unrecognised ptype is `unknown_ptype` — that would mean firmware/protocol
drift and is worth distinguishing.

**Serial setup.** `cfmakeraw` (no ICRNL/IXON etc. — otherwise `0x0D`, `0x11`,
`0x13` in binary payloads get translated or eaten), 8N1, `CLOCAL|CREAD`, no
RTS/CTS, `TIOCEXCL` so a second process can't silently steal bytes,
`TCIFLUSH` after configuration to drop stale input.

**Event loop.** Single thread, `poll()` on the tty and a `signalfd`. Signals
are just fd events, so there are no async-signal-safety concerns. SIGTERM/SIGINT
shut down cleanly with final stats; SIGUSR1 logs stats.

**Resilience.** The device can disappear (USB adapter unplugged, pty closed).
HUP/EIO/EOF closes the port, resets the parser and reopens with exponential
backoff (0.5 s → 30 s). The open error is logged once per outage.

**Units.** Raw values are int16 tenths (per the mock's docstring: degC, %RH,
m/s, mm; rain is accumulated since the previous wind/rain packet). Formatting
is done in integer arithmetic so `-5` prints `-0.5`, never `-0.50000001`.

## Things I hit along the way

- **`VMIN=0, VTIME=0` + `O_NONBLOCK` returns 0, not `EAGAIN`** on an empty
  tty. My first version used that and treated every drained buffer as
  EOF/hangup, reconnecting after every frame. Fixed with `VMIN=1` (still
  non-blocking because of `O_NONBLOCK`), so 0 really means EOF.
- `mock.py`'s framing comment says the CRC covers bytes `[0..7]`; the code
  (and the exercise README) cover bytes 0–6. I followed the code; the tests
  include golden frames produced by the mock itself.
- The mock's docstring mentions "gated by two GPIO lines" but there's no GPIO
  code in it, and the exercise doesn't mention it. Ignored.

## What I'd do with more time

- **Sensor B** (4800 baud, big-endian, CCITT-FALSE, 12-byte all-in-one frame)
  is in the mock. The parser would become one decoder per sensor behind a small
  interface, selected by flag or auto-detected by trying baud rates and
  looking for valid CRCs.
- **RS485 specifics.** This is receive-only so direction control doesn't
  matter, but if we ever transmit (polling, config) I'd set `TIOCSRS485` so
  the UART driver toggles DE/RE instead of user space.
- **Plausibility checks** (humidity 0–100 %, temperature range, rate of
  change) as a warning — CRC proves the bytes arrived intact, not that the
  sensor is healthy.
- **Real-hardware test** under systemd with the unit installed. I verified the
  unit with `systemd-analyze verify`/`security` and exercised the journal
  output against a standalone journald, but haven't run it as a real unit with
  PID 1 systemd (no `Type=notify`/watchdog/sandbox run-through).
- Integration test in CI driving socat + mock and asserting on counters.
- Packaging (`.deb`/Yocto recipe) instead of `cmake --install`.
