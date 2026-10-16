# Backup and Restore

Your memory lives in the data folder (`~/.superlocalmemory` by default). SuperLocalMemory protects it in four layers: local backups, restore points taken before an update changes the store, optional encrypted cloud backup, and an export you can read.

## Local backups

SLM creates local backups on a schedule (weekly by default), in the data folder's `backups/` directory. Change the interval (daily or weekly) and how many to keep (default 10) in the dashboard under **Settings**, **Backup Configuration**. Each local backup also keeps one copy of every picture and PDF original, so the extra space is about the size of your library once ([[Pictures and Documents]]). The copy is not encrypted, so keep the data folder on an encrypted disk.

The dashboard's **Export backup (.tar.gz)** button writes one archive with every managed store, the picture originals and a manifest that says what is inside and what was left out on purpose. It never includes `config.json`, which can hold provider keys; copy that yourself if you want your settings. There is no one-click restore from the archive: unpack it and put the stores back with the daemon stopped (`slm serve stop`).

## Restore points

Every update that changes the memory store first takes a verified copy; copies from the last two updates are kept.

```bash
slm db restore-points        # id, date, reason, versions, size, memory count
slm db restore <point_id>    # recorded now, applied at the next start
slm restart
```

Memories saved after the restore point are added back unless you pass `--no-reimport`. The restore deletes again only what you, or a privacy erasure, deleted after the copy, and erased profiles stay erased. `slm db restore --cancel` withdraws a restore that has not run yet.

To go back to the previous SLM version, run `slm db prepare-downgrade`. It takes a verified copy, names the version, and prints the install command. `slm db prepare-downgrade --cancel` undoes the preparation.

## Cloud backup (optional)

Google Drive and GitHub backups are encrypted on your computer before upload (AES-256-GCM), so the services only store ciphertext. One key per computer lives in your system keychain. Keep the recovery key somewhere safe: without it a backup cannot be restored on another computer.

```bash
slm backup status                    # encryption status and any notices
slm backup recovery-key              # show this computer's recovery key
slm backup recovery-key --import     # put a recovery key on a new computer (read from stdin)
slm backup decrypt FILE -o OUT.db    # turn a downloaded backup into a plain .db file
```

Pictures and PDFs are never uploaded; only the stores listed in [docs/cloud-backup.md](https://github.com/qualixar/superlocalmemory/blob/main/docs/cloud-backup.md) are sent, and that page also has the GitHub and Google Drive set-up and restore steps.

## Export and erasure

```bash
slm gdpr export --profile work --output work-export.json   # plain JSON; add --include-embeddings for vectors
slm gdpr erase --profile work --dry-run                    # preview
slm gdpr erase --profile work --yes                        # irreversible
slm gdpr verify --receipt-id <id>
```

Exports leave embeddings out unless you ask for them, stream to disk, and list what they leave out and why under `export_manifest`. An erasure says exactly what it removed and what remains: `--json` reports `success: false` if the erasure was incomplete. A backup keeps its erasure obligation until it is deleted or re-read clean, and a restore replays targeted erasures as well as profile ones. See [[GDPR Compliance]].

## If the store itself is damaged

Run `slm db integrity` (read-only, safe while SLM runs; add `--pages` to read every page). The dashboard's **Memory store** card under **Health** shows the same check, and **Repair now** takes a full backup copy first, then repairs. On a damaged keyword index (SQLite versions `3.44.0` to `3.46.0`), `slm db repair --apply` also turns off the SQLite setting that caused it. See [[Troubleshooting]].
