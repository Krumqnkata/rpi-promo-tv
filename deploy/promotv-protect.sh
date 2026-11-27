#!/bin/sh
# PromoTV: turn the SD card protection (read-only system) on or off.
#   sudo promotv-protect status
#   sudo promotv-protect off   # before updating or changing system settings
#   sudo promotv-protect on
set -eu

status() {
  if [ "$(findmnt -n -o FSTYPE /)" = "overlay" ]; then now="ВКЛЮЧЕНА"; else now="изключена"; fi
  if grep -q "overlayroot=" /boot/firmware/cmdline.txt 2>/dev/null || grep -q "overlayroot=" /boot/cmdline.txt 2>/dev/null; then
    next="включена"; else next="изключена"; fi
  echo "Защита на SD картата: сега $now, след рестарт: $next"
}

case "${1:-status}" in
  status) status ;;
  on|off)
    [ "$(id -u)" -eq 0 ] || { echo "Стартирайте със sudo" >&2; exit 1; }
    [ "$1" = on ] && arg=0 || arg=1
    echo "Променям настройката (може да отнеме 1-2 минути)..."
    raspi-config nonint do_overlayfs "$arg"
    if [ "$1" = off ]; then
      # do_overlayfs may leave the boot partition read-only; unlock it too,
      # otherwise updates cannot change config.txt / cmdline.txt.
      raspi-config nonint disable_bootro 2>/dev/null || true
    fi
    echo "Рестартиране..."
    systemctl reboot
    ;;
  *) echo "Използване: promotv-protect status|on|off" >&2; exit 1 ;;
esac
