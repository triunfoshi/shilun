#!/usr/bin/env bash
# Upload this file to /root, then run: bash /root/setup-ecs-management.sh
# Contains only a public key. Existing tunnel keys and SSH settings are preserved.
set -euo pipefail
[[ ${EUID} -eq 0 ]] || { echo 'Please run as root.' >&2; exit 1; }
command -v ssh-keygen >/dev/null || { echo 'OpenSSH tools are required.' >&2; exit 1; }
MANAGEMENT_PUBLIC_KEY='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBqk0a1v3GmxnxTD5vZbA0QXoCxoZxfJ5iQ293vl3NG4 shilun-ecs-management'
SSH_DIRECTORY=/root/.ssh
AUTHORIZED_KEYS=/root/.ssh/authorized_keys
[[ ! -L "$SSH_DIRECTORY" && ! -L "$AUTHORIZED_KEYS" ]] || { echo 'Refusing symlinked SSH paths.' >&2; exit 1; }
install -d -m 700 "$SSH_DIRECTORY"
if [[ -e "$AUTHORIZED_KEYS" ]]; then
  cp -p "$AUTHORIZED_KEYS" "${AUTHORIZED_KEYS}.before-shilun-$(date +%Y%m%d-%H%M%S)"
else
  install -m 600 /dev/null "$AUTHORIZED_KEYS"
fi
if ! grep -Fq -- "$MANAGEMENT_PUBLIC_KEY" "$AUTHORIZED_KEYS"; then
  printf '\n%s\n' "$MANAGEMENT_PUBLIC_KEY" >> "$AUTHORIZED_KEYS"
fi
chmod 600 "$AUTHORIZED_KEYS"
chown root:root "$SSH_DIRECTORY" "$AUTHORIZED_KEYS"
if command -v restorecon >/dev/null; then restorecon -RF "$SSH_DIRECTORY"; fi
printf 'Installed management public key: '
printf '%s\n' "$MANAGEMENT_PUBLIC_KEY" | ssh-keygen -lf -
printf '\nFrom your Mac, run:\n  ssh -i ~/.ssh/shilun_ecs_management_ed25519 -o IdentitiesOnly=yes root@47.110.33.132\n'
printf '\nExisting SSH configuration, tunnel key, firewall, and MongoDB were not changed. No SSH restart is required.\n'
