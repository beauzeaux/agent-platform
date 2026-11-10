#!/usr/bin/env bash
set -euo pipefail

die() { printf 'Error: %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Usage:
  sudo bash scripts/setup-wsl-wireguard.sh --prepare-only [--private-key FILE]
  sudo bash scripts/setup-wsl-wireguard.sh --address IPv4/32 \
    --hub-endpoint HOST:PORT --hub-public-key KEY \
    --ssh-user USER --ssh-key 'TYPE BASE64 [COMMENT]' [options]

Options:
  --prepare-only         Generate/import a durable identity and print its public key.
  --private-key FILE     Import a key; never replace a different existing identity.
  --address IPv4/32      This site's unique WireGuard address.
  --hub-endpoint HOST:PORT
  --hub-public-key KEY
  --allowed-ips CSV      Remote private networks (default: 10.82.0.0/24).
  --ssh-user USER        Existing non-root Ubuntu account allowed to log in.
  --ssh-key STRING       SSH client's full public-key line, quoted; not a file path.
  --ssh-source CIDR      Permitted SSH source network (default: 10.82.0.0/24).
  --no-ssh               Leave SSH configuration untouched.
  --gateway             Persistently enable IPv4 forwarding; no firewall/NAT changes.
  --help

Run from a local Ubuntu/WSL terminal. Requires systemd for connection persistence.
Managed files live in /etc/wireguard and /etc/ssh/sshd_config.d.
Existing unmanaged configuration is not overwritten. No Azure deployment is performed.
EOF
}

