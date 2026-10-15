# FAQ

Short answers to the questions people ask most, each with a link to the page that explains it properly.

## General

### What is SuperLocalMemory?

A memory for AI assistants that lives on your own computer. It keeps your decisions, bug fixes, preferences and project context, and gives the relevant ones back to your assistant in later sessions. Recall combines five retrieval methods and drops results that have no real evidence ([How Recall Works](How-Recall-Works)). The whole design is in [Architecture](Architecture).

### Is it free?

SLM is open source under the AGPL-3.0-or-later licence, with no usage limits, credits or subscription. A separate commercial licence exists for people who want to embed it in a closed-source product or run it as a hosted service without releasing their source (`COMMERCIAL-LICENSE.md` in the repository).

### Where is my data stored?

In a folder on your computer, `~/.superlocalmemory` by default, with the main database in `memory.db`. The same folder holds configuration, logs, downloaded models and backups. Nothing leaves the machine unless you turn on a feature that sends it; [Privacy and Data Flow](Privacy-and-Data-Flow) lists each one.

### Which systems and tools are supported?

Apple Silicon Macs, 64-bit Windows and 64-bit Linux. Intel Macs and 32-bit Windows are not supported. `slm connect --list` shows the editors and agents SLM can configure, and [IDE Setup](IDE-Setup) and [Plugins, Skills and Hooks](Plugins-Skills-and-Hooks) cover each one. Any other tool that speaks MCP can be pointed at `slm mcp` by hand.

### Does it work offline?

Mode A and Mode B work offline once the models are downloaded; Mode B needs a model running on your machine. Mode C needs the network because it calls the endpoint or provider you set. Optional features such as cloud backup, connectors and model downloads use the network in any mode when you enable them ([Modes Explained](Modes-Explained)).

## Install and update

### What do I need?

Python 3.12 to 3.14, and Node.js 18 or later if you install with npm. For Mode B, a local model server such as Ollama. For Mode C, a key for your provider. See [Installation](Installation).

### How do I install and update it?

```bash
npm install -g superlocalmemory
slm setup
```

To update, run `npm install -g superlocalmemory@latest` (or the equivalent for pipx or pip), then `slm restart` and `slm doctor`. Your data folder is never touched by installing or updating. Refresh host integrations with `slm upgrade-hosts` ([Host Integration Upgrades](Host-Upgrades)).

## Using it

### Do I have to tell my assistant to remember and recall?

Not if it is connected through a plugin or hooks. Session context is loaded automatically and the assistant can pass decisions, bug fixes and preferences for capture ([Auto-Memory](Auto-Memory)). You can always save or search by hand with `slm remember` and `slm recall`.

### What do queryable, enriching, complete and failed mean?

They are the stages of a save. `queryable` means the memory is durable and can be found by its wording. `enriching` means background work such as entities and summaries is running. `complete` means that work finished. `failed` means a stage failed and will be retried; the original text is kept, so nothing is lost. `slm ops list` shows operations that are stuck ([How Remember Works](How-Remember-Works)).

### How do I see why a result was returned?

`slm trace "your question"` shows the score each retrieval channel gave every result. The five channels are semantic, keyword, temporal, associative (Hopfield) and spreading activation; the entity graph adjusts scores afterwards and is not a separate channel.

### How do I delete a memory?

`slm forget "query"` previews and deletes memories that match, after asking you (add `--dry-run` to only preview). `slm delete <id>` deletes one memory by its exact id, and `slm list` shows the ids. Erasing a whole profile, with a signed receipt, is covered in [GDPR](GDPR-Compliance).

### My assistant does not seem to remember anything.

Check that SLM runs (`slm status`, `slm doctor`), that something is stored (`slm recall "test"`), that your editor was restarted after `slm connect`, and which profile is active (`slm profile list`). [Troubleshooting](Troubleshooting) goes further.

### Recall returns irrelevant results.

Make the question more specific, then use `slm trace` to see which channel brought the result. Recall tells you when it has nothing: "I don't have this exactly" with the closest memories labelled, or "That isn't in SuperLocalMemory." ([How Recall Works](How-Recall-Works)).

## Answer check

### What is the answer check?

A second decision after recall. Recall says which memories are related; the answer check says whether the top ones actually answer the question, so an assistant can say "I don't have that" instead of presenting a related memory as the answer. It feeds the support check that decides whether recall returns results, only the closest labelled ones, or nothing with "That isn't in SuperLocalMemory." ([Answer Check](Answer-Check), [How Recall Works](How-Recall-Works)). On Apple Silicon it is installed by default and runs on your own Mac; opt out with `slm setup --no-laya` or `SLM_SKIP_LAYA=1`.

### Does it send my memories anywhere?

Only if you pick the online option, add your own key and tick the consent box. Each checked recall then sends the question and the top three memories to the provider you chose. The on-device option keeps everything on your computer.

## Modes

### Which mode should I use?

Mode A if you want privacy and offline use, with no language model in the memory path. Mode B if you have a machine that can run a local model. Mode C if you accept a cloud provider for the best answers. You can switch with `slm mode a`, `b` or `c`; your memories stay the same. Details: [Modes Explained](Modes-Explained).

### How accurate is it?

The published results are on the LoCoMo conversation benchmark, with each number tied to its protocol, in [Research Foundations](Research-Foundations). They come from the architecture paper and are not a fresh run of the current package, so compare them only with results measured the same way. To measure your own installation, the repository has a benchmark harness (`benchmark/README.md`).

## Privacy and governance

### Can anyone else see my memories?

The database is a local file. It is not synced or uploaded unless you turn on Web access, cloud backup, SLM-Mesh peering or a provider-backed mode. Access across profiles is denied by default, and in company mode every action is checked against a role ([Governance](Governance), [Security, Company Mode and Teams](Security-Company-Mode-and-Teams)).

### Is it GDPR compliant?

SLM ships the controls a GDPR programme needs: export, erasure with a verifiable receipt, retention rules, an audit trail and access control. Whether a deployment is compliant depends on how it is configured and run, so SLM does not claim certification ([GDPR](GDPR-Compliance), [Compliance](Compliance)).

### Can I export or delete all my data?

`slm gdpr export --profile NAME --output FILE` exports a profile, and `slm gdpr erase --profile NAME --dry-run` previews an erasure that `--yes` makes final. Do not delete only `memory.db` and call it erased; configuration, logs, models and backups live elsewhere in the data folder.

## Where do I find...

Bounded loops are in [Bounded Loops](Bounded-Loops), framework adapters in [Framework Adapters](Framework-Adapters), several agents sharing memory in [Multi-Agent Memory](Multi-Agent-Memory), and pictures and documents in [Pictures and Documents](Pictures-and-Documents). Bugs go to the [issue tracker](https://github.com/qualixar/superlocalmemory/issues).
