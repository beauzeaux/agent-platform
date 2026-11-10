#!/usr/bin/env bash
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
mode=${1:?mode required}
device=${2:?device required}
uuid=${3:?UUID required}
expected_bytes=${4:?size required}
target=/mnt/microk8s-hostpath
[[ $(id -u) == 0 ]]
[[ "$uuid" =~ ^[0-9a-f-]{36}$ ]]
[[ "$expected_bytes" == 805306368000 ]]
unit=$(systemd-escape --path --suffix=mount "$target")

verify_mount() {
  mountpoint -q "$target"
  [[ $(findmnt -rn -M "$target" -o UUID) == "$uuid" ]]
  [[ $(findmnt -rn -M "$target" -o FSTYPE) == ext4 ]]
  findmnt -M "$target" -o SOURCE,TARGET,FSTYPE,OPTIONS
  df -hT "$target"
}

case "$mode" in
  initialize)
    [[ "$device" =~ ^/dev/sd[a-z]+$ ]]
    [[ -b "$device" ]]
    [[ $(blockdev --getsize64 "$device") == "$expected_bytes" ]]
    if ! mountpoint -q "$target" && [[ -d "$target" ]] &&
      [[ -n $(find "$target" -mindepth 1 -maxdepth 1 -print -quit) ]]; then
      echo "Mount directory is not empty; refusing to hide existing files." >&2
      exit 1
    fi
    current_uuid=$(blkid -s UUID -o value "$device" || true)
    if [[ "$current_uuid" != "$uuid" ]]; then
      [[ -z "$current_uuid" ]]
      lsblk --json --paths --output NAME,TYPE,FSTYPE,MOUNTPOINTS "$device" |
        python3 -c 'import json,sys; d=json.load(sys.stdin)["blockdevices"]; assert len(d)==1; d=d[0]; assert d["type"]=="disk" and not d.get("children") and not d.get("fstype") and not any(d.get("mountpoints") or []), "Device is not an unused blank disk"'
      [[ -z $(wipefs --noheadings --output TYPE "$device") ]]
      mkfs.ext4 -U "$uuid" -L mk8s-hostpath -m 1 "$device"
    fi
    [[ $(blkid -s TYPE -o value "$device") == ext4 ]]
    if mountpoint -q "$target"; then
      verify_mount
    fi
    install -d -m 0755 "$target"
    [[ ! -e /etc/systemd/system/"$unit" ]] ||
      grep -q "^What=/dev/disk/by-uuid/$uuid$" /etc/systemd/system/"$unit"
    cat > /etc/systemd/system/"$unit" <<EOF
[Unit]
Description=MicroK8s dedicated ext4 hostpath disk
JobTimeoutSec=45

[Mount]
What=/dev/disk/by-uuid/$uuid
Where=$target
Type=ext4
Options=defaults,noatime
TimeoutSec=30

[Install]
WantedBy=multi-user.target
EOF
    for service in snap.microk8s.daemon-containerd.service snap.microk8s.daemon-kubelite.service; do
      install -d -m 0755 /etc/systemd/system/"$service".d
      cat > /etc/systemd/system/"$service".d/hostpath-disk.conf <<EOF
[Unit]
RequiresMountsFor=$target
BindsTo=$unit
After=$unit
EOF
    done
    systemctl daemon-reload
    systemctl enable "$unit"
    systemctl start "$unit"
    verify_mount
    systemctl start snap.microk8s.daemon-containerd.service snap.microk8s.daemon-kubelite.service
    ;;
  attach)
    [[ $(blkid -U "$uuid") == "$device" ]]
    if ! mountpoint -q "$target"; then
      systemctl reset-failed "$unit" snap.microk8s.daemon-containerd.service snap.microk8s.daemon-kubelite.service
      systemctl start "$unit"
      verify_mount
      systemctl start snap.microk8s.daemon-containerd.service snap.microk8s.daemon-kubelite.service
    else
      verify_mount
    fi
    ;;
  verify)
    verify_mount
    systemctl is-active "$unit"
    systemctl is-active snap.microk8s.daemon-containerd.service snap.microk8s.daemon-kubelite.service
    ;;
  *)
    echo "Unknown mode: $mode" >&2
    exit 1
    ;;
esac
