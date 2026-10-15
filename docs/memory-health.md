# Memory health

Memories you saved a while ago were described with whatever SLM could extract at the
time: some have no event date, no names, no tags and no kind. Memory health fills
those gaps in the background, while your computer is idle, and offers repairs that
need your say-so. It is on by default and needs no configuration.

The one rule that holds everywhere on this page: **the background worker only adds.**
It never changes what a memory says, never replaces a value you or an agent set, and
never deletes or moves anything. Anything that deletes or moves something waits for you
to press **Fix**, and almost all of it can be undone.

## What the background worker adds

For each older memory it can fill in, when the field is empty:

- the **observation date** (when the memory was recorded, labelled as inferred) and the
  **event date** when the text itself gives one ("on 14 March 2026"). A save time is never
  turned into an event start, so asking what was true on an earlier date gives the same
  answer before and after;
- the **names** of people, projects and tools the memory mentions, found by the same
  rules new saves use, so a name that fails those rules (a timestamp, a hash, a status
  word) is not stored;
- **tags** written as `#tag` in the text, or the names found above, and a **project** when
  the text says one ("Orchard is our project"), or when every other memory in the same
  session agrees;
- a **kind** (fact, decision, rule and so on), by rule offline, and by a model on this
  computer when one is running and your kind settings allow it. A model on another
  computer, and an Ollama cloud model, are never used for this. A kind you or an agent
  confirmed is never replaced;
- a **stub mark** on operational receipts, described below.

It runs only while nothing else is using your memories, only within the memory budget,
and it stops at once when you start working. A save, a recall or an edit always wins.
Before its first change SLM takes **one safety copy** of the store (reused by every later
run, so the disk does not fill with copies), and every run is recorded in a ledger that
can put it back.

## Stubs

A memory that is only a label and an id, such as "Session ID: 9f8e7d6c5b4a" or "session
ended 2026-10-12 11:28", answers nothing. SLM marks it **stub**. Stubs stay in `slm list`,
the dashboard list, counts and exports, each marked as a stub; only **recall** leaves them
out of its answers. The rule is narrow on purpose: a label plus a machine-shaped id
(eight or more hex digits containing a letter from a to f, twelve or more digits, a UUID or
a ULID), or dated session boilerplate. A receipt number or a date stamp alone is not a stub.

If a stub is real, edit it (`slm update`, the dashboard, or the `update_memory` tool). The
mark is cleared for good once the edit is applied; a proposed edit that is rejected leaves
it as it was.

## Repairs that wait for you

Three repairs change or remove something, so the worker only counts them and shows a
suggestion:

| Repair | What it fixes |
|---|---|
| `entity-links` | Duplicate aliases and junk entities left by older name-matching rules |
| `refile-hidden` | Memories moved out of sight (archive or forgotten) although their retention score says to keep them |
| `repair-saves` | Saves that were given up on after repeated attempts and may lack entities or graph links |

A fourth, rebuilding a search sketch from the stored vector, only adds, so the worker runs
it on its own, in small batches.

Press **Fix** on the card, or run `slm enrich fix <repair>`. Fix asks first. Every run that
changed something can be undone: **Undo the last change** on the card, or `slm enrich undo`.
An undo puts back exactly what the run changed and keeps, and counts, anything you edited
since. `repair-saves` is the exception: it keeps no undo record, so it says so before it
runs and takes the safety copy first.

An undone run is final until you say otherwise. The repair is not suggested again, and
the items the run touched stay out of every suggestion, until you ask for them with
`slm enrich fix <repair> --include-undone`.

## The dashboard card

The **Memory health** card is at the top of the **Health** pane. It shows how far the
improvement has got ("Improving your memories: 25 of 26"), its state in plain words
("Waiting for your computer to be idle", "Improving your memories in the background",
"Paused", "Up to date"), what is ready to fix with one button each, the runs that can be
undone, and when SLM last checked. The same card carries the store check that runs after
an upgrade.

## Commands

```bash
slm enrich status              # progress, repairs waiting, the id of every run that changed something
slm enrich pause <run>         # stop the background run; recall and remember keep working
slm enrich resume <run>
slm enrich undo                # undo the latest run that changed something (asks first)
slm enrich undo <run> --yes    # a specific run, without asking
slm enrich fix entity-links    # apply a repair (asks first)
slm enrich fix repair-saves    # cannot be undone; safety copy taken first
slm enrich fix entity-links --include-undone
```

`undo` and `fix` ask before they change anything; with no terminal they refuse unless you
pass `--yes`. Every action takes `--json`. The command goes through the running service, and
says so when it is not running. See the [CLI reference](cli-reference.md#slm-enrich-statuspauseresumeundofix).

`slm doctor` reports the same state in four rows: **Memory answer-ability** (how many
memories can be found by asking a question, by words only, or not at all), **Memory
description** (how much of the store has tags, a kind, dates and entities, and whether the
improvement is running, paused or up to date), **Memory repairs** (each repair waiting for you,
with the command that applies it) and **Failed saves**. `slm doctor --refile-hidden` lists the
hidden-but-still-scored memories and `--yes` re-files exactly those.

## What it will not do

It does not write a summary, rewrite text, merge memories, invent an event date to raise a
coverage figure, or send memory text off your computer. Unknown event dates stay unknown.
Pictures and documents saved through the picture and document path pass through the same
description step as typed text.
