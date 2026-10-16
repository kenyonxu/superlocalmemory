# Ingestion Adapters

Ingestion adapters pull outside material into memory without you typing `remember`: a Gmail export, a calendar file, or a folder of meeting transcripts. There are three of them (`gmail`, `calendar`, `transcript`), all off until you enable them, and each runs as its own small background process that hands items to the local SLM daemon. Three of the paths work today with nothing but file placement: a Google Takeout `.mbox`, an `.ics` calendar file, and a transcript folder. The live-account paths (Gmail over IMAP or the Gmail API, Google Calendar over the API) exist in code but SLM does not ship the sign-in step that stores their credentials, so treat them as not usable out of the box. The sections below say exactly where that line is.

## How an adapter works

An adapter is a separate Python process (`python -m superlocalmemory.ingestion.<name>_adapter`) started by the adapter manager. It writes a PID file named `adapter-<name>.pid` in the data root, logs to `logs/adapter-<name>.log`, and watches its parent process so it exits if the daemon that started it goes away. It never writes to the database itself. Each item it finds is sent to the daemon as `POST /ingest` with the text, a source type, and a dedup key, and the daemon stores it through the same write path as `remember`.

That design keeps third-party parsing and network code out of the daemon process: a bad mbox or a hung IMAP call can only take down the adapter. The daemon deduplicates on the pair (source type, dedup key), so running an adapter twice over the same export does not create duplicate memories. The daemon also caps one ingest call at 1,000,000 characters and allows at most 10 ingestions in flight, answering HTTP 429 with a retry hint beyond that; the adapter waits five seconds and retries once. Roles apply too: the ingest route requires write permission on the active profile, so in a company-mode workspace a viewer cannot ingest.

Adapters pace themselves. By default an adapter sends batches of 50 items with a 5-second pause between batches and allows 100 items per hour. Reading the loop in `base_adapter.py`, an item that arrives while the hourly limit is reached is skipped after a 60-second wait rather than queued for later. A large one-shot import therefore does not finish in one run. Because the daemon deduplicates, you can stop and start the adapter again to continue; items already stored are recognised and skipped without counting against the limit. This is a code reading, not something measured on a large mailbox.

## The three adapters

| Adapter | Source | Network | Runs |
|---|---|---|---|
| `gmail`, tier `mbox` | A Google Takeout `.mbox` file | None | Once, then the adapter stops |
| `gmail`, tier `imap` | Your Gmail inbox over IMAP (`imap.gmail.com` unless `imap_host` is set) | Yes, to Gmail | Polls every 300 s, newest 20 unseen messages |
| `gmail`, tier `oauth` | Gmail API with your own Google Cloud OAuth client | Yes, to Google | Polls every 300 s; first pass takes the latest 20 messages, then only new ones |
| `calendar`, tier `ics` | An `.ics` file | None | Once, then the adapter stops |
| `calendar`, tier `oauth` | Google Calendar API (primary calendar), sharing the Gmail OAuth credentials | Yes, to Google | Polls every 900 s; first pass looks back 30 days |
| `transcript` | A local folder of `.srt`, `.vtt` and `.txt` files | None | Continuous file watch |

Only the `mbox`, `ics` and `transcript` rows are fully local. The other three open a connection to Google from your computer, so they sit outside the "nothing leaves this device" promise that holds for the local rows; what they fetch then follows the normal memory pipeline, including the Mode B or C model if you configured one (see [Privacy and Data Flow](Privacy-and-Data-Flow)).

### Gmail

The Gmail adapter picks its tier when it starts, in this order. If `adapters.json` names `"tier": "mbox"` or gives an `mbox_path`, it uses that file. Otherwise `"tier": "imap"` selects IMAP. Otherwise, if a `refresh_token` for Gmail is found in the credential store, it uses the Gmail API. Failing all of those it takes the first `*.mbox` it finds in the `import` folder of the data root, and if there is none it logs a warning and does nothing.

Each email becomes one item whose text is the subject, sender, date and the first `text/plain` part of the body cut to 3,000 characters. The dedup key is the message id, so the same mail is never stored twice. Attachments and HTML-only bodies are not read.

### Calendar

Each event becomes one item holding the title, start and end time, location, and a description cut to 2,000 characters, plus an attendees line. Attendees are reduced to the part of the address before the `@` (or the display name in the API path), and each attendee also gets a short "attended meeting" item so the person shows up as an entity with a timeline. The dedup key is the event id plus its start time or last-updated stamp, so an edited event is stored again as an update.

Tier choice mirrors Gmail: `"tier": "ics"` or an `ics_path` selects the file, a Gmail `refresh_token` in the credential store selects the API, and otherwise the first `*.ics` in the `import` folder is used. If both an `.ics` file and a stored Gmail token exist, the token wins. The `.ics` path uses the `icalendar` library when it is installed and a much smaller fallback parser (title and start time only) when it is not.

### Meeting transcripts