# Restrict this IPv4 bootstrap to canonical RFC1918 addresses and prefixes.
parse_cidr() {
    local value=$1 octet
    local -a octets
    [[ $value =~ ^([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)/([0-9]|[12][0-9]|3[0-2])$ ]] ||
        die "Invalid IPv4 CIDR: $value"
    CIDR_IP=${BASH_REMATCH[1]}
    CIDR_PREFIX=${BASH_REMATCH[2]}
    IFS=. read -r -a octets <<< "$CIDR_IP"
    for octet in "${octets[@]}"; do
        [[ $octet == 0 || $octet =~ ^[1-9][0-9]{0,2}$ ]] ||
            die "Noncanonical IPv4 address: $value"
        (( octet <= 255 )) || die "Invalid IPv4 address: $value"
    done
    CIDR_NUMBER=$(( (octets[0] << 24) | (octets[1] << 16) | (octets[2] << 8) | octets[3] ))
    if ! {
        (( octets[0] == 10 && CIDR_PREFIX >= 8 )) ||
        (( octets[0] == 172 && octets[1] >= 16 && octets[1] <= 31 && CIDR_PREFIX >= 12 )) ||
        (( octets[0] == 192 && octets[1] == 168 && CIDR_PREFIX >= 16 ))
    }; then
        die "CIDR must be entirely inside an RFC1918 private network: $value"
    fi
}

validate_network() {
    parse_cidr "$1"
    (( (CIDR_NUMBER & ((1 << (32 - CIDR_PREFIX)) - 1)) == 0 )) ||
        die "Network has host bits set: $1"
}

validate_allowed_ips() {
    local csv=$1 network
    local -a networks
    [[ -n $csv && $csv != ,* && $csv != *, && $csv != *,,* ]] ||
        die "AllowedIPs must be a nonempty comma-separated list"
    IFS=, read -r -a networks <<< "$csv"
    for network in "${networks[@]}"; do
        validate_network "$network"
    done
}

render_wireguard() {
    local private_key=$1 address=$2 public_key=$3 endpoint=$4 allowed=$5
    cat <<EOF
# Managed by setup-wsl-wireguard.sh
[Interface]
Address = $address
PrivateKey = $private_key

[Peer]
PublicKey = $public_key
Endpoint = $endpoint
AllowedIPs = $allowed
PersistentKeepalive = 25
EOF
}

read_private_key() {
    local value
    value=$(cat "$1")
    [[ $value =~ ^[A-Za-z0-9+/]{43}=$ ]] ||
        die "Private-key file must contain exactly one WireGuard key: $1"
    printf '%s\n' "$value" | wg pubkey >/dev/null
    printf '%s' "$value"
}

ensure_identity() {
    local key_file=$1 import_key=$2 candidate=$3 incoming_key
    [[ ! -L $key_file ]] || die "Refusing symlink: $key_file"
    if [[ -n $import_key ]]; then
        [[ -s $import_key ]] || die "Missing or empty import key"
        incoming_key=$(read_private_key "$import_key" | wg pubkey)
        if [[ -e $key_file ]]; then
            [[ $(read_private_key "$key_file" | wg pubkey) == "$incoming_key" ]] ||
                die "Imported key differs from durable identity; explicit rotation is required"
        else
            install -m 600 "$import_key" "$key_file"
        fi
    elif [[ ! -e $key_file ]]; then
        wg genkey > "$candidate"
        install -m 600 "$candidate" "$key_file"
    fi
    read_private_key "$key_file" >/dev/null
}

render_ssh() {
    local address=$1 user=$2 source=$3
    cat <<EOF
# Managed by setup-wsl-wireguard.sh
AddressFamily inet
ListenAddress ${address%/*}
Port 22
PermitRootLogin no
PubkeyAuthentication yes
AuthenticationMethods publickey
PasswordAuthentication no
KbdInteractiveAuthentication no
AuthorizedKeysFile .ssh/authorized_keys
AuthorizedKeysCommand none
TrustedUserCAKeys none
AllowUsers $user@$source
EOF
}

validate_ssh_key() {
    local key_line=$1
    [[ $key_line != *$'\n'* && $key_line != *$'\r'* &&
       $key_line =~ ^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521)|sk-ssh-ed25519@openssh.com|sk-ecdsa-sha2-nistp256@openssh.com)\ [A-Za-z0-9+/]+=*($|\ ) ]] ||
        die "--ssh-key must contain exactly one plain OpenSSH public-key line, not a file path"
}

require_managed() {
    local path=$1
    if [[ -e $path ]]; then
        [[ ! -L $path ]] || die "Refusing symlink: $path"
        grep -qxF '# Managed by setup-wsl-wireguard.sh' "$path" ||
            die "Unmanaged file exists: $path. Back it up and migrate it explicitly."
    fi
}

main() {
    local prepare=false gateway=false ssh=true address='' endpoint='' hub_key=''
    local allowed=10.82.0.0/24 ssh_source=10.82.0.0/24 ssh_user='' ssh_key='' import_key=''
    local key_file=/etc/wireguard/site-private.key config=/etc/wireguard/wg0.conf
    local ssh_config=/etc/ssh/sshd_config.d/00-wireguard-only.conf
    local forward_config=/etc/sysctl.d/90-wireguard-site-forwarding.conf
    local ssh_dependency=/etc/systemd/system/ssh.service.d/wireguard.conf
    local home uid gid private_key public_key candidate effective
    local ssh_backup='' key_line current_listeners
    while (( $# )); do
        case $1 in
            --help) usage; return ;;
            --prepare-only) prepare=true; shift ;;
            --gateway) gateway=true; shift ;;
            --no-ssh) ssh=false; shift ;;
            --private-key|--address|--hub-endpoint|--hub-public-key|--allowed-ips|--ssh-user|--ssh-key|--ssh-source)
                (( $# >= 2 )) || die "Missing value for $1"
                case $1 in
                    --private-key) import_key=$2 ;;
                    --address) address=$2 ;;
                    --hub-endpoint) endpoint=$2 ;;
                    --hub-public-key) hub_key=$2 ;;
                    --allowed-ips) allowed=$2 ;;
                    --ssh-user) ssh_user=$2 ;;
                    --ssh-key) ssh_key=$2 ;;
                    --ssh-source) ssh_source=$2 ;;
                esac
                shift 2 ;;
            *) die "Unknown option: $1" ;;
        esac
    done
    (( EUID == 0 )) || die "Run with sudo bash"
    [[ -r /etc/os-release ]] || die "This script requires Ubuntu"
    # shellcheck disable=SC1091
    source /etc/os-release
    [[ $ID == ubuntu ]] || die "This script supports Ubuntu only"

    if ! $prepare; then
        parse_cidr "$address"
        [[ $CIDR_PREFIX == 32 ]] || die "--address must be a single-host /32"
        validate_allowed_ips "$allowed"
        [[ $endpoint =~ ^[a-zA-Z0-9.-]+:([0-9]{1,5})$ ]] ||
            die "--hub-endpoint must be HOST:PORT"
        (( 10#${BASH_REMATCH[1]} >= 1 && 10#${BASH_REMATCH[1]} <= 65535 )) ||
            die "Invalid endpoint port"
        [[ $hub_key =~ ^[A-Za-z0-9+/]{43}=$ ]] || die "Invalid hub public-key format"
        [[ $(ps -p 1 -o comm=) == systemd ]] ||
            die "Enable systemd in /etc/wsl.conf ([boot] systemd=true), then restart WSL"
        require_managed "$config"
        if ip link show dev wg0 >/dev/null 2>&1 && ! systemctl is-active --quiet wg-quick@wg0; then
            die "wg0 is manually managed. Stop it locally before migrating to this script."
        fi
        if $ssh; then
            [[ $ssh_user =~ ^[a-z_][a-z0-9_-]*\$?$ && $ssh_user != root ]] ||
                die "--ssh-user must be an existing non-root account"
            id "$ssh_user" >/dev/null || die "SSH account does not exist"
            validate_network "$ssh_source"
            validate_ssh_key "$ssh_key"
            require_managed "$ssh_config"
            require_managed "$ssh_dependency"
        fi
        if $gateway; then require_managed "$forward_config"; fi
    fi

    local -a packages=(wireguard-tools iproute2)
    if ! $prepare && $ssh; then packages+=(openssh-server); fi
    if ! command -v wg >/dev/null || ! command -v ip >/dev/null ||
        { ! $prepare && $ssh && ! command -v sshd >/dev/null; }; then
        local installing_ssh=false
        if ! $prepare && $ssh && ! command -v sshd >/dev/null; then
            # Prevent package installation from starting an unrestricted listener.
            [[ ! -L /etc/systemd/system/ssh.service && ! -L /etc/systemd/system/ssh.socket &&
               ! -L /run/systemd/system/ssh.service && ! -L /run/systemd/system/ssh.socket ]] ||
                die "Existing SSH unit links need review before installing openssh-server"
            systemctl mask --runtime ssh.service ssh.socket
            installing_ssh=true
        fi
        apt-get update
        if ! apt-get install -y "${packages[@]}"; then
            die "Package installation failed; newly installed SSH units may remain runtime-masked"
        fi
        if $installing_ssh; then systemctl unmask --runtime ssh.service ssh.socket; fi
    fi

    umask 077
    install -d -o root -g root -m 700 /etc/wireguard
    candidate=$(mktemp /etc/wireguard/.site-setup.XXXXXX)
    # The scratch file may contain a private key; always remove it.
    trap 'rm -f -- "$candidate"' EXIT
    ensure_identity "$key_file" "$import_key" "$candidate"
    private_key=$(read_private_key "$key_file")
    public_key=$(printf '%s\n' "$private_key" | wg pubkey)
    chmod 600 "$key_file"
    chown root:root "$key_file"
    printf 'Site public key: %s\n' "$public_key"
    if $prepare; then
        printf 'Register this public key and a unique overlay address in the hub inventory, then redeploy.\n'
        rm -f -- "$candidate"
        trap - EXIT
        return
    fi

    if $ssh; then
        key_line=$ssh_key
        printf '%s\n' "$key_line" > "$candidate"
        ssh-keygen -l -f "$candidate" >/dev/null
        home=$(getent passwd "$ssh_user" | cut -d: -f6)
        uid=$(id -u "$ssh_user")
        gid=$(id -g "$ssh_user")
        [[ -d $home && ! -L $home/.ssh && ! -L $home/.ssh/authorized_keys ]] ||
            die "SSH home must exist and .ssh/authorized_keys must not be symlinks"
        install -d -m 755 /etc/ssh/sshd_config.d
        if [[ -e $ssh_config ]]; then ssh_backup=$(cat "$ssh_config"); fi
        render_ssh "$address" "$ssh_user" "$ssh_source" > "$candidate"
        install -o root -g root -m 644 "$candidate" "$ssh_config"
        install -d -m 755 /run/sshd
        # Inspect the effective configuration, including competing files and Match blocks.
        if ! /usr/sbin/sshd -t ||
            ! effective=$(/usr/sbin/sshd -T -C "user=$ssh_user,addr=${ssh_source%/*},host=wireguard-client"); then
            if [[ -n $ssh_backup ]]; then printf '%s\n' "$ssh_backup" > "$ssh_config"; else rm -f "$ssh_config"; fi
            die "SSH validation failed; previous managed configuration restored"
        fi
        current_listeners=$(grep '^listenaddress ' <<< "$effective")
        if [[ $current_listeners != "listenaddress ${address%/*}:22" ]] ||
            ! grep -qxF 'authenticationmethods publickey' <<< "$effective" ||
            ! grep -qxF 'pubkeyauthentication yes' <<< "$effective" ||
            ! grep -qxF 'passwordauthentication no' <<< "$effective" ||
            ! grep -qxF 'kbdinteractiveauthentication no' <<< "$effective" ||
            ! grep -qxF 'authorizedkeysfile .ssh/authorized_keys' <<< "$effective" ||
            ! grep -qxF 'authorizedkeyscommand none' <<< "$effective" ||
            ! grep -qxF 'trustedusercakeys none' <<< "$effective" ||
            ! grep -qxF 'permitrootlogin no' <<< "$effective" ||
            ! grep -qxF "allowusers $ssh_user@$ssh_source" <<< "$effective"; then
            if [[ -n $ssh_backup ]]; then printf '%s\n' "$ssh_backup" > "$ssh_config"; else rm -f "$ssh_config"; fi
            die "Competing SSH settings detected; fix other ListenAddress/authentication/Match entries and retry"
        fi
        install -d -o "$uid" -g "$gid" -m 700 "$home/.ssh"
        touch "$home/.ssh/authorized_keys"
        if ! grep -qxF "$key_line" "$home/.ssh/authorized_keys"; then
            printf '\n%s\n' "$key_line" >> "$home/.ssh/authorized_keys"
        fi
        chown "$uid:$gid" "$home/.ssh/authorized_keys"
        chmod 600 "$home/.ssh/authorized_keys"
    fi

    render_wireguard "$private_key" "$address" "$hub_key" "$endpoint" "$allowed" > "$candidate"
    install -o root -g root -m 600 "$candidate" "$config"
    unset private_key
    if $gateway; then
        printf '# Managed by setup-wsl-wireguard.sh\nnet.ipv4.ip_forward=1\n' > "$forward_config"
        sysctl -p "$forward_config"
    fi
    systemctl enable wg-quick@wg0.service
    systemctl restart wg-quick@wg0.service
    systemctl is-active --quiet wg-quick@wg0.service || die "WireGuard service is not active"

    if $ssh; then
        install -d -m 755 /etc/systemd/system/ssh.service.d
        cat > "$ssh_dependency" <<'EOF'
# Managed by setup-wsl-wireguard.sh
[Unit]
Requires=wg-quick@wg0.service
After=wg-quick@wg0.service
EOF
        systemctl disable --now ssh.socket
        systemctl daemon-reload
        systemctl enable ssh.service
        systemctl restart ssh.service
        systemctl is-active --quiet ssh.service || die "SSH service is not active"
        current_listeners=$(ss -H -lnt 'sport = :22' | awk '{print $4}')
        [[ $current_listeners == "${address%/*}:22" ]] ||
            die "Unexpected port-22 listener(s): $current_listeners"
        ss -lntp 'sport = :22'
    fi
    wg show wg0
    ip -4 address show dev wg0
    ip -4 route show dev wg0
    printf 'Configuration applied. Test a remote peer; an active interface alone does not prove connectivity.\n'
    rm -f -- "$candidate"
    trap - EXIT
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then main "$@"; fi
