# Cloud Backup — Google Drive & GitHub

SuperLocalMemory can automatically back up your memory databases to
**Google Drive** and **GitHub**. Credentials are stored in your OS keychain
(macOS Keychain, Windows Credential Locker, or Linux Secret Service) when
available. On systems without a keychain (headless Linux, containers without
Secret Service) they fall back to an owner-only plaintext
`~/.superlocalmemory/.credentials.json` (mode `0600`, parent `0700`, written
atomically). That file is not encrypted, so protect it and the data root with
volume encryption and owner-only modes, and prefer a keychain-capable host when
possible.

Cloud backup is one of three things that go by "backup" here. **Local backups**
are taken on a schedule into `~/.superlocalmemory/backups/`. **Recovery points**
are whole-store copies you can verify and restore from the Backup page.
**Restore points** are the copies SLM takes before an update; they are covered in
[Restore points](restore-points.md).

## Encryption

Every cloud backup file is encrypted on this machine before upload (AES-256-GCM,
streamed in chunks), so GitHub and Google Drive only store ciphertext. One
256-bit key per machine encrypts every backup; it lives in the same credential
store as the GitHub token, never beside the backup files.

```bash
slm backup status                  # encryption status and any notices
slm backup recovery-key            # show this computer's recovery key (SLMBK1-...)
slm backup recovery-key --import   # put a recovery key on a new computer (read from stdin)
slm backup decrypt FILE -o OUT.db  # turn a downloaded backup into a plain .db file
```

Keep the recovery key somewhere safe: without it, a backup cannot be restored
on another machine. Old unencrypted backups still restore. GitHub rotates
releases out as encrypted ones replace them, but Google Drive does not rotate
old backups, so delete any plain-text backups made before encryption yourself.

## Pictures and PDFs are never uploaded

Cloud backup sends the stores listed below and nothing else. The image and document
library (`media.db`) and the original picture and PDF files under `media/` never leave
this computer: they are not in the GitHub release and not in Google Drive. That is a
deliberate choice, not a gap.

**Local** backups do keep them. Each local backup copies `media.db` into
`~/.superlocalmemory/backups/` as `media-<timestamp>.db` and keeps one copy of every
original in `backups/media-originals/` (only new files are copied each time, so the
extra space is about the size of your library once, and the size is written to the
log). Restoring `media-<timestamp>.db` puts back any original the `media/` folder is
missing and never overwrites a file that is there. An original you erase also leaves
the backup copy at the next backup. Recovery points and local backups share that same
`media-originals/` folder, so there is only one copy. With images and documents off or
never used, nothing is added. The copy is not encrypted; keep the data folder on an
encrypted disk. See [Pictures and documents](pictures-and-documents.md).

## GitHub Backup (Recommended)

GitHub backup works out of the box. No additional setup needed beyond a Personal Access Token.

### Setup (2 minutes)

