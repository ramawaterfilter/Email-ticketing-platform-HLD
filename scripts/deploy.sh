#!/usr/bin/env bash
# Pull-based deploy: redeploys only when origin/main has new commits.
# Run on a schedule by support-desk-deploy.timer (systemd). No GitHub runner,
# no inbound ports — the server pulls. See deploy/README.md.
set -euo pipefail

cd "${DEPLOY_DIR:-$HOME/support-desk}"

git fetch --quiet origin main
if [ "$(git rev-parse @)" = "$(git rev-parse origin/main)" ]; then
  exit 0 # already up to date, nothing to do
fi

echo "$(date -Is) deploying $(git rev-parse --short origin/main)"
git reset --hard origin/main          # .env is gitignored, so it is untouched
docker compose --profile app build
docker compose --profile app up -d --remove-orphans
docker image prune -f                 # drop old layers so disk does not fill
echo "$(date -Is) deploy done"
