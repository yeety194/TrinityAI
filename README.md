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

Memory lives in `data/trinity.db` (not sent anywhere). **New session** clears the chat, not long-term memory.

Trinity also captures unmistakable personal facts on her own — your name, where you live, "my favorite X is Y", and stated preferences — so a forgetful local model still keeps them. Preferences accumulate under one `preferences` entry instead of overwriting each other.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_core
```

## Local-first privacy boundary

The brain, memory, speech recognition, and speech synthesis operate locally. Trinity does not have a cloud-AI fallback, telemetry, auto-updates, or background online activity. The only runtime features that contact the internet are browser links you explicitly ask to open and the research tools you ask her to use.
