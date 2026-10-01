#!/bin/bash

socat -d -d pty,raw,echo=0,link=/tmp/ttyMOCK pty,raw,echo=0,link=/tmp/ttyMOCK_PEER &
python3 mock.py --serial /tmp/ttyMOCK --sensor a 2>&1