#!/usr/bin/env bash
set -euo pipefail
disk_unit='mnt-microk8s\x2dhostpath.mount'
source=/mnt/microk8s-hostpath/kagent-lab/ate
destination=/var/lib/ate
nsenter --target 1 --mount -- findmnt -rn -M /mnt/microk8s-hostpath -t ext4 >/dev/null
if [ -d "$destination" ] && ! nsenter --target 1 --mount -- mountpoint -q "$destination"; then
    if [ -n "$(find "$destination" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
        printf 'Refusing to hide existing worker data in %s\n' "$destination" >&2
        exit 1
    fi
fi
nsenter --target 1 --mount -- install -d -m 700 "$source"
install -d -m 755 "$destination"
python3 - <<'PY'
from pathlib import Path

disk = r"mnt-microk8s\x2dhostpath.mount"
Path("/etc/systemd/system/var-lib-ate.mount").write_text(f"""[Unit]
Description=Substrate worker data on native MicroK8s storage
Requires={disk}
After={disk}
BindsTo={disk}
Before=snap.microk8s.daemon-containerd.service snap.microk8s.daemon-kubelite.service

[Mount]
What=/mnt/microk8s-hostpath/kagent-lab/ate
Where=/var/lib/ate
Type=none
Options=bind

[Install]
WantedBy=multi-user.target
""")
Path("/etc/systemd/system/kagent-worker-storage-propagation.service").write_text("""[Unit]
Description=Shared mount propagation for Substrate worker storage
Requires=var-lib-ate.mount
After=var-lib-ate.mount
BindsTo=var-lib-ate.mount
Before=snap.microk8s.daemon-containerd.service snap.microk8s.daemon-kubelite.service

[Service]
Type=oneshot
ExecStart=/usr/bin/mount --make-rshared /var/lib/ate
RemainAfterExit=yes
""")
PY
systemctl daemon-reload
systemctl enable --now var-lib-ate.mount
systemctl start kagent-worker-storage-propagation.service
python3 - <<'PY'
from pathlib import Path

for service in ("containerd", "kubelite"):
    directory = Path(f"/etc/systemd/system/snap.microk8s.daemon-{service}.service.d")
    directory.mkdir(exist_ok=True)
    (directory / "kagent-worker-storage.conf").write_text("""[Unit]
Requires=var-lib-ate.mount kagent-worker-storage-propagation.service
After=var-lib-ate.mount kagent-worker-storage-propagation.service
BindsTo=var-lib-ate.mount
""")
PY
systemctl daemon-reload
systemctl start snap.microk8s.daemon-containerd.service snap.microk8s.daemon-kubelite.service
nsenter --target 1 --mount -- findmnt -rn -M "$destination" -o SOURCE,TARGET,FSTYPE,OPTIONS,PROPAGATION
containerd_pid=$(systemctl show -p MainPID --value snap.microk8s.daemon-containerd.service)
nsenter --target "$containerd_pid" --mount -- findmnt -rn -M "$destination" -t ext4 >/dev/null
test "$(nsenter --target "$containerd_pid" --mount -- findmnt -rn -M "$destination" -o PROPAGATION)" = shared
/snap/bin/microk8s kubectl wait --for=condition=Ready node/"$(hostname | tr '[:upper:]' '[:lower:]')" --timeout=120s
printf 'PASS: persistent ext4 worker bind mount visible to containerd on %s\n' "$(hostname)"
