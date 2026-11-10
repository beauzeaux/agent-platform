# Ubuntu / WSL site bootstrap

Use [setup-wsl-wireguard.sh](setup-wsl-wireguard.sh) from a **local Ubuntu terminal**, not an SSH session through the
interface it will restart. The script configures persistent `wg-quick` networking and, by default, VPN-only public-key
SSH. It does not deploy Azure resources, create Kubernetes networks, configure NAT, or open forwarding firewall rules.

WSL must have systemd enabled. Merge this into `/etc/wsl.conf`, keeping existing settings:

```ini
[boot]
systemd=true
```

Run `wsl --shutdown` from Windows PowerShell and reopen Ubuntu. This stops all running WSL distributions.
Ubuntu starts the enabled services when the distribution starts; this does not launch WSL automatically at Windows boot.

## Add a site

1. Create a durable identity on the new machine:

   ```bash
   sudo bash scripts/setup-wsl-wireguard.sh --prepare-only
   ```

   To import an existing identity instead:

   ```bash
   sudo bash scripts/setup-wsl-wireguard.sh --prepare-only \
     --private-key /run/secrets/wg-private-key
   ```

   Only the public key is printed. The private key is retained at `/etc/wireguard/site-private.key`, owned by root with
   mode `600`. Existing identities are reused; importing a different key fails rather than silently rotating the peer.

