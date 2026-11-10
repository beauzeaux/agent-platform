#!/bin/bash
set -euo pipefail
action=${1:-help}
network=${ADO_PROXY_NETWORK:-ado-dev-proxy}
port=${ADO_PROXY_PORT:-5480}
chain="ADO_PROXY_$port"
[[ "$network" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]+$ && "$port" =~ ^[0-9]+$ && "$port" -gt 0 && "$port" -lt 65536 ]] ||
    { echo "Invalid network/port." >&2; exit 1; }
if [[ "$action" == preview ]]; then
    printf 'Would create/reuse labeled internal network %s.\nWould scope INPUT chain %s to its bridge gateway on TCP %s.\nWindows data listener remains loopback-only.\n' "$network" "$chain" "$port"
    exit 0
fi
[[ "$(id -u)" == 0 ]] || { echo "Run the WSL network setup as root." >&2; exit 1; }
if [[ "$action" == setup ]] && ! docker network inspect "$network" >/dev/null 2>&1; then
    docker network create --internal --driver bridge --label ado-dev-proxy.managed=true "$network" >/dev/null
fi
[[ "$action" =~ ^(setup|relay|status|check|remove)$ ]] ||
    { echo "Usage: container-network.sh preview|setup|relay|status|check|remove"; exit 1; }
owner=$(docker network inspect "$network" --format '{{index .Labels "ado-dev-proxy.managed"}}')
[[ "$owner" == true ]] || { echo "Refusing to modify an unowned network." >&2; exit 1; }
internal=$(docker network inspect "$network" --format '{{.Internal}}')
[[ "$internal" == true ]] || { echo "The agent network must be internal." >&2; exit 1; }
id=$(docker network inspect "$network" --format '{{.Id}}')
interface="br-${id:0:12}"
gateway=$(docker network inspect "$network" --format '{{(index .IPAM.Config 0).Gateway}}')
subnet=$(docker network inspect "$network" --format '{{(index .IPAM.Config 0).Subnet}}')
ip link show "$interface" >/dev/null ||
    { echo "This relay requires Docker Engine inside this WSL distro, not a remote/Desktop daemon." >&2; exit 1; }
case "$action" in
setup)
    if ! iptables -nL "$chain" >/dev/null 2>&1; then
        iptables -N "$chain"
        iptables -A "$chain" -i "$interface" -s "$subnet" -j ACCEPT
        iptables -A "$chain" -j REJECT
    else
        iptables -C "$chain" -i "$interface" -s "$subnet" -j ACCEPT ||
            { echo "Refusing an INPUT chain belonging to a different bridge." >&2; exit 1; }
    fi
    iptables -C INPUT -d "$gateway" -p tcp --dport "$port" -j "$chain" 2>/dev/null ||
        iptables -I INPUT 1 -d "$gateway" -p tcp --dport "$port" -j "$chain"
    ;;
relay)
    : "${ADO_PROXY_DOTNET:?Windows dotnet.exe path required}"
    : "${ADO_PROXY_APP:?Windows published DLL path required}"
    : "${ADO_PROXY_CONFIG:?Windows configuration path required}"
    iptables -C INPUT -d "$gateway" -p tcp --dport "$port" -j "$chain"
    helper="$(cd -- "$(dirname -- "$0")" && pwd)/relay-exec.sh"
    [[ "$helper" != *','* && "$helper" != *"'"* ]] || { echo "Unsupported helper path." >&2; exit 1; }
    exec socat "TCP-LISTEN:$port,bind=$gateway,reuseaddr,fork,max-children=64" "EXEC:sh '$helper',pipes"
    ;;
remove)
    [[ "$(docker network inspect "$network" --format '{{len .Containers}}')" == 0 ]] ||
        { echo "Stop/remove containers on the owned network before rollback." >&2; exit 1; }
    iptables -D INPUT -d "$gateway" -p tcp --dport "$port" -j "$chain"
    iptables -F "$chain"
    iptables -X "$chain"
    docker network rm "$network" >/dev/null
    exit 0
    ;;
check)
    iptables -C INPUT -d "$gateway" -p tcp --dport "$port" -j "$chain"
    iptables -C "$chain" -i "$interface" -s "$subnet" -j ACCEPT
    iptables -C "$chain" -j REJECT
    ss -ltnH "sport = :$port" | grep -Fq "$gateway:$port" ||
        { echo "Bridge-only relay is not listening." >&2; exit 1; }
    ;;
esac
printf 'Network: %s\nProxy: http://%s:%s\n' "$network" "$gateway" "$port"
