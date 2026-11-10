# VPS deployment and 24/7 operations runbook

## Scope and safety status

This runbook prepares a single Linux VPS for the current **paper-only** AI Trading System. It does not deploy automatically, connect trading-enabled keys, enable live orders, or make the application production-safe for real-money execution. Runtime trading remains simulated; an optional Binance Spot key can only read account balances and is not linked to the paper portfolio.

A VPS being online 24/7 is an infrastructure milestone, not permission to trade real funds.

## Suggested initial host profile

- Ubuntu Server 24.04 LTS (or another supported LTS release).
- 2 vCPU, 2 GiB RAM and at least 20 GiB SSD as a starting point for the current lightweight FastAPI/SQLite stack.
- Reliable provider networking, snapshots, console recovery and an independent backup destination.
- Prefer a region with stable connectivity to the configured market-data providers. Measure latency and provider rate limits rather than assuming proximity improves execution.

Monitor RAM, CPU, disk and network after deployment. Increase resources based on observed load; this profile is not a performance guarantee.

## 1. Host hardening

1. Create a non-root administrator and configure SSH public-key authentication.
2. Verify a second SSH session works before disabling password authentication or root SSH login.
3. Enable automatic security updates and time synchronization (NTP).
4. Configure the firewall with inbound deny by default; allow SSH only. Do not open TCP 8000 or database/backup ports publicly.
5. Install Docker Engine and the Docker Compose plugin from the official Docker repository.
6. Restrict Docker administration: membership in the `docker` group is effectively root-equivalent.
7. Keep provider credentials and `.env` files out of shell history, support bundles and Git.

Example firewall policy (adjust the SSH port before applying if it is not 22):

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw enable
sudo ufw status verbose
```

## 2. Install the application

Use a dedicated deployment path such as `/opt/ai-trading-system`. Clone the repository and deploy only a reviewed commit from `main`.

```bash
sudo mkdir -p /opt/ai-trading-system
sudo chown "$USER":"$USER" /opt/ai-trading-system
git clone https://github.com/Harilai/ai-trading-system.git /opt/ai-trading-system
cd /opt/ai-trading-system
cp .env.example .env
```

Generate a strong owner control token locally on the VPS and set it in `.env`; never paste it into a chat or commit it:

```bash
openssl rand -hex 32
chmod 600 .env
```

Configure only the public market-data settings that are required. Do not put exchange trading credentials, wallet seeds or private keys in this environment. If Twelve Data is used, keep its key server-side and rotate it if exposed.

For off-host backups, copy `.env.backup.example` to `.env.backup`, configure a private bucket and least-privilege upload/list/read permissions, then:

```bash
chmod 600 .env.backup
```

The API container must not receive S3 credentials. The backup service loads `.env.backup` separately. Configure bucket versioning, public-access blocking, encryption and lifecycle retention at the storage provider.

## 3. Start and verify

```bash
docker compose config --quiet
docker compose up --build -d
docker compose ps
docker compose logs --tail=200 api backup
curl --fail http://127.0.0.1:8000/api/health
curl --fail http://127.0.0.1:8000/api/health/ready
```

Expected API health response must state `mode=paper` and `live_trading_enabled=false`. Readiness also requires a recent worker heartbeat. Check the backup container health:

```bash
docker inspect --format '{{.State.Health.Status}}' ai-trading-backup
docker compose logs --tail=100 backup
```

If off-host backups are enabled, check `GET /api/agent/backup-status` with the owner token over a trusted local/SSH connection and confirm external status is `verified`. Do not treat a local-only snapshot as off-host protection.

## 4. Start automatically after host reboot

The repository includes `deploy/ai-trading-compose.service`. Review its `WorkingDirectory` and Docker binary path, then install it:

```bash
sudo cp deploy/ai-trading-compose.service /etc/systemd/system/ai-trading-compose.service
sudo systemctl daemon-reload
sudo systemctl enable --now ai-trading-compose.service
sudo systemctl status ai-trading-compose.service
```

Compose containers use `restart: unless-stopped`; Docker must be enabled at boot. Test a planned reboot during a maintenance window and verify both containers, API readiness and the backup healthcheck afterwards.

## 5. Remote dashboard access

The API binds to `127.0.0.1:8000` on the VPS. Keep it that way. From your own computer, open an SSH tunnel:

```bash
ssh -N -L 8000:127.0.0.1:8000 your-user@your-vps
```

Then browse to `http://127.0.0.1:8000/dashboard`. The SSH connection protects traffic in transit. Do not expose port 8000 directly. If remote browser access is later required, design a separate TLS reverse proxy with authentication, rate limiting and access controls; do not simply bind the API to `0.0.0.0`.

## 6. Monitoring and incident response

Monitor at minimum:

- API liveness: `/api/health`.
- API readiness: `/api/health/ready` (database and worker heartbeat).
- Backup container health and latest local snapshot time.
- Off-host backup verification when enabled.
- Disk free space, Docker volume size, RAM, CPU, container restarts and system clock.
- Provider errors, rate limits, stale market data and worker task failures.

The Compose configuration rotates container JSON logs. Keep logs free of secrets and never enable verbose HTTP request logging that could expose authorization headers.

If the API is unhealthy, stop new paper activity through the owner controls and investigate logs/SQLite state before restarting repeatedly. If backup health fails, retain the existing volume, fix the cause, and verify a fresh snapshot before any upgrade or restore. For database restore, stop both `api` and `backup` first and follow the external/local restore procedure in the README.

## 7. Updates and rollback

1. Confirm the latest local and off-host backups are verified.
2. Record the currently deployed Git SHA.
3. Fetch and review the target `main` commit and release notes.
4. Fast-forward the checkout; do not make ad-hoc edits on the VPS.
5. Run `docker compose config --quiet`, then `docker compose up --build -d`.
6. Verify API readiness, backup health, logs and persisted portfolio/worker state.
7. If the release is unhealthy, stop the stack and roll back the application code to the previously recorded SHA. Do not blindly restore an older database over newer data; restore only when a documented schema/data recovery plan requires it.

## 8. Not yet approved for real capital

Before any live order path is implemented or enabled, the project still needs authenticated account connectors, per-venue capability and eligibility checks, generic portfolio/ledger reconciliation, instrument-specific risk, sandbox/testnet evidence, execution and partial-fill recovery, operational alerting, security review and an explicit owner-controlled live enablement gate.

Never give trading API keys withdrawal permission. Wallet seed phrases/private keys must not be stored in this application. Live mode remains unimplemented and disabled.
