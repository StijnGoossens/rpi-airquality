#!/bin/sh
# Bring Wi-Fi back if it dropped.
# No wlan0 at all: the chip's firmware download failed at boot ("Downloaded RAM image is
# corrupted"), so nmcli has nothing to connect. Reload the driver to retry the download.
# Unload whatever holds brcmfmac first (brcmfmac_wcc/_cyw/_bca, depending on the chip).
if ! ip link show wlan0 >/dev/null 2>&1; then
    holders=$(lsmod | awk '$1 == "brcmfmac" { gsub(",", " ", $4); print $4 }')
    echo "$(date): wlan0 missing, reloading brcmfmac (holders: ${holders:-none})"
    sudo modprobe -r $holders brcmfmac
    sudo modprobe brcmfmac
elif ! ip -4 addr show wlan0 | grep -q "inet "; then
    echo "$(date): wlan0 has no IP, reconnecting"
    sudo nmcli device connect wlan0
fi
