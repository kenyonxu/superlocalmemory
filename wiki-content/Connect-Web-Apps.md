# Connect Web Apps

SuperLocalMemory works fully on your computer. **Web access** is an optional extra for one purpose: letting an AI app that runs on the internet (ChatGPT, Claude on the web, Composio, Muse, Grok Bot) recall from and, if you allow it, save to the same memory your local agents use. It is off until you turn it on. Your memory database stays on your computer and is never uploaded; the app asks your computer a question and gets an answer back.

Agents on your own computer (Claude Code, Codex, Cursor, VS Code and the rest) do not need Web access. See [[IDE Setup]] and run `slm connect` for those.

## What you need

SLM installed and running on a computer that stays on and online while the app needs your memory; a GitHub account for sign-in; and an app that supports a custom remote MCP connection with OAuth. Whether your plan with that app allows one is up to the app.

## Turn it on

1. Run `slm dashboard`, and under **Integrations** choose **Connected apps**.
2. Under **Set up an app**, choose the app.
3. Tick **Turn on internet access for this app**. Reading is always included. **Allow saving memories** and **Allow session tools** are separate boxes, off unless you tick them.
4. Select **Link this computer with GitHub** and finish the sign-in. Wait until the page says **Web access is on**.
5. Follow the steps shown for your app. Most only ask for the MCP server URL; some also ask for the OAuth metadata URL. Both are on the page under **Technical details**.
6. Sign in with the same GitHub account when the app asks, and approve the permissions.
7. Paste the setup prompt (**Copy instructions** on the page) into a chat with the app, so it knows when to recall and what to save.
8. Ask the app something your memory knows. Only a completed round trip proves the app is connected.

The exact clicks for each app are in the [host guides](https://github.com/qualixar/superlocalmemory/blob/main/docs/remote-access/hosts.md). ChatGPT keeps the permissions it discovered when the plugin was created, so a plugin created while it could only read must be removed and created again.

## What an app can and cannot do

An app reaches one profile, the one active when you turned Web access on. With reading it can use `recall`, `search`, `fetch` and `get_status`. Saving adds `remember`; session tools add `session_init`, `close_session`, `report_feedback` and `report_outcome`. It can never delete, retire or correct a memory, share one with another profile, switch profile or run any other tool. **Remove access** on the page ends one app immediately; **Turn off** ends all of them and leaves your memory untouched.

Anything an app reads is shared with that app and the AI service behind it, and anything it saves travels to your computer through the connection. Treat it like any other service you give access to.

## Pictures, documents and bot messages

Each app needs two yeses for these: a box on the approval page, and a switch on the **Web access** row in Connected apps (or `slm remote keys allow <key> media` or `mesh`; `disallow` turns it off).

With the media switch, an app can save a picture or PDF through a one-time upload link: you open it, pick the file, and it streams to your computer. See [[Pictures and Documents]]. With the mesh switch, the app joins SLM-Mesh beside your local agents: it can see peers, message a named bot, and read its inbox. A web app's messages arrive at least once, and one it did not acknowledge comes again, marked as a repeat. The **Bot messages** tab lists every bot by name with mute, rename and retire. See [[Multi-Agent Memory]].

## When it is not available

If your computer is asleep or offline, the app gets `connector_asleep` rather than an empty recall. If the free daily allowance of tool calls is used up, apps get `DAILY_LIMIT_REACHED` until midnight UTC. Local use is never limited, and a problem with Web access never stops local SLM. Error codes and fixes are in [[Troubleshooting]].
