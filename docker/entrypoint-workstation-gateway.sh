#!/bin/sh
set -eu

squid -N -d 1 &
squid_pid=$!
websockify --web /usr/share/novnc --token-plugin TokenFile \
  --token-source /run/ancos-vnc-tokens 6080
kill "$squid_pid" 2>/dev/null || true