2. Add the printed public key to [main.bicepparam](../main.bicepparam), using an unused address and non-overlapping
   subnet(s). For a fourth gateway, provided these ranges are actually configured behind it:

   ```bicep
   {
     name: 'devbox-3'
     overlayAddress: '10.82.0.5/32'
     publicKey: '<printed-public-key>'
     subnetPrefixes: [
       '172.30.3.0/24'
     ]
   }
   ```

   Use `subnetPrefixes: []` for a standalone client. Prefixes route existing networks; they do not create them.
   Redeploy the complete inventory using the [hub deployment procedure](../README.md#deploy).

3. Read the main host's SSH **public** key (for example, `~/.ssh/id_ed25519.pub`) on that host.
   Pass its complete line as a quoted string below; no key file needs to be copied onto the new machine.
   The target login account must already exist. Replace the username, public-key string, and hub endpoint/public key
   with your values:

   ```bash
   sudo bash scripts/setup-wsl-wireguard.sh \
     --address 10.82.0.5/32 \
     --hub-endpoint nibeaulihub-krn2gfikk2rlg-v4.westus2.cloudapp.azure.com:51820 \
     --hub-public-key 'z60ie56t4dl1Kf50ica5iE5gEwBy3HhOEHO0laaeSkw=' \
     --allowed-ips '10.82.0.0/24,172.30.0.0/24,172.30.1.0/24,172.30.2.0/24' \
     --ssh-user youruser \
     --ssh-key 'ssh-ed25519 REPLACE_WITH_PUBLIC_KEY_BASE64 main-host' \
     --gateway
   ```

   Omit `--gateway` for a client; use `--no-ssh` to leave SSH untouched. The default remote network is only
   `10.82.0.0/24`. Include the other sites' subnets, not this machine's own attached subnet. IPv4 RFC1918 prefixes only
   are supported; public/default routes and noncanonical network prefixes are rejected.
   `--ssh-key` is required when SSH setup is enabled. It accepts a full OpenSSH public-key line, not merely its base64
   field, a WireGuard key, or a private key. It is validated and appended to the target user's `authorized_keys`;
   existing entries are preserved and an identical line is not appended again.

4. Test both directions:

   ```bash
   ip route get 10.82.0.2
   ping -c 3 -W 2 10.82.0.1
   ping -c 3 -W 2 10.82.0.2
   sudo wg show wg0
   ```

   From an existing peer: `ssh youruser@10.82.0.5`. A recent handshake proves tunnel connectivity, not cluster routing.
   Repeat the same setup command to update a managed configuration; it reuses the identity, preserves existing
   authorized keys, and restarts the services. Keep the command (which contains no private keys) for repeatability.

## SSH behavior and migration

SSH binds only to the site's WireGuard address and allows the specified account only from `10.82.0.0/24`, configurable
with `--ssh-source`. Password and keyboard-interactive authentication are disabled. Ubuntu socket activation is disabled
in favor of `ssh.service`, which is ordered after `wg-quick@wg0`.

The script validates effective SSH settings and rejects competing listeners/authentication settings instead of claiming
success. Review existing `ListenAddress`, `AllowUsers`, and `Match` entries if validation fails. Settings belong under
`/etc/ssh/sshd_config.d/`, **not** `/etc/ssh/ssh_config.d/` (the client directory).
When installing OpenSSH for the first time, the script temporarily runtime-masks its units to prevent the package from
starting an unrestricted listener. If installation fails, review the error and those masks before retrying installation.

Binding and source-address restrictions are not an ingress-interface firewall. If your policy requires packets to arrive
specifically on `wg0`, enforce that with the host's existing firewall. Forwarded container source addresses are excluded
from SSH by default. Existing keys for the chosen account are preserved.

Existing files not marked as managed by this script are not overwritten. To migrate the previous manual setup:

- From a local terminal, stop `wg-quick@wg0` if running, or use `wg-quick down wg0` for a manually started wg-quick
  interface. For an interface created directly with `ip`, use `sudo ip link delete wg0`.
- Securely back up and move aside any existing `/etc/wireguard/wg0.conf` and competing SSH configuration.
- Import the existing private key with `--prepare-only --private-key FILE`, then run the setup command.

## Container / Kubernetes gateways

`--gateway` persists **only** `net.ipv4.ip_forward=1`. It does not configure a forwarding firewall, NAT, cluster Pod/Service
CIDRs, or a return route. Omitting the flag later does not disable forwarding enabled previously.

- Advertise actual reachable node/container or Pod CIDRs in the hub inventory, without overlapping other sites or WSL.
- Configure the gateway's route into the cluster and the cluster's return route through this gateway.
- Permit only intended `wg0`/cluster forwarding in your existing firewall; do not blindly replace CNI-managed rules.
- Add this site's advertised networks to other clients' `AllowedIPs` and routes. Rerunning their bootstrap with updated
  `--allowed-ips` installs the routes through `wg-quick`.
- Test pod-to-pod/application traffic, not just gateway pings. ClusterIP ranges require CNI-specific handling.

No Azure hosting-VNet route is added by default. This is **not hub-wide isolation enforcement**: the existing hub's
`attachedNetworkPrefixes` default still permits direct VNet forwarding unless separately changed.

## Recovery and removal

Back up `/etc/wireguard/site-private.key` **securely outside the machine**; never commit it or the generated `wg0.conf`.
On a replacement machine, import that key and repeat setup with the same address. Do not run the old and replacement
instances concurrently with the same key. SSH authorized keys can be restored by rerunning setup with each public key.
If the WireGuard private key is lost, generate a new identity, update the site's public key, and redeploy the hub.

To revoke a site, remove it from `sites` (or set `enabled: false`) and redeploy. Turning off the guest alone does not revoke
its key. Remove its subnet routes from other clients if no replacement site owns them. Remove its SSH client keys from
other machines' `authorized_keys` separately; WireGuard and SSH identities are independent.

On the retired guest, stop automatic connection:

```bash
sudo systemctl disable --now wg-quick@wg0.service
```

This retains the identity for recovery. The SSH dependency prevents new service startup without WireGuard; from a local
terminal, also stop `ssh.service` if decommissioning the guest. If repurposing it, explicitly review/remove the managed SSH
drop-in and dependency before configuring its new listener. Do not disable IPv4 forwarding blindly if Kubernetes uses it.

For diagnostics:

```bash
sudo systemctl status wg-quick@wg0.service ssh.service --no-pager
sudo journalctl -u wg-quick@wg0.service -u ssh.service -n 100 --no-pager
sudo wg show wg0
sudo ss -lntp 'sport = :22'
```

`wg showconf wg0` and `wg0.conf` expose the private key; do not share their contents.

## Validate the script without changing networking

```bash
bash -n scripts/setup-wsl-wireguard.sh
bash tests/test_setup_wsl_wireguard.sh
```

These tests cover input validation and generated configurations without touching system services or keys. Actual WSL
boot persistence, SSH login, and end-to-end gateway routing must be tested on the target instance.
When `wg` and `sshd` are available, they also validate a temporary key and the effective generated SSH configuration;
otherwise those checks are explicitly skipped.
