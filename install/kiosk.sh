#!/usr/bin/env bash
# Launch Chromium full-screen on the tap list. Run at desktop login on the Pi.
set -u

URL="${TAPLIST_URL:-http://localhost:8080/}"
PROFILE="${HOME}/.config/taplist-kiosk"

# Only one kiosk at a time (XDG autostart and compositor autostart can both fire).
exec 9>"/tmp/taplist-kiosk.lock"
if ! flock -n 9; then
  echo "taplist kiosk already running" >&2
  exit 0
fi

# Wait (up to 90 s) for the server to come up after boot.
for _ in $(seq 1 45); do
  if curl -fs -o /dev/null "${URL}api/version" 2>/dev/null; then break; fi
  sleep 2
done

for bin in chromium chromium-browser; do
  if command -v "$bin" >/dev/null 2>&1; then CHROME="$bin"; break; fi
done
if [ -z "${CHROME:-}" ]; then
  echo "chromium not found - install it with: sudo apt install chromium" >&2
  exit 1
fi

# Keep the screen awake on X11 sessions (Wayland sessions use raspi-config's blanking setting).
if [ -n "${DISPLAY:-}" ] && command -v xset >/dev/null 2>&1; then
  xset s off; xset -dpms; xset s noblank
fi

# Clear the "Chromium didn't shut down correctly" bubble after a power cut.
mkdir -p "$PROFILE/Default"
if [ -f "$PROFILE/Default/Preferences" ]; then
  sed -i 's/"exited_cleanly":false/"exited_cleanly":true/; s/"exit_type":"Crashed"/"exit_type":"Normal"/' \
    "$PROFILE/Default/Preferences"
fi

exec "$CHROME" \
  --kiosk "$URL" \
  --user-data-dir="$PROFILE" \
  --noerrdialogs \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --disable-restore-session-state \
  --disable-pinch \
  --overscroll-history-navigation=0 \
  --touch-events=enabled \
  --hide-scrollbars \
  --check-for-update-interval=31536000 \
  --password-store=basic \
  --disable-features=TranslateUI \
  --autoplay-policy=no-user-gesture-required \
  --start-fullscreen
