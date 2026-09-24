# Embedded Linux Engineer — Take-home Exercise

## Background

We deploy field devices that collect weather observations from RS485-connected sensors. Your task is to write the service that runs on the host: open the serial port, read observations from the sensor, and log them to the system journal. The whole thing should be packaged as a systemd unit.

## What to build

A service that opens a serial tty, configures it for the sensor, reads observations, and writes them to the system journal. The service should be packaged as a systemd unit — you don't need to install it on your machine, just include the unit file in your submission.

How the tty path is supplied (CLI argument, environment variable, config file, etc.) is your call.

## Setup

We provide a small Python mock that pretends to be the sensor and emits observations over the serial connection. The mock script is included alongside this document.

You'll create a paired PTY with `socat`, point the mock at one end, and point your service at the other:

```bash
# Terminal 1: create a pty pair
socat -d -d pty,raw,echo=0,link=/tmp/ttyMOCK pty,raw,echo=0,link=/tmp/ttyMOCK_PEER

# Terminal 2: run the mock against one end
python3 mock.py --serial /tmp/ttyMOCK --sensor a

# Terminal 3: your service reads from the other end (/tmp/ttyMOCK_PEER)
```

Paths and names are yours to change.

## Sensor specification

The sensor runs at 9600 baud, 8N1, and publishes one observation every 2 seconds, alternating between two packet types.

```
Wire format (9 bytes, little-endian, CRC-16/MODBUS):

  off  size  field
   0    2    sync          = 0xAA 0x55
   2    1    ptype         = 0x01 (temp + humidity)
                           | 0x02 (wind + rain)
   3    2    measurement 1 (int16, LE)
   5    2    measurement 2 (int16, LE)
   7    2    CRC-16/MODBUS over bytes 0–6, LE
```

## Constraints

- **Linux only.** A Raspberry Pi, a desktop, or a Linux VM all work fine.

## Time

We expect this to take 2–3 hours. If you find yourself approaching 4 hours, please stop and submit what you have, with notes on what you'd improve given more time. A partial, thoughtful submission is better than a stressed or late one.

## Submitting

Send us your code and unit file in any format that works for you — a gist, a zip in a cloud drive, a GitHub repo, whatever's easiest. Include a short README with anything you'd like us to know (e.g. how to run it).

We'll walk through your submission together in the on-site interview. The conversation matters as much as the code — be ready to talk through your reasoning, your trade-offs, and what you'd do differently if you had more time.