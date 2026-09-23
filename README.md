# Trinity

A local desktop assistant with a JARVIS-style HUD: voice in and out, long-term memory, and a tool-using agent. Her brain is Hermes 3 on your machine through [Ollama](https://ollama.com).

## What she keeps

- **Voice** — wake word, push-to-talk, Whisper on-device, ElevenLabs or local Piper speech
- **Automation** — keyboard and mouse (off until you flip **AUTOMATION**)
- **Desktop tools** — open apps/sites/folders, search files, clipboard, media keys, research, reminders, memory

WhatsApp, Discord bots, phone LINK / ntfy, and the old hosted cloud twin are gone.

## Interface

- **COMMS** — conversation  
- **CORE** — activity log and memory  
- **VOICE** — speech engine and API key  

Left rail: wake word, spoken replies, automation consent, telemetry, reminders.

## Setup and run

```powershell
cd $HOME\TrinityAI
powershell -ExecutionPolicy Bypass -File .\setup.ps1
.\run.bat
```

Needs Python 3.12, Ollama, and a model (default `hermes3:8b`). First mic use downloads Whisper `tiny.en`.

## Voice

**ElevenLabs** — VOICE tab → paste an `sk_` key from [elevenlabs.io → Developers → API Keys](https://elevenlabs.io/app/settings/api-keys). Stored in `data/secrets.json` (gitignored); `ELEVENLABS_API_KEY` also works.

**Piper** — fully local:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_local_voice.ps1 -Voice Amy
```

She falls back to Piper if ElevenLabs fails.

## Automation

Off by default. Flip **AUTOMATION (MOUSE/KEYS)** or set `automation.enabled` to `true`. Then she can move/click/scroll, type, press keys, and send hotkeys via Win32 `SendInput`. Media play/volume still works without that switch.

## Try

- "Open Spotify"
- "What's 18 percent of 2,450?"
- "Research the latest SpaceX launch and summarize it with sources"
- "Remind me to stretch in 20 minutes"
- (automation on) "Type hello and press Enter"
- (automation on) "Press ctrl+c"

## Config

| Key | Meaning |
| --- | --- |
| `llm.model` | Local Ollama model |
| `llm.base_url` | Must be loopback |
| `voice.engine` | `elevenlabs` or `piper` |
| `voice.wake_word` | Wake phrase when armed |
| `automation.enabled` | Keyboard/mouse tools (`false` by default) |

Memory: `data/trinity.db`. **NEW SESSION** clears chat, not the archive.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Privacy

Reasoning, memory, and speech recognition stay on this PC. Internet only for research/open-URL you ask for, and ElevenLabs if you choose it for spoken replies.
