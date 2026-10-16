# Capabilities and Operations

This page is a map of what SuperLocalMemory (SLM) does and how to keep it running. Each section names a capability in one or two sentences and sends you to the page that explains it properly. It also separates what SLM does on its own from what you must switch on or check yourself, because several features are off until you choose them.

## What it does

**Saving and finding memories.** A save is written to a journal first, so an accepted memory survives a crash, and becomes searchable by its wording straight away while entities, summaries and meaning are added in the background ([How Remember Works](How-Remember-Works)). A search runs five retrieval methods in parallel, fuses them into one ranking and drops results that have no real evidence ([How Recall Works](How-Recall-Works)). The numbers recall returns are explained in [Retrieval Score Contract](Retrieval-Score-Contract): a relevance score is not a probability that the answer is right.

**Knowing when it does not know.** The [Answer Check](Answer-Check) looks at the top results and decides whether they answer the question or merely relate to it, so an assistant can say "I don't have that" instead of guessing. It can run on your own device or, if you choose and give a key, at a provider you pick.

**A memory that behaves like memory.** Facts fade, merge into summaries and lose precision according to age and use ([Living Brain](Living-Brain)). Related facts are linked in a graph with clusters, summaries and a browsable Entity Explorer ([Knowledge Graph and Entities](Knowledge-Graph-and-Entities)). Each memory has a kind, such as rule or decision, and rules and decisions are loaded at the start of later sessions ([Memory Kinds](Memory-Kinds)). SLM can also learn from feedback you report ([Active Memory](Active-Memory)) and, if you allow it, propose improvements to your agent skills ([Skill Evolution](Skill-Evolution)).

**Pictures, documents and other sources.** Screenshots, photos, PDFs and whole folders can be saved and found by describing what they show ([Pictures and Documents](Pictures-and-Documents)). Meeting transcripts and calendar files can be imported, and mailbox import works from mbox files ([Ingestion Adapters](Ingestion-Adapters)).

**Many agents, one memory.** Several tools on one computer share a store, each attributed by agent, with separate profiles when you want separation ([Multi-Agent Memory](Multi-Agent-Memory), [Profiles](Profiles)). Agents and bots can message each other through SLM-Mesh ([Bots That Talk](Bots-That-Talk)), and web apps such as ChatGPT or Claude on the web can use your memory through an optional outbound-only link ([Web Access and Connected Apps](Web-Access-and-Connected-Apps)).

**Governed storage.** Roles, trust that agents earn and lose, retention rules, signed erasure receipts, a hash-chained audit trail and default-deny access across profiles are covered in [Governance](Governance), [Security, Company Mode and Teams](Security-Company-Mode-and-Teams), [GDPR](GDPR-Compliance) and [Compliance](Compliance). [Privacy and Data Flow](Privacy-and-Data-Flow) lists every route by which data can leave your computer, and what turns each one on.

**Working inside your tools.** Plugins for Claude Code, Cursor, Codex, GitHub Copilot, Hermes and Antigravity bring skills, agents, commands and hooks ([Plugins, Skills and Hooks](Plugins-Skills-and-Hooks)). Connection steps per editor are in [IDE Setup](IDE-Setup), and the nine framework adapters (LangChain, LlamaIndex, CrewAI and others) are in [Framework Adapters](Framework-Adapters). The MCP tool surface, in six profiles from a small core set to everything, is in [MCP Tools](MCP-Tools). [Bounded Loops](Bounded-Loops) lets an agent iterate until an independent check passes rather than until it says it is done, and [Optimize](Optimize) offers reversible compression and caching for context.

## What is automatic and what you switch on

Some behaviour needs nothing from you: the journal and background enrichment, the evidence floor in recall, decay and consolidation, the once-per-upgrade store check, and the safety copy a restore makes before it changes anything. Other features are deliberately off until you decide: adaptive ranking, skill evolution, Optimize, Web access, cloud backup, ingestion adapters, images and documents, and the online answer-check option. [Modes Explained](Modes-Explained) says what each of the three modes runs and what leaves the machine in each, and [Configuration](Configuration) lists every setting and where to change it.

## Operating it

Four commands give a quick picture of a running installation: `slm status` for mode, profile and data folder, `slm doctor` for dependencies, the embedding worker and the background service, `slm health` for the scoring and lifecycle layers, and `slm trace "a real question"` to see how a representative search was scored. The dashboard shows the same things in one place ([Dashboard Tour](Dashboard-Tour)). When something is off, [Health and Repair](Health-and-Repair) explains the checks and the repair tools, and [Troubleshooting](Troubleshooting) goes symptom by symptom.

Protect the data folder with [Cloud Backup](Cloud-Backup), which covers local backups, encrypted cloud copies, restore points and how a restore keeps a safety copy. Keep software current with the steps in [Installation](Installation) and refresh host integrations with `slm upgrade-hosts` ([Host Integration Upgrades](Host-Upgrades)). To change the text embedding model, see [Embedding Models](Embedding-Models).

## Boundaries worth knowing

SLM supports Apple Silicon Macs, 64-bit Windows and 64-bit Linux. Profiles organise and separate memory inside one installation, and they are not an operating-system security boundary. SLM-Mesh coordinates peers; it does not replicate a memory database across computers. The controls under Governance help a deployment meet its own compliance duties but are not a legal certification. And no page here promises a speed or capacity figure for every machine: measure on yours.
