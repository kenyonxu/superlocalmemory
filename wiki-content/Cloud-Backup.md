# Cloud Backup

SLM keeps four different kinds of copy, and they answer different questions. Scheduled local backups are plain snapshots of your databases in `backups/`. Recovery backups are verified sets of every store plus your settings, made on demand from the dashboard, and the dashboard can restore from them. Cloud backup pushes the latest scheduled snapshot to GitHub or Google Drive, encrypted on your computer first. Restore points are the verified copies SLM takes before an update that changes the store, and they are restored from the command line. The sections below take them in that order, and the one rule that holds for all of them is that SLM checks a copy before it relies on it.

## Local backups

A background job takes timestamped snapshots with SQLite's `sqlite3.backup()` API, which produces a consistent copy of each file even while the daemon is running. They go to `backups/` in the data folder.

| Setting | Default | Notes |
|---|---|---|
| Interval | 168 hours (weekly) | Minimum 1 hour. The Settings pane offers Daily (24 h) or Weekly. |
| Copies kept | 10 | Oldest are pruned. |

You can take a plain snapshot on demand with `POST /api/backup/create`. The **Backup Now** button in Settings, group "Backup", and **Back up now** on the Backup & Cloud page make a recovery backup instead (see below). Settings is also where you switch auto-backup off.

Which files are copied is a fixed list called `MANAGED_DATABASES`: `memory.db`, `learning.db`, `audit_chain.db`, `code_graph.db`, `pending.db`, `audit.db` and `media.db`. Only the ones that exist on your machine are copied. Each is snapshotted on its own, one after another, so a set is not a single atomic point in time across stores. For a consistent whole-folder copy, run `slm serve stop` and copy the data folder while the daemon is stopped.

Local copies of pictures and documents are included: `media.db` goes into `backups/` as `media-<timestamp>.db`, and one copy of every original picture and PDF is kept in `backups/media-originals/` (only new files are copied each time). They are never uploaded anywhere. Local backup files are plain SQLite and are not encrypted, so keep the data folder on an encrypted disk. Restoring a media copy puts back any original that is missing and never overwrites a file that is there.

A scheduled snapshot is a single database file with no checksum manifest, so SLM does not restore it for you. The dashboard can check that one opens (**Verify database** in the backup history, which says the file is readable and that older snapshots have no complete recovery manifest). To use one, stop the daemon, copy the file back as its canonical name (for example `memory.db`) into a clean data folder, and run `slm restart`. For a restore SLM can do and check for you, use a recovery backup.

## Recovery backups and restoring from the dashboard

A recovery backup is a verified set: every store that exists on your computer, copied under one fence so they agree with each other, the vector index folder, your settings files (`config.json`, the three mode files, `backup_config.json` and `adapters.json`), and a copy of your picture and document originals. Every file has a checksum in a manifest, and SLM reads the whole set back and checks it right after making it and again before any restore. Make one from the dashboard with **Back up now** on the Backup & Cloud page, or with the **Backup Now** button in Settings (`POST /api/backup/recovery/create`). SLM keeps the newest 10 by default, the same count you set for backups in Settings.

What a recovery backup leaves out is stated in its own manifest: credentials, and downloaded models and runtime caches. Settings are saved without anything that looks like a secret (names ending in key, token, password and similar), and a web address loses any user name or password in it. The accounts, sessions and sign-in setting of company mode are never rolled back by a restore: whatever this installation has now is carried into the restored store, so a restore cannot bring back a removed user or switch sign-in off.

**To restore.** In the backup history, choose **Verify / restore** on a recovery backup. SLM verifies it and shows what it contains, what it excludes, and the warning that the service will stop and restart and that changes made since the backup may be lost. You then type `RESTORE` to enable the **Restore this backup** button. The restore is refused if another one is already in progress, or if the disk does not have the room it needs. That room is about three copies of your stores and vector index, plus the pictures and documents mirror and settings, because a restore holds the safety point, a plain pre-restore file copy and the backup system's own snapshot at the same time. The message states the megabytes needed and free, and says nothing was changed.

**What happens, in order.**

1. SLM verifies the backup and checks free space again.
2. The service stops and the restore waits for its process to exit.
3. A **safety copy** is taken: a fresh recovery backup of the current state, so the state just before the restore can always be put back. It appears in the same list.
4. A plain file copy of everything the restore may change is made. If the restore fails at any point after this, that copy is put back using only file copies, so a failure that repeats cannot make the rollback fail too.
5. The stores are restored, anything you erased since the backup is erased again (a restore never brings back erased data), settings are merged (a restored setting that points at a different provider or endpoint blanks the current secrets so they are not sent to somewhere new; the data folder location and ports are never restored), and only pictures and documents that the restored catalogue still lists are copied back.
6. The service starts again and the page reports the outcome. Reopen the Backup & Cloud page afterwards to see it.

