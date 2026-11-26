# Pull-based deploy (no GitHub runner)

The WSL server polls `origin/main` every 2 minutes and redeploys when it
changes. GitHub executes nothing on the box, so this is safe even on a public
repo. No inbound ports — works over Tailscale.

## One-time install (on the server, as `palnice`)

```bash
cd ~/support-desk
git fetch origin && git checkout main
git reset --hard origin/main   # discards local edits to tracked files (e.g. a hand-edited
                               # docker-compose.yml); .env is gitignored and preserved
chmod +x scripts/deploy.sh

sudo cp deploy/support-desk-deploy.service deploy/support-desk-deploy.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now support-desk-deploy.timer
```

If your Linux user or path is not `palnice` / `/home/palnice/support-desk`,
edit `support-desk-deploy.service` (the `User=` and both `Environment=` lines)
before copying.

## Check it

```bash
systemctl list-timers | grep support-desk     # next run time
journalctl -u support-desk-deploy -f           # live deploy logs
sudo systemctl start support-desk-deploy        # force a deploy now
```

## Notes

- `.env` is gitignored, so `git reset --hard` never touches your secrets.
- DB migrations run automatically on `api` start (its Dockerfile `CMD`).
- The script only rebuilds when `main` actually has new commits; idle runs exit in well under a second.
