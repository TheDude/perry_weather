# weather-sensor (Python)

A Python daemon that reads observations from the RS485 weather sensor
("Sensor A": 9600 8N1, 9-byte little-endian frames, CRC-16/MODBUS) on a serial
tty and writes them to the systemd journal, packaged as a templated systemd
unit. **Standard library only** (Python ≥ 3.8), no pip installs on the device.

This is a port of the C++ solution on the `claude-from-scratch` branch; the
design and behaviour are the same.

## Layout

```
weather_sensor/
  frame_parser.py   CRC + streaming frame decoder (pure, unit tested)
  serial_port.py    termios setup
  systemd.py        journald native protocol + sd_notify, stdlib only
  sinks.py          journal / stderr output
  service.py        poll loop, reconnect, signals, watchdog, CLI
tests/              unittest
systemd/weather-sensor@.service
scripts/            demo.sh, build_zipapp.sh, mock.py (from the exercise)
```

## Test, run, package

```bash
python3 -m unittest -v                      # 12 tests
CORRUPT=0.2 scripts/demo.sh 20              # socat + mock + service, 20 s

# by hand
socat -d -d pty,raw,echo=0,link=/tmp/ttyMOCK pty,raw,echo=0,link=/tmp/ttyMOCK_PEER
python3 scripts/mock.py --serial /tmp/ttyMOCK --sensor a
python3 -m weather_sensor /tmp/ttyMOCK_PEER            # journal
python3 -m weather_sensor --stderr /tmp/ttyMOCK_PEER   # terminal

# single-file executable for the device
scripts/build_zipapp.sh
sudo install -m755 dist/weather-sensor /usr/local/bin/
sudo install -m644 systemd/weather-sensor@.service /etc/systemd/system/
```

The zipapp is one self-contained file, which avoids fighting PEP 668
("externally managed environment") with `pip install` on a modern distro.

### CLI

```
weather-sensor [options] [DEVICE]
  DEVICE                  serial tty (or $WEATHER_TTY)
  -b, --baud N            default 9600
  -s, --stale-timeout MS  warn if no valid frame for MS (default 10000)
  -g, --frame-gap MS      drop a partial frame after MS of silence (default 100)
  -e, --stderr            log to stderr instead of the journal
```

## Journal output

One journal entry per observation, with a readable `MESSAGE` plus structured
fields:

```
$ journalctl -t weather-sensor -o cat
wind=2.8m/s rain=0.0mm
Dropped frame (crc_mismatch): aa 55 01 e2 00 1f 02 bf 46

$ journalctl -t weather-sensor WEATHER_PTYPE=temp_humidity -o json
{ "MESSAGE": "temperature=22.2C humidity=54.8%RH", "PRIORITY": "6",
  "WEATHER_PTYPE": "temp_humidity", "WEATHER_TEMP_C": "22.2",
  "WEATHER_HUMIDITY_PCT": "54.8", "WEATHER_DEVICE": "/dev/ttyUSB0", ... }
```

| Priority | Event |
|---|---|
| info    | observation, SIGUSR1 stats |
| notice  | port opened, data resumed, shutdown (with final stats) |
| warning | CRC mismatch / unknown ptype (raw bytes included), partial frame dropped, no data for `--stale-timeout` |
| err     | open/read failure (first failure per outage only, then silent retries with backoff) |

## systemd unit

Template; the instance is the systemd-escaped device path:

```bash
systemctl enable --now weather-sensor@dev-ttyUSB0.service
systemctl enable --now "weather-sensor@$(systemd-escape --path /dev/serial/by-id/usb-FTDI_...-port0).service"
```

`Type=notify` with `STATUS=` updates, `WatchdogSec=30` (pinged from the poll
loop), and hardening: `DynamicUser` + `dialout`, `DevicePolicy=closed` with
only the tty allowed, no network, no capabilities, syscall filter.
`systemd-analyze security` rates it 0.7 (SAFE). Per-site flags go in
`Environment=WEATHER_ARGS=...` via `systemctl edit`.

## Design notes

**Framing.** `FrameParser` hunts for `AA 55`, waits for 9 bytes, checks CRC
and ptype. On failure it drops **one** byte and re-hunts, so a false sync in
noise can't swallow a real frame that starts inside the window. A trailing lone
`AA` is kept in case it's half a sync word. A bad ptype with a bad CRC is
reported as CRC corruption; only a CRC-valid unknown ptype is `unknown_ptype`
(that would mean protocol drift).

**Timing as a second framing signal.** A frame takes ~9.4 ms at 9600 baud,
frames are 2 s apart. If bytes stop mid-frame for `--frame-gap`, the partial
frame is dropped so it isn't glued onto the next one.

**Serial setup.** Raw mode (the `cfmakeraw` flags, spelled out), 8N1,
`CLOCAL|CREAD`, no RTS/CTS, `TIOCEXCL`, `TCIFLUSH` after configuring.
`VMIN=1` + `O_NONBLOCK`: with `VMIN=0/VTIME=0` an empty tty read returns
`b""` instead of raising `BlockingIOError`, which looks exactly like EOF.
(The C++ version hit this first.)

**Event loop.** Single thread, `select.poll()` on the tty and a
`signal.set_wakeup_fd` socket. Signal handlers only append to a list; the loop
handles them, which is the Python equivalent of the C++ `signalfd`.

**journald / sd_notify without dependencies.** Both are datagrams on a Unix
socket, so `systemd.py` implements them in ~60 lines rather than requiring
`python3-systemd` (a compiled extension). Trade-off: I own a small amount of
protocol code. If journald is unreachable, messages fall back to stderr, which
systemd captures anyway, so a logging failure never stops the reader.

**Resilience.** HUP/EIO/EOF closes the port, resets the parser and reopens with
exponential backoff (0.5 s → 30 s). The open error is logged once per outage.

**Units.** Raw values are int16 tenths (degC, %RH, m/s, mm per the mock; rain
is accumulated since the previous wind/rain packet), formatted with integer
arithmetic so `-5` prints `-0.5`.

## Python vs. the C++ version

- CPU/memory: at 9 bytes every 2 s the load is irrelevant either way; the
  Python process sits around 10 MB RSS vs. well under 1 MB for C++. That matters
  only on a very small target.
- Deployment: the device needs a Python 3 runtime; C++ needs `libsystemd` and a
  cross toolchain. Neither needs anything else.
- Pure-Python CRC is a per-bit loop; fine at this rate, and a 256-entry table
  would be the first change if throughput ever mattered.

## Mock / spec discrepancies

- `mock.py`'s framing comment says CRC covers bytes `[0..7]`; the code and the
  README cover 0–6. I followed the code; tests include golden frames from the
  mock itself.
- The mock's docstring mentions "gated by two GPIO lines" with no GPIO code.
  Ignored.

## What I'd do with more time

- **Sensor B** (4800 baud, big-endian, CCITT-FALSE, 12-byte frame): one decoder
  per sensor behind a common interface, selected by flag or auto-detected.
- `TIOCSRS485` if we ever transmit, so the driver handles DE/RE.
- Plausibility checks (humidity 0–100 %, rate of change) as warnings.
- Run as a real unit under PID 1 systemd on hardware. Verified here: unit file
  (`systemd-analyze verify`/`security`), journal output against a standalone
  journald, and sd_notify `READY`/`STATUS`/`WATCHDOG`/`STOPPING` against a fake
  `NOTIFY_SOCKET`; not the sandbox settings in a live run.
- Integration test in CI driving socat + mock and asserting on counters.
