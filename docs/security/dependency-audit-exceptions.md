# Dependency audit exceptions

The release dependency audit is fail-closed except for the exact advisories
listed below. Each exception must name the transitive dependency path, explain
why the vulnerable API is not reachable from untrusted input, and be removed
as soon as a stable patched release is available.

**Status as of 2026-10-02:** one exception remains, and it has no patched release upstream.

## GHSA-8mgp-746c-j5xp — NLTK 3.10.3

- **Dependency path:** `superlocalmemory -> llmlingua 0.2.2 -> nltk`.
  SuperLocalMemory pins `nltk==3.10.3` directly so the resolver cannot pick an
  older release that carries the fixed advisories.
- **Advisory:** several model-artifact APIs (`TransitionParser.train`,
  `TransitionParser.parse`, `AveragedPerceptron.save`/`load`,
  `PerceptronTagger.save_to_json`, `save_maxent_params`) bypass NLTK's own
  `pathsec` sandbox when an application enables `pathsec` enforcement and lets
  untrusted input choose a model path. GitHub lists `<= 3.10.3` as affected
  with no patched release; 3.10.3 is the newest release on PyPI.
- **Exposure:** none. SuperLocalMemory never imports NLTK. LLMLingua calls only
  `nltk.sent_tokenize`, which is not one of the affected APIs. Nothing in the
  installed runtime calls the affected APIs or enables `pathsec`, so no model
  path reaches them from any CLI, HTTP, dashboard or MCP input.
- **Removal condition:** an NLTK release that fixes the advisory. Pin it and
  remove the `--ignore-vuln` line from `.github/workflows/test.yml`.
- **Review deadline:** 2026-11-02.

---

## Why these stay published

Naming an advisory, proving the vulnerable API is unreachable from untrusted
input, and committing to a removal condition is more useful to a reader than
silence. Ignoring the advisories and saying nothing would be worse.

The obligation that comes with publishing is that the entries must stay true: each one has to be rechecked against upstream releases, and removed once a stable patched release exists.
