#!/usr/bin/env bash
# PromoTV installer for Raspberry Pi OS Lite (Bookworm or newer).
# Usage:  sudo ./install.sh
# Optional: sudo WIFI_SSID="Моят TV" WIFI_PASSWORD="parola123" ./install.sh
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR=/opt/promotv
DATA_DIR=/var/lib/promotv
CON=promotv-ap
AP_IP=192.168.4.1
WIFI_SSID="${WIFI_SSID:-PromoTV}"
WIFI_COUNTRY="${WIFI_COUNTRY:-BG}"
TIMEZONE="${TIMEZONE:-Europe/Sofia}"

say() { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }

if [[ $EUID -ne 0 ]]; then
  echo "Стартирайте с sudo:  sudo ./install.sh" >&2
  exit 1
fi
if ! command -v nmcli >/dev/null; then
  echo "NetworkManager (nmcli) липсва. Нужен е Raspberry Pi OS Bookworm или по-нов." >&2
  exit 1
fi

say "Инсталиране на пакети (може да отнеме няколко минути)"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  mpv ffmpeg cec-utils python3 python3-flask python3-qrcode \
  fonts-dejavu-core dnsmasq-base network-manager fake-hwclock

say "Копиране на програмата в $APP_DIR"
rm -rf "$APP_DIR/promotv"
mkdir -p "$APP_DIR" "$DATA_DIR"/{media,thumbs,tmp} /etc/promotv
cp -r "$SRC/promotv" "$APP_DIR/"
find "$APP_DIR" -name '__pycache__' -prune -exec rm -rf {} +
[[ -f /etc/promotv/mpv.conf ]] || cp "$SRC/deploy/mpv.conf" /etc/promotv/mpv.conf
cp "$SRC/deploy/promotv.service" /etc/systemd/system/promotv.service

say "Часова зона: $TIMEZONE"
timedatectl set-timezone "$TIMEZONE" || true

say "Настройки за HDMI и видео"
BOOT=/boot/firmware
[[ -d $BOOT ]] || BOOT=/boot
CFG="$BOOT/config.txt"
CMD="$BOOT/cmdline.txt"
if [[ -f $CFG ]]; then
  cp -n "$CFG" "$CFG.promotv-backup" || true
  if ! grep -q "# PromoTV" "$CFG"; then
    cat >>"$CFG" <<'EOF'

# PromoTV
[all]
# Keep HDMI on even if the TV is off while the Pi boots.
hdmi_force_hotplug=1
# Memory for the hardware video decoder.
gpu_mem=128
EOF
  fi
fi
if [[ -f $CMD ]]; then
  cp -n "$CMD" "$CMD.promotv-backup" || true
  line="$(head -n1 "$CMD")"
  for opt in consoleblank=0 logo.nologo vt.global_cursor_default=0; do
    [[ " $line " == *" $opt "* ]] || line="$line $opt"
  done
  # Force 1080p on HDMI even when the TV is in standby at boot (D = force enable).
  [[ " $line " == *" video="* ]] || line="$line video=HDMI-A-1:1920x1080@60D"
  printf '%s\n' "$line" >"$CMD"
fi

say "Wi-Fi точка за достъп"
if command -v raspi-config >/dev/null; then
  raspi-config nonint do_wifi_country "$WIFI_COUNTRY" || true
fi
rfkill unblock wifi 2>/dev/null || true

if nmcli -t -f NAME connection show | grep -qx "$CON"; then
  echo "Мрежата $CON вече съществува – запазвам името и паролата ѝ."
  if [[ -n "${WIFI_PASSWORD:-}" ]]; then
    nmcli connection modify "$CON" 802-11-wireless.ssid "$WIFI_SSID" 802-11-wireless-security.psk "$WIFI_PASSWORD"
  fi
else
  if [[ -z "${WIFI_PASSWORD:-}" ]]; then
    # 10 characters without look-alike letters (0/O, 1/l/I)
    WIFI_PASSWORD="$(tr -dc 'abcdefghjkmnpqrstuvwxyz23456789' </dev/urandom | head -c 10)"
  fi
  nmcli connection add type wifi ifname wlan0 con-name "$CON" autoconnect yes \
    connection.autoconnect-priority 100 \
    ssid "$WIFI_SSID" \
    802-11-wireless.mode ap 802-11-wireless.band bg 802-11-wireless.channel 6 \
    802-11-wireless-security.key-mgmt wpa-psk 802-11-wireless-security.psk "$WIFI_PASSWORD" \
    802-11-wireless-security.proto rsn 802-11-wireless-security.pairwise ccmp \
    802-11-wireless-security.group ccmp 802-11-wireless-security.pmf disable \
    ipv4.method shared ipv4.addresses "$AP_IP/24" ipv6.method disabled
fi

# Captive portal: every DNS name points to the Pi, so phones open the panel by themselves.
mkdir -p /etc/NetworkManager/dnsmasq-shared.d
cat >/etc/NetworkManager/dnsmasq-shared.d/promotv.conf <<EOF
address=/#/$AP_IP
dhcp-option=114,http://$AP_IP/
EOF

# The Pi has only one Wi-Fi chip: stop auto-joining other Wi-Fi networks so the
# access point always comes up after a reboot.
while IFS=: read -r name type; do
  if [[ $type == "802-11-wireless" && $name != "$CON" ]]; then
    echo "Изключвам автоматичното свързване към Wi-Fi „$name“"
    nmcli connection modify "$name" connection.autoconnect no || true
  fi
done < <(nmcli -t -f NAME,TYPE connection show)

say "Стартиране на услугата"
systemctl daemon-reload
systemctl enable promotv.service
# Free the HDMI console so mpv can use the screen.
systemctl disable --now getty@tty1.service 2>/dev/null || true

PSK="$(nmcli -s -g 802-11-wireless-security.psk connection show "$CON")"
SSID="$(nmcli -g 802-11-wireless.ssid connection show "$CON")"

cat <<EOF

=====================================================================
 Готово!
 След рестарт Raspberry Pi ще създаде Wi-Fi мрежа:

   Мрежа:   $SSID
   Парола:  $PSK
   Панел:   http://$AP_IP

 Паролата се вижда и на телевизора: OK на дистанционното ->
 "Wi-Fi и уеб панел".

 ВНИМАНИЕ: ако сте свързани по Wi-Fi през SSH, след рестарта
 връзката ще прекъсне – Wi-Fi чипът става точка за достъп.

 Рестартирайте сега с:  sudo reboot
=====================================================================
EOF
