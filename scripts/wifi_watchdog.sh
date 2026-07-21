#!/bin/sh
# Reconnect wlan0 if it has dropped its IPv4 address.
if ! ip -4 addr show wlan0 | grep -q "inet "; then
    echo "$(date): wlan0 has no IP, reconnecting"
    sudo nmcli device connect wlan0
fi
