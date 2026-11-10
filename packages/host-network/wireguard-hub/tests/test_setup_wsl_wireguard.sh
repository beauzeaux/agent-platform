#!/usr/bin/env bash
set -euo pipefail
umask 077

source "$(dirname "${BASH_SOURCE[0]}")/../scripts/setup-wsl-wireguard.sh"

assert_rejected() {
    if ("$@") >/dev/null 2>&1; then
        printf 'Expected rejection: %s\n' "$*" >&2
        exit 1
    fi
}

validate_allowed_ips '10.82.0.0/24,172.30.0.0/24,172.30.2.0/24'
validate_network '172.16.0.0/12'
validate_network '192.168.0.0/16'
validate_ssh_key 'ssh-ed25519 AAAA main-host'
validate_ssh_key 'ssh-ed25519 AAAA'
for key_input in '' '/path/to/id_ed25519.pub' 'AAAA' \
    '-----BEGIN OPENSSH PRIVATE KEY-----' \
    'command="unwanted" ssh-ed25519 AAAA' \
    $'ssh-ed25519 AAAA\nssh-ed25519 BBBB' \
    $'ssh-ed25519 AAAA\r'; do
    assert_rejected validate_ssh_key "$key_input"
done
parse_cidr '10.82.0.3/32'
[[ $CIDR_IP == 10.82.0.3 && $CIDR_PREFIX == 32 ]]
for network in '0.0.0.0/0' '8.8.8.0/24' '172.32.0.0/24' '10.0.0.0/7' \
    '10.82.0.3/24' '10.82.0.256/32' '10.082.0.0/24' '::/0' \
    '10.82.0.0/24,' ',10.82.0.0/24' '10.82.0.0/24,,172.30.0.0/24' \
    '10.82.0.0/24;touch /tmp/invalid' ''; do
    assert_rejected validate_allowed_ips "$network"
done

config=$(render_wireguard 'test-private-key' '10.82.0.3/32' 'test-public-key' \
    'hub.example:51820' '10.82.0.0/24,172.30.2.0/24')
grep -qxF 'Address = 10.82.0.3/32' <<< "$config"
grep -qxF 'AllowedIPs = 10.82.0.0/24,172.30.2.0/24' <<< "$config"
grep -qxF 'PersistentKeepalive = 25' <<< "$config"
if grep -qE '10\.81\.|SaveConfig|PostUp|MASQUERADE' <<< "$config"; then
    echo 'Unexpected Azure route, runtime save, or forwarding rule' >&2
    exit 1
fi

config=$(render_ssh '10.82.0.3/32' 'alice' '10.82.0.0/24')
for line in 'ListenAddress 10.82.0.3' 'AllowUsers alice@10.82.0.0/24' \
    'AuthenticationMethods publickey' 'PasswordAuthentication no' \
    'KbdInteractiveAuthentication no' 'AuthorizedKeysFile .ssh/authorized_keys'; do
    grep -qxF "$line" <<< "$config"
done

scratch=$(mktemp)
scratch_dir=$(mktemp -d)
trap 'rm -f -- "$scratch" "$scratch_dir/host" "$scratch_dir/host.pub" "$scratch_dir/sshd.conf" "$scratch_dir/identity" "$scratch_dir/restored"; rmdir "$scratch_dir"' EXIT
printf 'unmanaged\n' > "$scratch"
assert_rejected require_managed "$scratch"
printf '# Managed by setup-wsl-wireguard.sh\n' > "$scratch"
require_managed "$scratch"
bash "$(dirname "${BASH_SOURCE[0]}")/../scripts/setup-wsl-wireguard.sh" --help >/dev/null

if command -v wg >/dev/null; then
    key=$(wg genkey)
    printf '%s\n' "$key" > "$scratch"
    key=$(read_private_key "$scratch")
    [[ ${#key} == 44 ]]
    printf '\nPostUp = unwanted-command\n' >> "$scratch"
    assert_rejected read_private_key "$scratch"
    ensure_identity "$scratch_dir/identity" '' "$scratch"
    original_key=$(read_private_key "$scratch_dir/identity")
    ensure_identity "$scratch_dir/identity" '' "$scratch"
    [[ $(read_private_key "$scratch_dir/identity") == "$original_key" ]]
    ensure_identity "$scratch_dir/identity" "$scratch_dir/identity" "$scratch"
    ensure_identity "$scratch_dir/restored" "$scratch_dir/identity" "$scratch"
    [[ $(read_private_key "$scratch_dir/restored") == "$original_key" ]]
    other_key=$(wg genkey)
    printf '%s\n' "$other_key" > "$scratch"
    assert_rejected ensure_identity "$scratch_dir/identity" "$scratch" "$scratch"
    [[ $(read_private_key "$scratch_dir/identity") == "$original_key" ]]
else
    echo 'Skipping real key validation: wg is not installed'
fi

if command -v sshd >/dev/null && command -v ssh-keygen >/dev/null; then
    ssh-keygen -q -t ed25519 -N '' -f "$scratch_dir/host"
    key_line=$(cat "$scratch_dir/host.pub")
    validate_ssh_key "$key_line"
    printf '%s\n' "$key_line" > "$scratch"
    ssh-keygen -l -f "$scratch" >/dev/null
    {
        printf 'HostKey %s/host\n' "$scratch_dir"
        render_ssh '10.82.0.3/32' 'alice' '10.82.0.0/24'
    } > "$scratch_dir/sshd.conf"
    effective=$(sshd -T -f "$scratch_dir/sshd.conf" -C user=alice,addr=10.82.0.2,host=test)
    for line in 'listenaddress 10.82.0.3:22' 'allowusers alice@10.82.0.0/24' \
        'authenticationmethods publickey' 'pubkeyauthentication yes' \
        'passwordauthentication no' 'kbdinteractiveauthentication no' \
        'authorizedkeysfile .ssh/authorized_keys' 'authorizedkeyscommand none' \
        'trustedusercakeys none' 'permitrootlogin no'; do
        grep -qxF "$line" <<< "$effective"
    done
else
    echo 'Skipping effective OpenSSH validation: sshd/ssh-keygen are not installed'
fi
echo 'WSL bootstrap tests passed'