The transcript adapter watches one folder, not its subfolders, for `.srt`, `.vtt` and `.txt` files. On start it ingests the files already there, and afterwards it picks up files created while it runs. Files that are edited or moved into place after creation are not re-read. For `.srt` and `.vtt` it extracts `Speaker: text` prefixes and `<v Speaker>` tags into speaker names; plain `.txt` files are taken whole with no speaker detection. The combined text is cut to 5,000 characters. The dedup key is a SHA-256 of the file content, so renaming a file does not store it again, and every detected speaker gets a short participation item linked to the file name.

## Configuration

Adapter settings live in `adapters.json` in the data root (`~/.superlocalmemory/` by default). The CLI and the dashboard change only the `enabled` flag. Everything else you edit by hand:

```json
{
  "gmail":      { "enabled": true, "tier": "mbox", "mbox_path": "/path/to/All-mail.mbox" },
  "calendar":   { "enabled": true, "tier": "ics",  "ics_path":  "/path/to/calendar.ics" },
  "transcript": { "enabled": true, "watch_dir": "/path/to/transcripts" }
}
```

The transcript adapter has no default folder. If `watch_dir` is missing the adapter prints "No watch_dir configured. Set it in ~/.superlocalmemory/adapters.json" and exits. The `import` folder used for automatic mbox and ics discovery is not created for you; make `~/.superlocalmemory/import/` yourself if you want to drop files there instead of setting a path.

The `keyring` and `watchdog` packages are base dependencies. The Google client libraries and `icalendar` come with the optional `ingestion` extra (also part of `full`): `pip install 'superlocalmemory[ingestion]'`. Without the extra, the OAuth tiers log a warning and return nothing, and the `.ics` path falls back to the small parser.

## Commands

```
slm adapters list                     # gmail / calendar / transcript: disabled, enabled or running
slm adapters enable gmail             # flips "enabled" in adapters.json, does not start anything
slm adapters start gmail              # launches the background process
slm adapters status                   # same data, with PIDs
slm adapters stop gmail
slm adapters disable gmail            # stops it and sets enabled false
```

Enable and start are separate steps on purpose: enabling records consent, starting spends CPU and, for the live tiers, network. The same operations are available over HTTP as `GET /api/adapters` and `POST /api/adapters/enable|disable|start|stop` with a body of `{"name": "gmail"}`. Logs for troubleshooting are in `logs/adapter-<name>.log`.

## The dashboard tab

Governance > Ingestion shows one card per adapter with a status badge (Disabled, Enabled (Stopped), Running) and Enable, Start, Stop and Disable buttons, plus the PID while running. It has no fields for paths or credentials, so it cannot point an adapter at a file; set paths in `adapters.json` first. The "Ingestion log" area below the cards currently shows a fixed placeholder sentence, not a real log. See [Governance](Governance) for the rest of the pane and [Dashboard Tour](Dashboard-Tour) for the whole dashboard.

## What does not work yet

Nothing in the repository performs the Google sign-in (OAuth consent) for Gmail or Calendar, and nothing stores the resulting `client_id`, `client_secret` and `refresh_token`, or an IMAP email and password. The credential helper can read them (OS keychain through `keyring`, falling back to a 0600 file under `credentials/` in the data root), but no SLM command, wizard step or dashboard control writes the Gmail entries. Three pieces of text in the product suggest otherwise and are wrong or unfinished:

- The IMAP warning says `Run: slm adapters enable gmail --setup`. There is no `--setup` flag; `slm adapters` takes only the actions listed above.
- The dashboard Gmail card says "SLM will guide you through OAuth setup". No such guide exists.
- The dashboard Calendar card says it "polls for new events hourly and processes updates in real-time via webhooks". The code polls every 15 minutes and has no webhook receiver. The same card says attendee details are not stored; the code does store attendee names.

What to do today: use the Takeout `.mbox` route for Gmail, an exported `.ics` for Calendar, and the transcript folder for meetings. Those need no account access at all. If you need continuous Gmail or Calendar sync, you would have to create the Google Cloud OAuth client and store the credentials under the keychain service names the code reads yourself; SLM does not provide a supported way to do this today.

## Where to look when it does nothing

If `slm adapters list` shows `enabled` but never `running`, run `slm adapters start <name>` and read `logs/adapter-<name>.log`. The common messages are "MBOX file not found" (wrong `mbox_path`), "No Gmail credentials or MBOX file found", "Watch directory does not exist", and "Gmail OAuth requires: pip install 'superlocalmemory[ingestion]'". If an import "finished" with fewer memories than messages, check the hourly limit described above and start the adapter again. Daemon problems are covered in [Troubleshooting](Troubleshooting).

Code: `src/superlocalmemory/ingestion/` (`adapter_manager.py`, `base_adapter.py`, `gmail_adapter.py`, `calendar_adapter.py`, `transcript_adapter.py`, `parsers.py`, `credentials.py`), `server/routes/ingest.py`, `server/routes/adapters.py`, `ui/js/ng-ingestion.js`.
