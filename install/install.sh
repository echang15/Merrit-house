#!/usr/bin/env bash
# One-shot installer for Raspberry Pi OS (Bullseye or Bookworm, 32- or 64-bit).
#
#   git clone https://github.com/echang15/merrit-house.git ~/merrit-house
#   cd ~/merrit-house && ./install/install.sh
#
# What it does:
#   1. installs python3-flask, python3-requests and chromium via apt
#   2. installs + starts a systemd service running the web app on port 8080
#   3. registers a Chromium kiosk that opens the board when the desktop logs in
#   4. turns off screen blanking so the board stays on
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="${SUDO_USER:-$USER}"
RUN_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)"

echo "==> Installing packages"
sudo apt-get update -qq
# avahi-daemon answers for <hostname>.local so phones can find the Pi by name.
sudo apt-get install -y -qq python3-flask python3-requests curl avahi-daemon \
  $(apt-cache show chromium >/dev/null 2>&1 && echo chromium || echo chromium-browser)

echo "==> Installing systemd service (user: $RUN_USER, dir: $APP_DIR)"
mkdir -p "$APP_DIR/data"
sed -e "s|__APP_DIR__|$APP_DIR|g" -e "s|__USER__|$RUN_USER|g" \
  "$APP_DIR/install/taplist.service" | sudo tee /etc/systemd/system/taplist.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now taplist.service

echo "==> Registering kiosk autostart"
chmod +x "$APP_DIR/install/kiosk.sh"
mkdir -p "$RUN_HOME/.config/autostart"
sed -e "s|__APP_DIR__|$APP_DIR|g" "$APP_DIR/install/taplist-kiosk.desktop" \
  > "$RUN_HOME/.config/autostart/taplist-kiosk.desktop"
# labwc (Pi OS Bookworm 2024+) only reads its own autostart file.
if command -v labwc >/dev/null 2>&1 || [ -d "$RUN_HOME/.config/labwc" ]; then
  mkdir -p "$RUN_HOME/.config/labwc"
  grep -q "install/kiosk.sh" "$RUN_HOME/.config/labwc/autostart" 2>/dev/null || \
    echo "$APP_DIR/install/kiosk.sh &" >> "$RUN_HOME/.config/labwc/autostart"
fi
# wayfire (Pi OS Bookworm 2023) reads [autostart] in wayfire.ini.
if [ -f "$RUN_HOME/.config/wayfire.ini" ] && ! grep -q "install/kiosk.sh" "$RUN_HOME/.config/wayfire.ini"; then
  if grep -q '^\[autostart\]' "$RUN_HOME/.config/wayfire.ini"; then
    sed -i "/^\[autostart\]/a taplist = $APP_DIR/install/kiosk.sh" "$RUN_HOME/.config/wayfire.ini"
  else
    printf '\n[autostart]\ntaplist = %s/install/kiosk.sh\n' "$APP_DIR" >> "$RUN_HOME/.config/wayfire.ini"
  fi
fi

if command -v raspi-config >/dev/null 2>&1; then
  echo "==> Disabling screen blanking"
  sudo raspi-config nonint do_blanking 1 || true
fi

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
NAME="$(hostname -s 2>/dev/null || hostname)"
echo
echo "Done. The board is running at:"
echo "  http://${IP:-localhost}:8080/"
echo "  http://${NAME}.local:8080/"
echo "Open either address in a browser on any phone or laptop on your Wi-Fi to"
echo "manage the taps. The same addresses are shown on the kiosk's splash screen."
echo "Reboot to start the kiosk:"
echo "  sudo reboot"