**What the outcome says.** The history page shows the last recovery as one of `queued`, `stopping`, `restoring`, `restarting`, `complete` or `failed`, with a message. "Recovery could not finish, so nothing was changed: the stores, settings and files are as they were before" means the restore failed and was undone. "The disk is full, so the restore could not start and nothing was changed" and "Recovery could not start, so nothing was changed. Retry when other activity has finished." mean it never began. If the restore process itself dies, the status turns into a failure that names the log, `logs/backup-recovery.log` in the data folder, and the safe next step.

**Waiting versus recovering.** The two situations are told apart on purpose. While a restore is running, any attempt to start SLM says "A restore is replacing the memory store; start again when it finishes", and the hint is simply to wait; the restore starts the service itself. Only a restore that stopped part-way, or whose rollback also failed, says "The last restore stopped part-way and the stores may be mixed, so the service will not start on them", names the folder where the pre-restore copy is kept, and tells you to run:

```bash
slm backup restore-safety-copy     # put the safety copy back (service stopped)
slm serve start
```

`slm backup restore-safety-copy` takes a fresh recovery backup of the current state first, then restores the safety copy as one unit, and tells you the id of the state it kept. Pass `--id` to restore a different recovery backup. If you have repaired the stores yourself, `slm backup clear-recovery-needed` lets the service start again, and it deletes the pre-restore copy; use it only after a repair, because the safe way back is the safety copy. Until one of these has run, nothing starts on the mixed stores: the marker is written before the restore's first write, so a crash, a kill or a power cut still leaves every start refused. `slm backup status` repeats the message. When SLM is asked to do something while this is the case, hooks and commands report that memory is unavailable and name the recovery command rather than suggesting a retry. A safety copy is also protected from retention while a restore runs and after one has failed, so the copy that can repair it is never the one pruned.

**Export.** The dashboard's **Export** button downloads a recovery backup as one zip, `slm-recovery-<id>.zip`. It exports the newest kept backup, and only creates one if none exists yet, so exporting never pushes an older backup out of retention. The copy in the zip has accounts, sessions and the sign-in setting emptied out, and its manifest says so, because a downloaded file leaves your computer and a password hash in it would be something to crack offline.

All of these need installation-wide management permission, not just management of the current workspace, because a restore replaces every workspace's data.

## Cloud backup

When a cloud destination is connected, each scheduled backup is followed by a push of the latest set, and "Sync Now" pushes on demand. Both paths use `sync_all_destinations`. Pictures and documents are excluded by rule: any file whose name starts with `media-` is local only. That is a deliberate privacy choice, not a gap.

**GitHub.** Connect with a Personal Access Token with the `repo` scope, or sign in with GitHub in the dashboard. SLM creates a private repository named `slm-backup` (you can change the name) if it does not exist, and publishes each backup as a GitHub Release with the database files as assets. Only the last 5 releases are kept. If the repository already exists SLM reuses it without checking its visibility, so confirm an existing repository is private before connecting.

**Google Drive.** Google requires every app to register an OAuth client, so you supply your own client id and secret from Google Cloud Console and add the redirect URI `http://localhost:8765/api/backup/oauth/google/callback`. Files go into a `SLM-Backup` folder and are replaced in place, so there are no duplicates. Google Drive does not rotate old backups for you.

**Custom S3 or WebDAV.** The dashboard's Cloud Backup page lists a third row, "Custom S3 / WebDAV". It has no backend: there is no S3 or WebDAV route or module in the code, and its OAuth path does not exist. Treat it as not implemented.

Credentials go to your operating system's keychain (macOS Keychain, Windows Credential Locker, Linux Secret Service). Where no keychain exists, such as a headless server, they fall back to an owner-only file, `.credentials.json` in the data folder (mode 0600, directory 0700). That fallback file is not encrypted, so keep the data folder on an encrypted volume.

## Encryption

Every file is encrypted on your computer before upload, so GitHub and Google Drive only ever store ciphertext. A cloud backup carries the whole memory store, credentials included, which is why the encryption is not optional.

The format is AES-256-GCM over fixed-size chunks (the STREAM construction used by Tink and `age`). Each file gets its own key, derived with HKDF-SHA256 from your backup key and a random 96-bit nonce, and the header is authenticated, so reordering, splicing, truncation or appended data all fail to decrypt. Chunks are processed one at a time, so a multi-gigabyte database is never held in memory. Encrypted files end in `.slmenc`.

There is one 256-bit key per computer. It lives in the same credential store as your GitHub token, never beside the backup files, and SLM never replaces it silently. You see it as a recovery key that starts with `SLMBK1-`, base32 with a checksum so a typo is reported as a typo. If SLM has no key when a sync starts, it uploads nothing and says why.

