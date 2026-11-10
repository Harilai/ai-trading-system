# External backup and recovery design

## Objective

Keep the existing verified local SQLite snapshots, then replicate each successful snapshot to an off-host S3-compatible object store. The off-host copy must survive loss of the Docker volume or VPS.

## Storage model

- Local snapshots remain in `BACKUP_DIRECTORY` with the existing local retention policy. When off-host storage is enabled, retention does not delete a local snapshot until its remote copy has passed read-back verification; a prolonged outage can therefore consume local disk and must be monitored.
- Remote objects use immutable keys containing source database name, UTC timestamp and SHA-256 prefix.
- Supported destinations: AWS S3 and S3-compatible services such as Cloudflare R2, Backblaze B2 or self-hosted MinIO, subject to endpoint and encryption capabilities.
- The application never creates buckets, changes bucket policies, deletes remote backups or configures lifecycle rules. Operators configure private bucket access, versioning and lifecycle retention in the storage provider.
- Remote uploads use HTTPS and server-side encryption (`AES256` or `aws:kms`); `provider-managed` is allowed only when the selected provider documents encryption at rest by default.
- Upload verification checks object metadata, size and a streamed SHA-256 read-back. Restore downloads to a temporary file, verifies SHA-256 and SQLite integrity, then atomically publishes the candidate.

## Credential isolation

- S3 credentials are configured only in the backup service's optional `.env.backup` file or the backup container's IAM/task role.
- The API container must not receive backup storage credentials.
- The backup container has no published ports, a read-only root filesystem, non-root user, dropped capabilities and a dedicated egress network.
- Credentials are never written to logs, status files, object metadata or Git.
- Grant only `s3:ListBucket` on the bucket and `s3:PutObject`/`s3:GetObject` on the configured prefix; do not grant delete permissions to the backup writer. For SSE-KMS, grant only the KMS encrypt/decrypt/data-key permissions required by the chosen key. Prefer a separate administrator role for bucket lifecycle/versioning.

## Restore workflow

1. Stop API and backup services to avoid concurrent SQLite writes.
2. Run `python -m app.backup_remote list` to inspect remote snapshots.
3. Download a selected object to a separate restore-candidate path; checksum and SQLite integrity are validated.
4. Review the candidate and use the existing `app.backup restore` command to restore it to the configured database path; the current database is preserved as a pre-restore safety copy.
5. Start services and verify health, portfolio balances, positions, agent tasks and provider quota state.

Never overwrite the live database while the API is running. A remote restore is an operator action, not an autonomous agent action.

## Failure semantics

- Local snapshot creation continues if off-host storage is unavailable; status explicitly reports the external failure.
- A local backup is not reported as an off-host success until read-back checksum verification completes.
- Missing credentials, TLS errors, bucket permission errors, upload failures, checksum mismatch or invalid SQLite downloads are surfaced as failed status and logs.
- Remote backups are never deleted by the application. Configure retention/lifecycle and versioning in the storage provider, and periodically perform a restore drill.

## Configuration

- `BACKUP_EXTERNAL_ENABLED` defaults to false.
- `BACKUP_S3_BUCKET`, `BACKUP_S3_REGION`, optional HTTPS `BACKUP_S3_ENDPOINT_URL`, `BACKUP_S3_PREFIX` and encryption mode configure the destination.
- Static credentials belong in `.env.backup`, which is mounted only into the backup service. Alternatively, use the cloud provider's workload identity/instance role.
- `BACKUP_S3_SERVER_SIDE_ENCRYPTION` accepts `AES256`, `aws:kms` or `provider-managed`; KMS mode requires a key ID.
