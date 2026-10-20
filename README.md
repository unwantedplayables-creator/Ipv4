# NovaNode Bot

Discord VPS-management bot with a web terminal. VPSs are Docker containers on the machine running this bot; the bot cannot provide more CPU, RAM, or disk than that host has available.

## Install

Use a Linux host with Docker Engine, Python 3, and enough disk/RAM for the VPS containers. The app creates privileged Docker containers, so use a dedicated, trusted host. A Codespace is suitable for testing only; it can sleep or be deleted.

```sh
chmod +x install_sift.sh
./install_sift.sh
```

The installer asks for the public site URL and Discord bot token, then starts the bot. The token is saved in `sift.env`, which must remain private. Enable the required privileged intents for the Discord application (Message Content and Server Members) in the Developer Portal.

## Public web terminal

Set `SIFT_WEB_URL=https://novahost.qzz.io` in `sift.env`. In Cloudflare Zero Trust, create a tunnel public-hostname route to `http://host.docker.internal:8082`. For a Docker-based connector on Linux, add `--add-host=host.docker.internal:host-gateway` to its `docker run` command. Check `https://novahost.qzz.io/health` for `ok`.

The tunnel must keep running. See `novanode-tunnel.service.example` for a systemd template. Store the tunnel token on the host in `/etc/novanode/cloudflare.env` with permissions `600`; never put it in this archive or a public repository.

## Settings

Edit `settings.txt` (JSON format), then restart the bot:

- `brand_name`: website and bot display name.
- `max_vps_cpus`, `max_vps_ram_gb`: per-VPS caps.
- `max_total_vps_cpus`, `max_total_vps_ram_gb`: aggregate running-VPS caps.
- `web_idle_seconds`: browser idle timeout. A timeout disconnects the browser, not the tmux shell.
- `daily_claim_credits`, `daily_claim_cooldown_seconds`: daily claim reward and interval.
- `vps_plans`: `&buywc` plan sizes and Intel/AMD credit prices.
- `coin_plans`: `&claim` plan sizes and credit prices.

The bot also checks Docker's reported host capacity and existing running VPS reservations. Settings cannot increase the host's actual resources. Disk sizes are currently recorded and displayed but are not enforced as Docker storage quotas.

## Data and backups

`user_data.json`, `vps_data.json`, `admin_data.json`, and `coupons.json` live beside the bot. JSON bot records are atomically replaced, backed up as `.bak`, and restricted to the bot user's account. Back up these files and `settings.txt` to persistent storage. Do not include these user/VPS records or `sift.env` in public archives.

Restarting the bot does not erase records. Deleting the host's disk or running VPS containers can still lose data. For a new host, migrate the JSON files and the corresponding Docker containers/volumes together; copying the JSON alone does not migrate the VPS filesystem.

## 24/7 Linux service

Create a dedicated Linux account (for example `novanode`), add it to the Docker group, install the project under `/opt/novanode`, and create its venv/dependencies. Copy `novanode-bot.service.example` to `/etc/systemd/system/novanode-bot.service`, adjust the paths/user, then enable it:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now novanode-bot
sudo systemctl status novanode-bot
```

Use a Linux VPS or dedicated server with Docker Engine, persistent disk, and enough resources for the total VPS allocations plus overhead. The current Codespace exposes 2 CPUs and about 7.8 GiB RAM; it cannot successfully allocate a 4-core VPS and is not a 24/7 production host. Cloudflare Tunnel only exposes the web terminal; it does not host or scale the VPS containers.

## Existing credit commands

`&coupon create CODE SLOTS CREDITS` creates an admin-only redeem code; users redeem it with `&redeem CODE`. `&buywc starter Intel` purchases a configured plan. `&claim` uses configured credit plans. Automatic per-message credits and unrestricted free user VPS creation are intentionally not enabled.
