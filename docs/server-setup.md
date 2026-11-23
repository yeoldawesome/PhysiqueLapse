# Run PhysiqueLapse on a home server

PhysiqueLapse runs in its own Docker container on the server. You open it in a browser on your phone or laptop at `https://<server>.<tailnet>.ts.net:8443`. Tailscale provides HTTPS, and only your tailnet can reach it. Uploads, landmark extraction, alignment and exports all run on the server. The desktop app keeps working as before, with its own library.

The steps below change the host, so **you** run them. They were written for the server as it was checked on 2026-11-23:

| | |
|---|---|
| OS | Ubuntu 26.04.1 LTS, 8 CPUs, 30 GiB RAM |
| Other app | **Better Lift**: `betterlift-pb` (PocketBase, `127.0.0.1:8090`), `betterlift-push` (`127.0.0.1:8092`), public through `cloudflared` (`127.0.0.1:20241`). Not in Docker. |
| Docker | not installed |
| Tailscale | 1.104.7, no `serve` config, HTTPS certificates **not enabled** yet |
| PhysiqueLapse uses | host port `127.0.0.1:8420`, Tailscale HTTPS port `8443`, data in `/srv/physiquelapse` |

Nothing here touches Better Lift's services, ports, files or its Cloudflare tunnel. Each step ends with a check that Better Lift is still fine.

## 0. Check Better Lift (do this before and after every step)

```bash
systemctl is-active betterlift-pb betterlift-push cloudflared   # three lines: active
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8090/api/health   # 200
```

Also open Better Lift's public URL on your phone once. Save this as `~/check-betterlift.sh` if you like.

## 1. Look at what's already in use

```bash
sudo ss -tlnp                 # listening ports and which program owns each
systemctl list-units --type=service --state=running
tailscale serve status        # must not already list :8443
docker ps 2>/dev/null         # (no Docker yet)
```

If `8420` or `8443` shows up as taken, pick other numbers and use them for `PL_PORT` (in `.env`) and `--https=` (step 6).

## 2. Install Docker Engine

Use Docker's own apt repository ([docs.docker.com/engine/install/ubuntu](https://docs.docker.com/engine/install/ubuntu/)):

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER     # then log out and back in (or: newgrp docker)
docker run --rm hello-world
```

If Docker's repo has no packages for this Ubuntu release yet (26.04 is new), `apt-get update` complains about the `docker.list` line. Use Ubuntu's own packages instead: `sudo apt-get install -y docker.io docker-compose-v2 docker-buildx`.

Docker adds its own firewall (iptables) rules for container networks. Services that listen on `127.0.0.1` and Cloudflare's outbound tunnel are not affected. **Check Better Lift (step 0).**

## 3. Get the code and configure it

```bash
sudo mkdir -p /opt/physiquelapse /srv/physiquelapse/data
sudo chown -R $USER:$USER /opt/physiquelapse /srv/physiquelapse
git clone https://github.com/yeoldawesome/PhysiqueLapse.git /opt/physiquelapse
cd /opt/physiquelapse
cp .env.example .env
nano .env        # set TZ (e.g. America/Chicago); check PUID/PGID match `id`
```

## 4. (Optional) bring your existing library

Your desktop library can become the server's library. Close the desktop app first. The library folder is `%LOCALAPPDATA%\PhysiqueLapse` for the installed app, or `data\` in a source checkout. On Windows (PowerShell, with Tailscale connected):

```powershell
scp -r "$env:LOCALAPPDATA\PhysiqueLapse\*" betterlifts:/srv/physiquelapse/data/
```

Paths in the database are relative to the library folder, so it works as-is. On first start the server upgrades the database and keeps a copy of the old one (`physiquelapse.before-v<N>.db`). From then on, add photos on the server. The desktop copy no longer syncs with it.

## 5. Start it

```bash
cd /opt/physiquelapse
docker compose up -d --build          # first build: ~10 min
docker compose logs -f                # first start downloads the models (~650 MB) into /srv/physiquelapse/data/models
docker compose exec physiquelapse python -m app.auth set-password
docker compose ps                     # STATUS shows (healthy)
curl -s http://127.0.0.1:8420/api/health          # {"status":"ok"}
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8420/api/dashboard   # 401 (login required)
```

The container is capped at 5 CPUs and 8 GB of memory, and its jobs run at a lower priority (`PL_CPUS`, `PL_MEM`, `PL_CV_WORKERS`, `PL_JOB_NICE` in `.env`). Watch it with `docker stats physiquelapse`. **Check Better Lift (step 0).**

## 6. HTTPS on your tailnet

Phone cameras only work on HTTPS pages, and `tailscale serve` gives the server a real certificate for its `*.ts.net` name.

1. In the Tailscale admin console → **DNS**: make sure **MagicDNS** is on, then **Enable HTTPS** (certificates).
2. On the server:

```bash
sudo tailscale set --operator=$USER                       # lets you run `tailscale serve` without sudo
tailscale serve status                                     # see what's there; leave other entries alone
tailscale serve --bg --https=8443 http://127.0.0.1:8420
tailscale serve status                                     # shows https://betterlifts.<tailnet>.ts.net:8443 -> 127.0.0.1:8420
```

Open `https://betterlifts.<your-tailnet>.ts.net:8443` on your phone (Tailscale on) and log in. `--bg` keeps it after reboots. To remove only this entry later: `tailscale serve --https=8443 off`.

This is reachable only from devices on your tailnet. Don't use `tailscale funnel`, which would put it on the internet. **Check Better Lift (step 0).**

## Update

```bash
cd /opt/physiquelapse
git pull
docker compose up -d --build
docker image prune -f
```

The database upgrades itself on start and keeps a copy of the old one.

## Back up

Everything lives in `/srv/physiquelapse/data`: originals, database, derived images and models. The models can be re-downloaded and `derived/` can be rebuilt, but copying them too makes restoring faster.

```bash
# consistent copy of the database while the app runs
sqlite3 /srv/physiquelapse/data/physiquelapse.db ".backup '/srv/physiquelapse/backup-$(date +%F).db'"
# the photos (and everything else) to another disk / machine
rsync -a --delete /srv/physiquelapse/data/ /mnt/backup/physiquelapse/
```

Or the simplest consistent copy: `docker compose stop && rsync -a /srv/physiquelapse/data/ <dest>/ && docker compose start`.

**Restore**: stop the container, put the folder back at `PL_DATA_PATH` (owned by `PUID:PGID`), and start it again.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Container exits with "/data is not writable" | `sudo chown -R 1000:1000 /srv/physiquelapse` (or your `PUID:PGID`) |
| Login says "No password set yet" | `docker compose exec physiquelapse python -m app.auth set-password` |
| Forgot the password | same command; it also logs every browser out |
| Log out every device | `docker compose exec physiquelapse python -m app.auth revoke-sessions` |
| Dates are hours off | set `TZ` in `.env`, then `docker compose up -d` |
| "cross-origin request refused" | you're using an address other than the one in the browser bar; add it to `PL_ALLOWED_ORIGINS` |
| Camera doesn't start on the phone | the page must be the `https://…ts.net:8443` address, not `http://` |
