# Auto-Memory

SuperLocalMemory can automatically capture and recall memories without explicit commands. This page explains how auto-capture and auto-recall work.

## Auto-Recall

**What it does:** When you start a conversation in your IDE, SuperLocalMemory automatically retrieves relevant memories and injects them into the AI's context.

**How it works:**
1. Your IDE sends the conversation context to the MCP server
2. SuperLocalMemory extracts key terms and entities from your message
3. It runs a 4-channel retrieval (semantic, keyword, entity graph, temporal)
4. The top results are returned to your IDE
5. Your IDE includes these memories in the AI's system prompt

**What this means for you:** Your AI knows about your past work without you having to say "recall" or "remember." It just knows.

**Example:**
```
You: "Can you help me debug the auth service?"
AI: "Based on your previous work, the auth service uses JWT tokens with 24-hour expiry
     and refresh tokens lasting 30 days. Last time you debugged it, the issue was
     related to clock skew between services. Let me help..."
```

The AI referenced memories you stored days or weeks ago — automatically.

## Auto-Capture

**What it does:** SuperLocalMemory can automatically store important information from your conversations without you running `slm remember`.

**What gets captured:**
- Decisions ("We chose PostgreSQL for the user service")
- Bug fixes ("Fixed the race condition in the queue processor")
- Configuration details ("Deploy to us-east-1, use t3.large instances")
- Preferences ("Always use TypeScript strict mode")
- Project context ("The frontend uses React 19 with Server Components")

**What does NOT get captured:**
- Casual conversation
- Questions without answers
- Temporary debugging output
- Sensitive data marked as excluded

**How it works:**
1. Your IDE conversation flows through the MCP server
2. An entropy gate evaluates each message for information density
3. High-information messages are extracted into structured facts
4. Facts are stored with entities, timestamps, and graph connections
5. Low-information messages are ignored

## Configuration

### Enable/Disable Auto-Recall

Auto-recall is enabled by default. To disable:

```bash
slm config set auto_recall false
```

To re-enable:

```bash
slm config set auto_recall true
```

### Enable/Disable Auto-Capture

Auto-capture is enabled by default. To disable:

```bash
slm config set auto_capture false
```

### Adjust Auto-Recall Depth

Control how many memories are injected per conversation turn:

```bash
slm config set recall_limit 5    # Default: 5 memories per turn
slm config set recall_limit 10   # More context (uses more tokens)
slm config set recall_limit 3    # Less context (faster, fewer tokens)
```

### Exclude Profiles from Auto-Capture

If you have a profile where you do not want auto-capture:

```bash
slm profile switch scratch
slm config set auto_capture false
```

This only affects the `scratch` profile. Other profiles keep their settings.

## How Auto-Capture Decides What to Store

The entropy gate uses several signals:

1. **Information density** — Messages with specific facts, names, numbers, or decisions score higher
2. **Novelty** — Information that is not already stored scores higher
3. **Entity presence** — Messages mentioning people, projects, tools, or services score higher
4. **Temporal markers** — Messages with dates, deadlines, or time references are captured
5. **Decision language** — Phrases like "we decided," "the fix was," "going with" trigger capture

You can see what was auto-captured:

```bash
slm list --source auto --limit 10
```

## Reviewing Auto-Captured Memories

It is good practice to periodically review what was captured:

```bash
slm list --source auto --limit 20
```

If something was captured incorrectly, delete it:

```bash
slm forget --id <memory-id>
```

If something important was missed, store it manually:

```bash
slm remember "The critical detail that was missed"
```

## Privacy Note

Auto-capture only processes conversations that pass through the MCP server. It does not monitor your system, read files, or access anything outside the IDE conversation. All processing is local (Mode A/B) or uses the cloud provider you configured (Mode C).

---
*Part of [Qualixar](https://qualixar.com) | Created by [Varun Pratap Bhardwaj](https://varunpratap.com)*
