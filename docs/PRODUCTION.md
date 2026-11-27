# Production rollout (on-prem + Tailscale)

How Rama Support Desk runs in production for the team: the support mailbox
`support@phoenixwaterfilters.co.uk`, Google sign-in for the team, secure LAN
access over Tailscale, and auto-start after a reboot.

**Setup assumptions**
- `phoenixwaterfilters.co.uk` and `ramawaterfilter.com` are domains in **one
  Google Workspace** → the OAuth app can be **Internal** (no Google
  verification, refresh tokens don't expire).
- The app runs on the WSL server; the team reaches it over **Tailscale**.
- Replace `<name>.ts.net` below with the server's real MagicDNS name.

Roles:
| Email | Role |
|---|---|
| anandraj.g@ramawaterfilter.com | admin (bootstrap — first sign-in) |
| nicepal.m@ramawaterfilter.com | admin (added in the UI) |
| supriya.k@ramawaterfilter.com | executive (added in the UI) |

---

## Phase 1 — Tailscale HTTPS hostname

1. Tailscale admin console → enable **MagicDNS** and **HTTPS Certificates**.
2. On the server, find the MagicDNS name: `tailscale status` → e.g. `desktop-agimguu.tailXXXX.ts.net`.
3. Free host `:443` for Tailscale by binding the web container to localhost. In `~/support-desk/.env`:
   ```
   DESK_HTTP_BIND=127.0.0.1
   DESK_HTTPS_BIND=127.0.0.1
   DESK_HTTPS_PORT=8443
   ```
   Apply: `docker compose --profile app up -d`
4. Serve the app over Tailscale HTTPS:
   ```
   sudo tailscale serve --bg 80
   tailscale serve status   # shows https://<name>.ts.net -> http://127.0.0.1:80
   ```
5. Team machines: install Tailscale, join the tailnet, browse to `https://<name>.ts.net`.

Base URL is now **`https://<name>.ts.net`** (used by Google OAuth + `.env`).

## Phase 2 — Google Cloud OAuth client (Internal)

In the Google Cloud project tied to the Workspace:
1. **APIs & Services → Library → enable Gmail API.**
2. **OAuth consent screen → User type: Internal** → app name + support email.
3. **Credentials → Create OAuth client ID → Web application.** Authorized redirect URIs (both):
   - `https://<name>.ts.net/api/v1/auth/google/callback`
   - `https://<name>.ts.net/api/v1/mailboxes/oauth/callback`
4. Copy the **Client ID** and **Client secret**.

## Phase 3 — Switch the app to production

In `~/support-desk/.env` set/confirm, then `docker compose --profile app up -d`:
```
DESK_ENVIRONMENT=prod
DESK_PUBLIC_URL=https://<name>.ts.net
DESK_SITE_ADDRESS=:80
DESK_ALLOWED_DOMAINS=ramawaterfilter.com
DESK_BOOTSTRAP_ADMIN_EMAIL=anandraj.g@ramawaterfilter.com
DESK_GOOGLE_CLIENT_ID=<from Phase 2>
DESK_GOOGLE_CLIENT_SECRET=<from Phase 2>
DESK_DEV_LOGIN_ENABLED=false
```
Keep `DESK_SESSION_SECRET`, `DESK_TOKEN_KEY`, `POSTGRES_PASSWORD` unchanged.
`prod` disables the dev-login backdoor — Google sign-in must work from here on.

## Phase 4 — First admin signs in, then add the team

1. Open `https://<name>.ts.net` → **Sign in with Google** as
   `anandraj.g@ramawaterfilter.com` → becomes admin (bootstrap).
2. **Team** screen → add `nicepal.m@ramawaterfilter.com` (admin) and
   `supriya.k@ramawaterfilter.com` (executive).
3. Everyone signs in with their own Google account; only pre-added, active
   `@ramawaterfilter.com` users get in.

## Phase 5 — Connect the support mailbox

1. As admin → **/admin/emails → Connect** → Google sign-in as
   `support@phoenixwaterfilters.co.uk` (a real licensed Workspace mailbox, not
   just an alias) → grant Gmail access.
2. Map the address `support@phoenixwaterfilters.co.uk` → your store, then set
   the mailbox **Active** (won't activate until the default address is mapped).
3. The worker ingests incoming mail into tickets; replies send **from that address**.

## Phase 6 — Always-on (resume after reboot, no commands)

Goal: a Windows reboot brings WSL → Docker → containers → Tailscale back with
zero manual steps.

- WSL systemd on: `/etc/wsl.conf` →
  ```
  [boot]
  systemd=true
  ```
  (then `wsl --shutdown` from Windows once to apply).
- Auto-start services (docker-ce in WSL): `sudo systemctl enable docker tailscaled support-desk-deploy.timer`
  — or, with Docker Desktop: enable "Start Docker Desktop when you log in" + WSL integration.
- Containers already carry `restart: unless-stopped`.
- `tailscale serve` config persists across restarts once set.
- Windows: a Task Scheduler task at logon/startup running `wsl.exe -d <distro> -u root true`
  keeps WSL (and everything in it) running headless.

Verify after a test reboot: `docker compose --profile app ps` all healthy, and
`https://<name>.ts.net` loads from a team machine.
