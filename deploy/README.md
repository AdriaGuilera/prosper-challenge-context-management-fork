# Deploy

`https://prosper-challenge.adriaguilera.com` runs `deploy/docker-compose.yml` on the VPS via Dokploy,
which redeploys on every push to `main`.

- **app** — the image from the root `Dockerfile`: ingests the given catalog, then runs `bot.py`
  (UI at `/builder/`, agents API at `/api/agents`, WebRTC signalling at `/start` and `/api/offer`).
- **db** — Postgres from `db/`, bound to `127.0.0.1:5433`.

The app runs on the host network so WebRTC audio (UDP, ports 32768–60999) reaches it directly; only
HTTP signalling goes through Cloudflare and Traefik. Traefik adds TLS and HTTP basic auth (one demo
user, configured on the server, not in this repo).

```bash
curl -u USER:PASS https://prosper-challenge.adriaguilera.com/api/agents
```
