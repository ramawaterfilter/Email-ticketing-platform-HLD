FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# Build Caddy with the DuckDNS DNS module so it can obtain certs via the DNS-01
# challenge — required for on-prem deploys with no inbound HTTP/TLS reachability.
FROM caddy:2-builder-alpine AS caddybuild
RUN xcaddy build --with github.com/caddy-dns/duckdns

# Caddy serves the built React app, forwards /api to the backend, and handles HTTPS certificates.
FROM caddy:2-alpine
COPY --from=caddybuild /usr/bin/caddy /usr/bin/caddy
COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /srv
