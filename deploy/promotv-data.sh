#!/bin/sh
# PromoTV: keep /var/lib/promotv (videos + settings) on the SD card when the rest of the
# system is read-only (overlay file system, "raspi-config -> Overlay File System").
#
# With the overlay, everything written to / goes to RAM and is lost on reboot. Here we
# make the real (lower) root file system writable again and bind-mount only our data
# folder from it, so uploads and settings are kept while the system files stay protected.
set -eu
DATA=/var/lib/promotv

if [ "$(findmnt -n -o FSTYPE /)" != "overlay" ]; then
  exit 0   # protection off: nothing to do
fi
if mountpoint -q "$DATA"; then
  exit 0
fi

ROOTDEV=$(sed -n 's/.* root=\([^ ]*\).*/\1/p' /proc/cmdline)
case "$ROOTDEV" in
  PARTUUID=*|UUID=*|LABEL=*) ROOTDEV=$(blkid -t "$ROOTDEV" -o device | head -n1) ;;
esac
if [ -z "$ROOTDEV" ] || [ ! -b "$ROOTDEV" ]; then
  echo "promotv-data: cannot find the root partition" >&2
  exit 1
fi

LOWER=$(findmnt -n -o TARGET -S "$ROOTDEV" | head -n1 || true)
if [ -n "$LOWER" ] && [ "$LOWER" != "/" ]; then
  mount -o remount,rw "$LOWER"
else
  LOWER=/run/promotv-lower
  mkdir -p "$LOWER"
  mount -o rw "$ROOTDEV" "$LOWER"
fi

mkdir -p "$LOWER$DATA"
mount --bind "$LOWER$DATA" "$DATA"
echo "promotv-data: $DATA is persistent ($ROOTDEV via $LOWER)"
