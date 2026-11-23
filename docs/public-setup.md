# Open PhysiqueLapse to the people you invite

This puts PhysiqueLapse at a public HTTPS address (for example `https://app.your-domain.com`), so people you invite can use it from their phones without Tailscale. It goes through **PhysiqueLapse's own Cloudflare Tunnel**: a separate tunnel with its own token, run by a container inside PhysiqueLapse's Docker project.

- Your home IP stays hidden and no router ports are opened. The container only dials *out* to Cloudflare.
- It's **completely separate from Better Lift**. Better Lift's `cloudflared` system service, its tunnel and its Cloudflare settings are never touched. Use a different tunnel, and preferably a different domain.
- Only people with an account can see anything, and accounts need an invite code from you.

## 1. A domain on Cloudflare

Buy a domain just for PhysiqueLapse (Cloudflare Registrar or anywhere else) and add it to your Cloudflare account (**Add a site** → Free plan), so it uses Cloudflare's nameservers. You can also use a subdomain of a domain you already have on Cloudflare, but a separate domain keeps it independent of Better Lift.

## 2. Create the tunnel

In the Cloudflare dashboard → **Zero Trust** → **Networks** → **Tunnels** → **Create a tunnel**:

1. Type **Cloudflared**, name it `physiquelapse`.
2. On the "Install connector" page, copy the **token**: the long string after `--token` in any of the commands shown. Don't run those commands; the container does this.
3. **Public hostname:**
   - subdomain `app` (or blank), domain: yours
   - service type **HTTP**, URL **`physiquelapse:8420`**

   That's the container's name on PhysiqueLapse's own Docker network.
4. Save.

## 3. Configure and start

```bash
cd /opt/physiquelapse
nano .env
#   CLOUDFLARE_TUNNEL_TOKEN=<the token>
#   PL_PUBLIC_URL=https://app.your-domain.com
#   (optional, for "forgot password" emails) SMTP_HOST=..., SMTP_USER=..., SMTP_PASSWORD=..., MAIL_FROM=...
docker compose --profile public up -d
docker compose logs tunnel | grep -i "registered tunnel connection"     # 4 lines = connected
```

From now on, always use `docker compose --profile public ...` for `up`, `pull` and `down`, so the tunnel is included.

## 4. Check

- Open `https://app.your-domain.com` on your phone **with Wi-Fi and Tailscale off**: the sign-in page loads, with a valid certificate.
- Sign in, go to **Admin** → **Create invite**, and send the link to someone.
- Better Lift is untouched:
  ```bash
  systemctl is-active betterlift-pb betterlift-push cloudflared   # active x3
  curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8090/api/health   # 200
  ```
  Open Better Lift's public URL once too.

## Recommended Cloudflare settings (for this domain)

- **SSL/TLS** → Edge Certificates → **Always Use HTTPS**: on.
- **Caching:** leave the defaults. The app marks every API response and photo `private` / `no-store`, so Cloudflare never stores anyone's data.
- **Uploads:** Cloudflare's free plan accepts requests up to 100 MB. The app uploads in 8 MB pieces, so large batches still work.
- **Optional:** Security → WAF → a rate-limiting rule on `/api/auth/` as an extra layer on top of the app's own limits.

## Email for password resets (optional)

Without email, a person who forgets their password asks you, and you send them a reset link from **Admin** → **Reset link**. To make "Forgot password?" work by itself, set SMTP in `.env`, e.g. with **Resend** (free tier):

1. Sign up at resend.com, add your domain and add the DNS records it shows to Cloudflare DNS.
2. Create an API key, then in `.env`:
   ```
   SMTP_HOST=smtp.resend.com
   SMTP_PORT=587
   SMTP_USER=resend
   SMTP_PASSWORD=<the API key>
   MAIL_FROM=PhysiqueLapse <no-reply@your-domain.com>
   ```
3. Restart with `docker compose --profile public up -d`.

## Turning it off

`docker compose stop tunnel` takes the public address offline at once; Tailscale access keeps working. Delete the tunnel in Zero Trust to remove it for good.
