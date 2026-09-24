$!/bin/bash

socat -d -d pty,raw,echo=0,link=/tmp/ttyMOCK pty,raw,echo=0,link=/tmp/ttyMOCK_PEER