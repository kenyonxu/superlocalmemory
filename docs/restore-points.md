# Restore Points and Downgrades

Every update that changes the memory store first takes a verified copy. Those
copies are restore points. They live in `pre-migration-snapshots/` inside your
data directory, and the copies from the last two updates are kept.

## List them

```bash
slm db restore-points
```

Each line shows the point id, when it was taken, why (for example
`migration`), the versions involved, its size and how many memories it holds.
A copy made before checksums were recorded is marked as an older copy, and a
copy made by a newer version is marked as one this version cannot restore.

## Go back to one

```bash
slm db restore <point_id>
```

The command first prints what the restore will do: how many memories the copy
holds and how many exist now, how many were added since (they are kept aside
and added back afterwards), how many were deleted or erased since (they stay
deleted or erased), and any learning records or open corrections that cannot be
carried across. You then type `RESTORE` to confirm, or pass `--yes` to skip the
prompt. Nothing changes until you confirm.

After you confirm, SLM stops the running service, restores, and starts it
again. If it cannot stop the service, the restore stays recorded and runs at
the next start, before anything opens the store. A restore never runs under a
live engine.

- Memories saved after the restore point are added back, unless you pass
  `--no-reimport`.
- It deletes again only what you, or a privacy erasure, deleted after the copy.
  An erased profile stays erased, in the learning data as well.
- Kinds you confirmed after the copy are kept.
- It refuses a copy made by a newer version, or a copy whose checksum no longer
  matches, and changes nothing.
- It works when the live store is damaged. Nothing can be carried across from a
  store SQLite cannot read, so the restore is a full one, and the preview says
  so before you confirm.
- `slm db restore --cancel` withdraws a restore that has not started writing.
  Once it is part-way through it cannot be cancelled; restart SLM and it
  finishes.

### The safety copy

Before a restore replaces anything, SLM copies the store as it is now into the
`pre-restore/` folder of your data directory. If the store was damaged, the
damaged files are kept there byte for byte. The result message names this
folder. If the memory store was replaced but the learning data could not be,
the memory store is put back exactly as it was and the outcome says the restore
failed, with the details in the log.

The newest two safety copies are kept however old they are; older ones are
removed after 30 days. Because a safety copy holds a person's words, a privacy
erasure registers every copy that still holds the erased profile, and a later
restore erases that profile again. See [Compliance](compliance.md).

### Disk space

A restore needs room for a staging copy of the point plus the safety copies. If
the disk is too full, the command says so and changes nothing.

## Go back to the previous version

An older SLM refuses to open a store stamped by a newer one. To install the
previous release without losing anything:

```bash
slm db prepare-downgrade
# it names the version to go back to and prints the exact command, for example:
pipx install --force "superlocalmemory==<version>"   # or: npm install -g superlocalmemory@<version>
```

You confirm by typing `DOWNGRADE` (or pass `--yes`). Preparation is allowed
only when every change since that version is one the older build safely
ignores; otherwise it refuses and says why. It takes a verified copy first. The
preparation holds across restarts of the current version, so an automatic
restart cannot undo it. Undo it with `slm db prepare-downgrade --cancel`; it
takes effect at the next start.

## HTTP API

The same actions are available over the local HTTP API under `/api/upgrade/`
(`status`, `restore-points`, `restore/preview`, `restore`, `restore/cancel`,
`prepare-downgrade`, `prepare-downgrade/cancel`). A restore requested over HTTP
needs the confirmation word `RESTORE` in the request body, records the request
and restarts the service, which performs it. The dashboard does not have a card
for these restore points; use the command line or the API.

For copies kept off this machine, and for the whole-store recovery points on
the Backup page, see [Cloud backup](cloud-backup.md). A repair started from the
dashboard's **Memory store** card takes its own full backup copy first; see
[Troubleshooting](troubleshooting.md#memory-store-check).
