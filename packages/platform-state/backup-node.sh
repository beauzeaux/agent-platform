#!/usr/bin/env bash
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin
backup=/var/snap/microk8s/common/kagent-lab-backup-20261031
install -d -m 700 "$backup"
for component in kube-apiserver kubelet kube-controller-manager; do
    if [ ! -e "$backup/$component" ]; then
        install -m 600 "/var/snap/microk8s/current/args/$component" "$backup/$component"
    fi
done
if [ ! -e "$backup/datastore.tar.gz" ]; then
    /snap/bin/microk8s dbctl backup -o "$backup/datastore.tar.gz" >"$backup/backup.log" 2>&1
    chmod 600 "$backup/datastore.tar.gz"
    chmod 600 "$backup/backup.log"
fi
tar -tzf "$backup/datastore.tar.gz" >/dev/null
printf 'PASS: argument backups and readable datastore archive on %s\n' "$(hostname)"
findmnt -rn -M /mnt/microk8s-hostpath -o TARGET,FSTYPE
