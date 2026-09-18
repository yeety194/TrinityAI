# Trinity

Local desktop assistant: chat, voice, long-term memory, app launching, and live web research. Its brain is local Hermes through [Ollama](https://ollama.com).

## Capabilities

- **Talk** — type, Mic, or Ctrl+Space. Optional wake word “Trinity”
- **Two workspaces** — Chat for a clean conversation; Brain for live activity, local memory, and a context snapshot. Both share the same conversation.
- **Conversation continuity** — recent turns remain active; older turns are compacted into local session notes so Trinity can stay on topic.
- **Local voice** — Piper neural speech uses a voice model on your PC; choose Amy, Lessac, or Ryan from the app
- **Open apps** — Start Menu / installed apps (Chrome, Discord, Spotify, Cursor, …)
- **Open sites and folders** — URLs, Desktop, Documents, Downloads
- **Remember** — durable facts in a local SQLite store (shown in the Memory panel)
- **Research** — web search, Wikipedia, page reading, weather
- **Utilities** — time, clipboard, filename search under your user profile, notes
- **Phone link** — she can text your phone, and you can message her back and get answers

She uses tools for real actions. She should not claim she opened something unless the tool ran.

The Brain workspace shows the observable workflow (requests, memory updates, tools, and results). It intentionally does not expose private model reasoning.

## Setup and run

```powershell
cd $HOME\TrinityAI
powershell -ExecutionPolicy Bypass -File .\setup.ps1
.\run.bat
```

Needs Python 3.12, Ollama, and a model (default `hermes3:8b`). First mic use downloads Whisper `tiny.en`.

Install a local voice once, then it runs offline:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_local_voice.ps1 -Voice Amy
```

Use `Lessac` or `Ryan` in place of `Amy` to add those local choices. The voice model download is a one-time setup step; Trinity never uses a cloud voice service.

## Try

- “Open Discord”
- “Remember that my name is …”
- “What’s the weather in Miami?”
- “Research the latest SpaceX launch and summarize it”
- “What do you remember about me?”

## Phone link

Two-way messaging over [ntfy](https://ntfy.sh) — free, no account needed.

1. Install the **ntfy** app on your phone
2. Open Trinity's **Phone** tab and turn on **Enable phone link**
3. Subscribe your phone to both topics shown there
4. Press **Send test message** to confirm it arrives

She messages you on the outbound topic; anything you publish to the inbound topic she answers. Ask her to "text me when you're done" and she uses it on her own.

This works while this PC is awake and Trinity is running. Messages you send while it is asleep are answered when she next starts, because she resumes from the last message she saw.

Your topics are generated on first run and kept in `data/phone_link.json`, which git ignores — they never reach a commit.

**Treat the topic names like passwords.** On the public `ntfy.sh` server the topic name is the only thing protecting them, which is why each install generates its own random pair. Phone messages are deliberately limited to research, weather, memory, notes, and texting — they cannot open apps, read your clipboard, or search your files. The Phone tab has a switch to lift that restriction; leaving it off means a leaked topic cannot drive your PC. Self-host ntfy with an access token for stronger protection.

## Config

`config.json`

| Key | Meaning |
| --- | --- |
| `llm.model` | Local Ollama model (`hermes3:8b` is tuned for tool use and multi-step tasks) |
| `llm.base_url` | Usually `http://127.0.0.1:11434` |
| `voice.wake_word` | Wake phrase when listening is armed |
| `voice.speak_replies` | Spoken answers |
| `voice.whisper_model` | `tiny.en` or `base.en` |
| `voice.piper_voice` | Local Piper voice model currently selected |
| `voice.piper_length_scale` | Speaking pace; below 1.0 is faster |
| `messaging.enabled` | Whether the phone link listens on startup |
| `messaging.server` | ntfy server, `https://ntfy.sh` or your own |
| `messaging.token` | Access token for a self-hosted ntfy; put it in `data/phone_link.json` so it stays out of git |
| `messaging.remote_can_control_pc` | Off by default; on lets phone messages use every tool |

Memory lives in `data/trinity.db` (not sent anywhere). **New session** clears the chat, not long-term memory.

Trinity also captures unmistakable personal facts on her own — your name, where you live, "my favorite X is Y", and stated preferences — so a forgetful local model still keeps them. Preferences accumulate under one `preferences` entry instead of overwriting each other.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_core
```

## Local-first privacy boundary

The brain, memory, speech recognition, and speech synthesis operate locally. Trinity has no cloud-AI fallback, telemetry, or auto-updates: your words are never sent to an external AI service.

Three things do reach the internet, all at your request:

- research tools you ask her to use
- browser links you ask her to open
- the phone link, when you enable it

The phone link is the only one that holds a standing connection, and it carries the messages between you and her through the ntfy server you configure. Turn it off in the Phone tab and she is fully offline again.