1. Open the SLM Dashboard: `http://localhost:8765`
2. Click the **account widget** in the sidebar (bottom), then click the **GitHub icon**
3. You'll see the "Connect GitHub" form:

   - **Personal Access Token**: Click [Create one here](https://github.com/settings/tokens/new?scopes=repo&description=SLM+Backup) — this opens GitHub with the `repo` scope pre-selected. Click "Generate token" and copy it.
   - **Repository Name**: Default is `slm-backup`. Change if you want.

4. Click **Connect**

That's it. SLM will:
- Verify your token
- Create a **private** repository when the named repository does not exist
- Reuse an existing repository without changing or verifying its visibility;
  confirm that an existing backup repository is private before connecting
- Initialize it with a README
- Show your GitHub avatar and username in the sidebar

### How It Works

- Each GitHub backup is an independent per-file `memory-*.db` snapshot plus
  companion `learning-*.db` and the other stores when present, published as a
  **GitHub Release** with those files as assets. It is not a single consistent
  snapshot across stores; a failed upload of a companion is logged as non-critical
  and does not fail the primary `memory.db`.
- The stores sent are `memory.db`, `learning.db`, `audit_chain.db`,
  `code_graph.db`, `pending.db` and `audit.db`; only those present are sent.
  Raw evidence and ingestion records live in `memory.db`; `pending.db` is a
  legacy offline spool where present. `media.db` is kept locally and never
  uploaded, and the `lance/` vector index is not included (it is rebuilt from
  `memory.db`).
- Only the last **5 releases** are kept; older ones are deleted to prevent
  storage bloat.
- Backups run in the background, so the dashboard keeps responding.
- Snapshot files follow the process `umask`; verify `0600`/`0700` on the data
  root and any manual copy destination.

### Restoring from GitHub

1. `slm serve stop` — stop the daemon so WAL/SHM checkpoint before overwriting
   live files
2. Go to your `slm-backup` repo on GitHub
3. Click **Releases** in the sidebar
4. Download every database asset from one timestamped backup run. Compare the
   set with what you expect to have; cloud sync can report success when only some
   assets upload. Do not mix timestamps or proceed with a partial set.
5. Quarantine the complete stopped live data root as a separate offline copy
   and restore into a clean data root. This prevents stale live `-wal`/`-shm`
   sidecars from replaying against a replaced database. Rename each
   downloaded timestamped asset back to its canonical managed filename before
   replacement: `memory-<timestamp>-cloud-sync.db` → `memory.db`,
   `learning-...` → `learning.db`, `audit_chain-...` → `audit_chain.db`,
   `code_graph-...` → `code_graph.db`, `pending-...` → `pending.db`, and
   `audit-...` → `audit.db`. Cloud backup does not upload WAL/SHM files or
   `lance/`; use only the renamed canonical database assets in the clean root.
   Each downloaded file is encrypted, so turn it into a plain `.db` first with
   `slm backup decrypt FILE -o OUT.db` (see Encryption above).
6. Verify owner-only (`0600`/`0700`) permissions on an encrypted/private volume,
   then run `slm restart`. Restoring a cloud download is an offline file
   replacement; the dashboard's one-click restore works on recovery points, not
   on cloud downloads.

---

## Google Drive Backup

Google Drive backup requires a one-time OAuth client setup through Google Cloud Console. This is a Google requirement for any application that accesses Drive on behalf of users.

### Why Is This Needed?

Google requires every application to register an "OAuth client" before it can access your Drive. This is a security measure — it ensures you know exactly which application is accessing your data. For GitHub, a simple Personal Access Token is enough, but Google's security model is stricter.

### Setup (5 minutes)

#### Step 1: Create a Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Click the project dropdown (top bar) → **New Project**
3. Name it anything (e.g., `slm-backup`) → **Create**
4. Select the new project from the dropdown

#### Step 2: Enable APIs

1. Go to **APIs & Services** → **Library**
2. Search for and enable:
   - **Google Drive API**
   - **People API** (for showing your email/name)

#### Step 3: Configure OAuth Consent Screen

1. Go to **APIs & Services** → **OAuth consent screen**
2. Select **External** → **Create**
3. Fill in:
   - **App name**: `SuperLocalMemory` (or anything)
   - **User support email**: your Gmail
   - **Developer contact email**: your Gmail
4. Click **Save and Continue** through the remaining steps
5. Go to **Test users** → **Add users** → add your Gmail address

#### Step 4: Create OAuth Client

1. Go to **APIs & Services** → **Credentials**
2. Click **Create Credentials** → **OAuth client ID**
3. Application type: **Web application**
4. Name: `SLM Dashboard` (or anything)
5. Under **Authorized redirect URIs**, add:
   ```
   http://localhost:8765/api/backup/oauth/google/callback
   ```
6. Click **Create**
7. Copy the **Client ID** and **Client Secret** (you'll need both)

#### Step 5: Connect in SLM

1. Open the SLM Dashboard: `http://localhost:8765`
2. Click the **Google icon** in the sidebar account widget
3. Paste your **Client ID** and **Client Secret**
4. Click **Save & Connect Google Drive**
5. Google's login page opens — sign in and click **Allow**
6. You'll see "Google Drive Connected!" — close the popup

### How It Works

- Backups are uploaded as independent per-file snapshots to a `SLM-Backup`
  folder in your Google Drive; files are **replaced in place** (no duplicates).
- Included: `memory.db`, `learning.db`, `audit_chain.db`, `code_graph.db`,
  `pending.db`, `audit.db`; only present files are sent. `media.db` and
  `lance/` are not uploaded. The set is not one consistent snapshot across
  stores. A failed companion upload is logged as non-critical and does not fail
  the primary.
- OAuth credentials are stored in your OS keychain when available; otherwise
  the owner-only plaintext `.credentials.json` described above is used.
- Backups run in the background; resulting snapshot files follow the process
  `umask`, so verify `0600`/`0700` and keep the destination private.

### Restoring from Google Drive

1. `slm serve stop`
2. Open Google Drive → `SLM-Backup` folder
3. Download the complete timestamped set from one run and reject an incomplete
   or mixed-timestamp set. Cloud backup does not include `lance/`.
4. Decrypt each file with `slm backup decrypt FILE -o OUT.db`. Quarantine the
   stopped live root and restore into a clean data root so stale WAL/SHM
   sidecars cannot replay. Rename each timestamped asset to its canonical
   filename (`memory.db`, `learning.db`, `audit_chain.db`, `code_graph.db`,
   `pending.db`, `audit.db`) before replacement. Install only the renamed
   canonical database assets.
5. Verify owner-only modes on an encrypted/private volume, and run
   `slm restart`. Offline replacement is the supported path for cloud
   downloads.

---

## Sync & Schedule

### Manual Sync

Click **Sync Now** (cloud upload icon) in the sidebar account widget, or go to **Settings** → **Cloud Backup** → **Sync Now**.

### Auto-Backup

SLM automatically creates local backups on a schedule (default: weekly). When cloud destinations are connected, backups are also pushed to the cloud after each auto-backup.

Configure the schedule in **Settings** → **Backup Configuration**:
- **Interval**: Daily or Weekly
- **Max backups**: How many local backups to keep (default: 10). The same number caps how many recovery points are kept.
- **Auto-backup enabled**: a switch for the schedule. **Backup Now** takes one immediately.

### Export

Click **Export backup (.tar.gz)** on the Backup page. This is
`POST /api/backup/export`: one archive holding the coordinated backup set (every
managed store, copied under one fence, with its checksum manifest), the picture
and document originals, and `EXPORT-MANIFEST.json`, which lists what is inside and
what is left out on purpose. Every checksum is read back from the finished archive
before the download is offered. `config.json` (it can hold provider API keys) and
the identity and access secrets are never bundled; copy `config.json` yourself if
you want your settings. The archive is a temporary file, removed after the
response, created readable by you only. User accounts, sign-in sessions
and the sign-in setting are removed from the exported stores, and upload
links are never in a backup at all: password and token hashes never leave the
computer. There is no one-click
restore of this archive: unpack it and restore the stores with the daemon
stopped.

### Recovery points and restore

The Backup page also keeps verified recovery points. Each one holds every
store, your settings with secrets removed (provider keys, tokens and the like are
never copied) and the picture and document originals. Credentials and downloaded
models are left out. Creating, verifying, exporting and restoring a point acts on
every workspace at once, so in company mode only the computer's owner or an
administrator of every workspace can use them.

To restore, press **Verify / restore** on a point. SLM re-reads every file
against its checksum and shows what the point includes and leaves out. You then
type `RESTORE` to confirm. Nothing is replaced before that, and a point that
fails verification is refused with "Backup verification failed. Nothing was
restored; create a new recovery backup." If the disk cannot hold the copies the
restore needs, it says how many MB are needed and changes nothing.

After you confirm, the restore stops the service, holds the store so nothing
else can write to it, and takes a **safety point** of the current state. It
then copies the stores, settings and vector index as plain files, restores, and
starts the service again. It is all or nothing: if any step fails, those plain
copies are put back without running the restore code again, and the page says
"Recovery could not finish, so nothing was changed: the stores, settings and
files are as they were before." Accounts, sessions and the sign-in setting are
never rolled back; the ones you have now are kept. Erasures you made since the
point are replayed, so erased data does not come back.

If even the rollback fails, or the restore process is cut off while writing, the
service stays stopped: every start, including hooks and `slm serve start`,
refuses with a message that the last restore stopped part-way and the stores may
be mixed. The message names where the pre-restore copy is kept. To recover:

```bash
slm backup restore-safety-copy   # puts back the safety copy of the last restore
slm serve start
```

`--id <recovery point>` restores a specific point instead. The command first
takes a new point of the current state, so it can itself be undone. If the
service is running it refuses ("SuperLocalMemory is running. Stop it with
`slm serve stop`, then retry."), and if the disk is too full it refuses before
changing anything. Only if you have repaired the stores another way does
`slm backup clear-recovery-needed` let the service start again; it also removes
the pre-restore copy it kept. `slm backup status` reports a restore that is
waiting for recovery. The restore worker's log is `logs/backup-recovery.log` in
the data directory.

---

## What Gets Backed Up

| Database | Contents | Local backup | Cloud backup |
|---|---|---|---|
| `memory.db` | Facts, raw evidence, entities, graph edges, embeddings, sessions | Always if present (primary) | Yes |
| `learning.db` | Learning signals, behavioral patterns, ranker data | If present | Yes |
| `audit_chain.db` | Audit trail, compliance provenance | If present | Yes |
| `code_graph.db` | Code knowledge graph (if used) | If present | Yes |
| `pending.db` | Legacy offline spool (when present) | If present | Yes |
| `audit.db` | Legacy audit database from older installs | If present | Yes |
| `media.db` | Picture and document library records | If present, as `media-<timestamp>.db`, with the originals in `media-originals/` | Never |

Scheduled and cloud-sync backups take independent per-file SQLite snapshots,
one file at a time. Each snapshot is consistent for that file even while the
service runs, but there is no single point in time across stores and no atomic
rollback across them. The `lance/` vector index is not part of those backups;
recovery points do capture it. Destination files follow the process `umask`;
verify `0600`/`0700` after copy. For an offline whole-folder copy, run
`slm serve stop` first so the write-ahead logs are checkpointed, then copy the
complete set of store files.

---

## Security

- **New GitHub backup repositories are created private.** Existing repositories
  are reused without a visibility check or enforcement, so verify the selected
  repository is private before uploading any memory database.
- **Credentials: OS keychain preferred.** macOS Keychain, Windows Credential
  Locker, or Linux Secret Service when available. **Fallback:** owner-only
  plaintext `~/.superlocalmemory/.credentials.json` (`0600`, parent `0700`,
  written atomically) on systems without a keychain (headless Linux,
  containers). It is not encrypted. Keep that file on an encrypted/private
  volume and verify `0600`/`0700` after writes. Provider and reranker keys
  persisted in `~/.superlocalmemory/config.json` are likewise plaintext
  protected only by `0600`; prefer environment variables (`OPENAI_API_KEY`,
  `SLM_CROSS_ENCODER_API_KEY`) to avoid keeping them on disk.
- **Google OAuth tokens** are refresh tokens — they can be revoked from your [Google Account Security page](https://myaccount.google.com/permissions)
- **GitHub PATs** can be revoked from [GitHub Settings → Tokens](https://github.com/settings/tokens)
- **Backup destination permissions:** snapshot files follow process `umask`,
  not `0600` inheritance — verify owner-only modes. Use an encrypted/private
  destination for backups and exports (see
  [encryption at rest](SECURITY-encryption-at-rest.md)). A cloud backup is
  restored by downloading it, decrypting it with `slm backup decrypt`, and
  copying it back with the daemon stopped. For the local copies SLM takes before
  an update, use `slm db restore` (see [Restore points](restore-points.md)).
- **Erasure and backups:** a privacy erasure cleans the live stores at once, but
  older backups still hold the erased data until they rotate out. SLM keeps a
  ledger of such backups so the erasure receipt does not claim completeness while
  one exists, and a restore replays the erasure. See [Compliance](compliance.md).
- **No zero-loss claim for per-file copies:** they have no single point in time
  across stores; an offline copy of the whole folder while stopped is the
  consistent set.

---

## Troubleshooting

### "Sync failed" in the sidebar
Check the destination status in **Settings** → **Cloud Backup**. Common causes:
- GitHub: PAT expired or revoked → reconnect with a new token
- Google: OAuth token expired → click "Connect Google Drive" again to re-authorize

### Google Drive shows "Connection Failed"
- Make sure you added yourself as a **test user** in the OAuth consent screen
- Verify the redirect URI matches exactly: `http://localhost:8765/api/backup/oauth/google/callback`
- Check that the SLM daemon is running on port 8765

### Dashboard freezes during sync
Syncs run in a background thread and should not block the dashboard. If one does,
update SLM (`npm update -g superlocalmemory`, or inside the activated virtual
environment `python -m pip install --upgrade superlocalmemory`) and report it.

### The service will not start after a restore
If the message says the last restore stopped part-way and the stores may be mixed,
a restore was cut off or could not roll back. Run `slm backup restore-safety-copy`
and then `slm serve start`. If the message says a restore is replacing the memory
store, one is still running; wait for it to finish.

### "SuperLocalMemory is running" from a backup command
`slm backup restore-safety-copy` needs the service stopped. Run `slm serve stop`
and retry.

### Windows
Backup configuration and manifests are read and written as UTF-8, and the `slm`
command line writes UTF-8 when its output goes to a pipe or a file, so
`slm backup status > out.txt` no longer fails on a legacy code page.
