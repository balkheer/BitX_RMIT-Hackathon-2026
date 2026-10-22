# Builds the SPA and serves it with Caddy, which also terminates TLS and proxies /api.
# Debian-based Node image: the lockfile's native build tools (rolldown) install reliably on glibc.
FROM node:22-slim AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm config set fetch-retries 5 \
 && npm config set fetch-retry-mintimeout 20000 \
 && npm config set fetch-retry-maxtimeout 120000 \
 && npm ci --no-audit --no-fund --loglevel=warn
COPY . .
RUN npm run build && rm -f dist/tidecloak.json dist/tidecloak.example.json

FROM caddy:2-alpine
COPY --from=build /src/dist /srv
COPY deploy/Caddyfile /etc/caddy/Caddyfile
