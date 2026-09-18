# Trinity

A local desktop assistant with a JARVIS-style interface: a live arc-reactor HUD, voice in and out, long-term memory, and a tool-using agent that plans before it acts. Her brain is Hermes 3 running on your own machine through [Ollama](https://ollama.com).

## The interface

A reactor core that reacts to what she is doing — idle, listening, thinking, speaking — alongside live machine telemetry and your queued reminders. Three workspaces:

- **COMMS** — the conversation
- **CORE** — activity log, memory archive, and a context snapshot
- **VOICE** — speech engine, API key, and voice selection

## What she can do

**Think.** Multi-part requests get a short plan before any tool runs. When a tool comes back empty she changes the arguments or switches tools instead of giving up, and a stalled reply gets one nudge rather than a shrug.

**Act on this PC.** Open apps, sites, and folders. Search filenames, list directories, and read text files. Control playback and volume. Report live CPU, memory, disk, and battery. Read and write the clipboard.

**Research.** A single search for quick facts, or `deep_research` to search, actually read the top sources, and hand back notes with links to cite.

**Remember.** Durable facts in a local SQLite archive, shown in the CORE tab. She also captures unmistakable personal facts on her own — your name, where you live, favourites, stated preferences — so a forgetful local model still keeps them.

**Calculate.** Arithmetic goes through a real evaluator, never the model's guesswork.

**Remind.** "Remind me to stretch in 20 minutes" — reminders are scheduled, shown on the HUD, and spoken when due.

## Setup and run

```powershell
cd $HOME\TrinityAI
powershell -ExecutionPolicy Bypass -File .\setup.ps1
.\run.bat
```

Needs Python 3.12, Ollama, and a model (default `hermes3:8b`). First mic use downloads Whisper `tiny.en`.

## Voice

**ElevenLabs** gives her a natural voice. Open the **VOICE** tab, paste your API key, press **SAVE**, then **LOAD MY VOICES** and pick one. The key is stored in `data/secrets.json`, which git ignores; `ELEVENLABS_API_KEY` works too.

**Piper** runs on this PC with no account and no network. Install a voice once:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_local_voice.ps1 -Voice Amy
```

She falls back to Piper automatically whenever ElevenLabs is unreachable, unauthorized, or out of credits, so she never goes silent.

## Talking to her

- Type in the composer and press Enter
- **MIC** or **Ctrl+Space**, then speak; she stops at a pause
- Turn on the wake word and say "Trinity, …"

## Try

- "Open Discord"
- "What's 18 percent of 2,450?"
- "Research the latest SpaceX launch and summarize it with sources"
- "Remind me to stretch in 20 minutes"
- "How's this machine doing?"
- "Remember that my name is …"

## Config

`config.json`

| Key | Meaning |
| --- | --- |
| `llm.model` | Local Ollama model (`hermes3:8b` handles tools well) |
| `llm.base_url` | Must be loopback; she refuses a remote brain |
| `voice.engine` | `elevenlabs` or `piper` |
| `voice.elevenlabs_voice_id` | Voice used for cloud speech |
| `voice.elevenlabs_model` | Defaults to `eleven_turbo_v2_5` |
| `voice.piper_voice` | Local fallback voice |
| `voice.piper_length_scale` | Speaking pace; below 1.0 is faster |
| `voice.wake_word` | Wake phrase when listening is armed |
| `voice.whisper_model` | `tiny.en` or `base.en` |

Memory lives in `data/trinity.db`. **NEW SESSION** clears the conversation, not the archive.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_core
```

## Where your words go

Her reasoning, memory, and speech recognition run entirely on this PC, and she has no cloud-AI fallback, telemetry, or auto-updates.

Three things reach the internet, all at your request: the research tools you ask her to use, links you ask her to open, and — if you choose ElevenLabs for speech — the text of her spoken replies, sent to ElevenLabs to be voiced. Switch the VOICE tab to Piper and she is fully offline again.