```bash
slm backup status                   # encryption status and any notices
slm backup recovery-key             # show this computer's recovery key
slm backup recovery-key --import    # put a key on a new computer (read from stdin)
slm backup decrypt FILE -o OUT.db   # turn a download into a plain .db file
```

A recovery key is read only from stdin or a hidden prompt, never from the command line, so it stays out of shell history. Store it somewhere safe: without it a backup cannot be restored on another machine. Backups made before encryption still restore, and you should delete old plain-text ones from Google Drive yourself.

### Restoring from the cloud

1. Run `slm serve stop` so the write-ahead log is flushed before you replace files.
2. Download every database file from one timestamped run. Do not mix timestamps, and reject an incomplete set.
3. Run `slm backup decrypt FILE -o OUT.db` on each (on a new computer, import the recovery key first).
4. Quarantine the stopped data folder as an offline copy and restore into a clean folder, renaming each file to its canonical name: `memory.db`, `learning.db`, `audit_chain.db`, `code_graph.db`, `pending.db`, `audit.db`.
5. Check the files are owner-only (0600, folder 0700), then run `slm restart`.

Restoring into a clean folder matters because a stale `-wal` or `-shm` file beside a replaced database would replay against it. The cloud path does not upload `lance/` or write-ahead files, only the canonical databases.

## Restore points and downgrades

Before any update that changes the memory store, SLM takes a verified copy with a manifest and checksum. Those are restore points, and copies from the last two updates are kept. Like a recovery backup, and unlike a cloud backup, SLM can restore one for you, but only from the command line.

```bash
slm db restore-points                       # id, time, reason, versions, size, memory count
slm db restore <point_id> [--yes] [--no-reimport]
slm db restore --cancel                     # withdraw a restore that has not run yet
slm db prepare-downgrade [--yes]            # get the data ready for the previous SLM version
slm db prepare-downgrade --cancel
```

`slm db restore` shows a preview first, in counts and plain sentences: memories in the copy and now, those added since, those deleted or erased since, confirmed kinds that will be applied again, and learning records or corrections that cannot be carried over. It then asks you to type `RESTORE` (or pass `--yes`). It stops SLM, records the request and starts SLM again; the restore itself runs at start, before migrations, because restoring under a live engine is unsafe. If SLM cannot be stopped, the restore waits for the next start. After the copy is back, memories written after it are added again exactly once (`--no-reimport` skips that), anything you or a privacy erasure deleted stays deleted, kinds you confirmed are kept and erased profiles stay erased. A copy made by a newer version is refused, and a restore works even when the live store is damaged. Once it has started it cannot be cancelled half-way.

`slm db prepare-downgrade` exists because an older SLM refuses to open a store stamped by a newer one. It names the version to go back to and prints the install command. Preparation is allowed only when every change since that version is one the older build can safely ignore, and it takes a verified copy first.

The same actions exist over the local HTTP API under `/api/upgrade/` (`status`, `restore-points`, `restore/preview`, `restore`, `restore/cancel`, `prepare-downgrade`, `prepare-downgrade/cancel`). Changes need the install credential, MANAGE permission and a typed confirmation word, `RESTORE` or `DOWNGRADE`. Nothing in the dashboard calls these routes, even though the route's own docstring describes a dashboard card. Use the CLI. A repair started from the Health pane's "Memory store" card takes its own full backup first; see [Health and Repair](Health-and-Repair).

## Export and erasure

The dashboard's **Export** button downloads a recovery backup as a zip (see above). The older `POST /api/backup/export` route still builds one `.tar.gz` of the stores and the picture and document originals with a manifest of what is included and what is left out on purpose (`config.json` and identity secrets). Neither is a whole-folder copy. When you erase data with `slm gdpr erase`, SLM records the backup copies that may still contain it as "backup residue obligations", because it cannot reach into copies you hold elsewhere. See [Security, Company Mode and Teams](Security-Company-Mode-and-Teams).

Code: `infra/backup.py` (`BackupManager`, `MANAGED_DATABASES`), `infra/backup_media.py`, `infra/cloud_backup.py`, `infra/cloud_backup_github.py`, `infra/cloud_backup_crypto.py`, `infra/backup_crypto.py`, `infra/backup_keys.py`, `cli/backup_cmd.py`, `storage/upgrade_restore.py`, `cli/upgrade_cmd.py`, `server/routes/backup.py`, `server/routes/backup_encryption.py`, `server/routes/upgrade_restore.py`, `server/routes/backup_recovery.py`, `server/backup_bundle.py`, `server/backup_restore_worker.py`, `server/backup_restore_txn.py`, `server/backup_rollback.py`, `server/recovery_marker.py`, `server/recovery_inventory.py`, `infra/backup_credentials.py`, `ui/js/od-backup-recovery.js`.
