# Offline backup and recovery

Stop the service before making a backup. Only one process may own a state directory. Create a NEW private backup directory; existing backups and state directories are never overwritten.

```sh
mkdir -m 700 backups
python3 -S -B -m eidos.manage backup --config local/config.json --output backups/state-001.eidos
```

Record `backup_sha256` and the audit head from this command in a separately trusted location. A checksum supplied from the same untrusted backup cannot establish freshness. Keep the original audit key/configuration separately in operator-controlled secure storage: neither configuration, operator keys, audit key nor model artifacts are included in the snapshot. Do not print or put those keys in a shell argument.

The snapshot is plaintext sensitive state protected by private file permissions, a whole-database SHA256 and HMAC-authenticated metadata using the existing audit key. It is not encrypted. An administrator with that key can forge a snapshot. The existing audit chain checks retained events; it does not retroactively authenticate every database cell before capture, detect a missing tail without an independently retained head, or supply a remote timestamp. A trusted, separately retained exact backup checksum identifies the intended snapshot and prevents silently substituting an older valid backup.

Use a NEW state directory beneath an owned mode-0700 parent. Stop related services; the original configuration is read for its audit key and is not changed. The command starts no service:

```sh
python3 -S -B -m eidos.manage restore --config local/config.json --backup backups/state-001.eidos --state-dir local/restored-state --sha256 CHECKSUM_RECORDED_WHEN_BACKUP_WAS_CREATED
```

Replace the final placeholder with the exact independently retained 64-character checksum. Authentication, complete checksum verification, SQLite integrity/schema/record-budget checks and audit-chain/head verification occur before state is published. Malformed images, wrong keys/checksums, symlinks, nonregular inputs, public backup permissions, existing destinations and unsupported schema objects are refused. Database images are bounded to 128 MiB; no network or target code is executed. Files are private, copied consistently using SQLite's backup API, synced and exclusively published. A restored snapshot does not overwrite or reconfigure the source state.

Create a separate private operator configuration pointing `state_dir` at the restored directory, preserving the correct audit key, matching tenant/release policy and reviewed release artifacts. Model files and configuration are separate recovery prerequisites. Do not run source and restored copies as simultaneous replicas: this is offline disaster recovery, not coordinated failover. Starting the service normally clears interrupted unfinished requests; completed responses, reviews, active releases, audit chain and still-live replay nonces are preserved. Requests within the retained idempotency window return the original response. Expired retention and nonce windows retain their existing semantics.

Validate recovery with a signed inventory request, the retained audit head and authorized application requests before directing work to the restored instance. Stop service before `maintain`. This feature supplies an exercised recovery mechanism; encrypted/offsite storage, key escrow/rotation, operational retention/freshness policy and recovery-time objectives remain operator deployment work.

The classifier, inference engine, model, browser and aggregate BANKING77 scores are preserved. `manage.py` adds two offline commands and `recovery.py` is new. This release does not extend the known 77-intent inventory or establish a new classifier-quality result. The original evaluated archive remains available unchanged.
