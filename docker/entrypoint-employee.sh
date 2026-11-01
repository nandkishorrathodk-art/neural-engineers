#!/bin/bash
set -e

# Remove stale VNC locks if any
rm -f /tmp/.X1-lock /tmp/.X11-unix/X1 || true

# Start TigerVNC server on :1 (port 5901)
vncserver :1 -geometry 1280x800 -depth 24 -localhost no

# Start noVNC proxy on port 6081 forwarding to localhost:5901
websockify --web /usr/share/novnc 6081 localhost:5901 &

echo "Employee workstation active: VNC on 5901, noVNC on 6081"

# Keep container running
tail -f /dev/null

