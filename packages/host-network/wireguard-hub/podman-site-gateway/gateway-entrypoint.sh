#!/bin/sh
set -eu

case "${HUB_ENDPOINT:-}${HUB_PUBLIC_KEY:-}${REMOTE_PREFIXES:-}${WIREGUARD_ADDRESS:-}" in
    ''|*[!a-zA-Z0-9+=.,:/_-]*)
        echo "WireGuard configuration is missing or contains unsupported characters" >&2
        exit 1
        ;;
esac

private_key=/run/secrets/wg-private-key
test -s "$private_key"

ip link add wg0 type wireguard
ip address add "$WIREGUARD_ADDRESS" dev wg0
wg set wg0 \
    private-key "$private_key" \
    peer "$HUB_PUBLIC_KEY" \
    endpoint "$HUB_ENDPOINT" \
    allowed-ips "$REMOTE_PREFIXES" \
    persistent-keepalive 25
ip link set wg0 up

old_ifs="$IFS"
IFS=','
for prefix in $REMOTE_PREFIXES; do
    ip route replace "$prefix" dev wg0
done
IFS="$old_ifs"

nft -f - <<'EOF'
table inet podman_wg_gateway {
    chain forward {
        type filter hook forward priority filter; policy drop;
        ct state established,related accept
        iifname "eth0" oifname "wg0" accept
        iifname "wg0" oifname "eth0" accept
    }
}
EOF

cleanup() {
    nft delete table inet podman_wg_gateway 2>/dev/null || true
    ip link delete wg0 2>/dev/null || true
}

trap cleanup EXIT INT TERM
while :; do
    sleep 3600 &
    wait $!
done
