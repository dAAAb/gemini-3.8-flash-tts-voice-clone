# Gemini 3.8 Flash TTS Voice Clone

Local web tool for **Google Gemini 3.8 Flash TTS** voice replication (speaker cloning). Paste your own API key, upload reference audio + consent audio, generate a Taiwan Mandarin–quality sample — using the official Voice Replication API free tier (about **10 requests/day**, no paid AI Studio upgrade required for API calls).

用自己的 **Gemini API Key**，在本機透過官方 **Voice Replication** 複製講者音色並生成試聽 WAV。對應 AI Studio「Create Speaker」流程；社群常用的「Gemini 3.8 Flash TTS」免費額度約每天 10 次。

| | |
| --- | --- |
| **Model** | `gemini-3.8-flash-tts` (optional Lite: `gemini-3.8-flash-lite-tts`) |
| **Clone** | `POST /v1beta/voices` · `type=replicated` |
| **Speak** | `generateContent` + `response_modalities=["AUDIO"]` |
| **SDK** | [`google-genai`](https://pypi.org/project/google-genai/) ≥ 2.25 |
| **UI** | FastAPI + single-page Create Speaker form |

> **Not private data.** This repo ships only app code. Your API keys, uploads, outputs, and voice profiles stay on your machine (gitignored).

---

## Why this exists

AI Studio’s UI may gate “Create Speaker” behind a paid account. Calling the **Gemini API** directly still gets the free quota for TTS / voice replication. This tool wraps the official docs flow so you can:

1. Paste an API key from [Google AI Studio](https://aistudio.google.com/apikey)
2. Upload **source** audio (≈10–30s) and **consent** audio (verbatim official consent text)
3. Enter text → **Generate Speaker Profile & Sample**
4. Play / download WAV, optionally reuse a saved local profile

Default speaking style targets **Taiwan Mandarin（台灣國語）** — clear, natural, not Mainland accent. Change the style prompt in the UI as needed.

---

## Quick start

**Needs:** Python 3.10+, [`ffmpeg`](https://ffmpeg.org/) / `ffprobe` on `PATH`.

```bash
git clone https://github.com/dAAAb/gemini-3.8-flash-tts-voice-clone.git
cd gemini-3.8-flash-tts-voice-clone

python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt

# Optional: put the key in .env (never commit .env)
cp .env.example .env
# edit .env → GEMINI_API_KEY=...

python app.py
```

Open **http://127.0.0.1:7860**

Bind for LAN / Tailscale peers:

```bash
HOST=0.0.0.0 PORT=7860 python app.py
```

---

## Usage (Create Speaker)

Official replication needs **two** clips from the **same adult speaker** (clean speech; tool converts toward 24 kHz mono WAV):

| File | Role |
| --- | --- |
| **Source / reference** | ~10–30 seconds of natural speech |
| **Consent** | Same person reading Google’s consent text **verbatim** |

Consent locales currently in Google’s table (no `zh-TW` yet):

- **en-US:** `I am the owner of this voice and I consent to Google using this voice to create a synthetic voice model.`
- **zh-CN:** `我是此声音的拥有者并授权谷歌使用此声音创建语音合成模型`

Taiwan users often record the **en-US** line clearly, then generate with a Taiwan Mandarin style prompt.

Then in the UI:

1. Paste API Key (stored in browser `localStorage` only, or use `.env`)
2. Speaker name
3. Upload source + consent audio
4. Text to speak + optional style
5. Generate → play / download under `outputs/`

---

## API notes (verified against Google docs)

- Models: [Gemini 3.8 Flash TTS](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash-tts)
- Flow: [Voice replication](https://ai.google.dev/gemini-api/docs/generate-content/voice-replication) / [Voices API](https://ai.google.dev/api/voices)
- Free-tier RPM / RPD vary by account; TTS models commonly show low daily caps (e.g. ~10 RPD). Check [AI Studio → Usage & Billing → Rate limits](https://aistudio.google.com/).
- Social “one clip + Chinese chat prompt in AI Studio” is a **different** multimodal chat path; this project follows **official Voice Replication** (two clips + Voices API).

---

## Project layout

```
app.py              # FastAPI: /api/generate, /api/synthesize, profiles, health
static/index.html   # Create Speaker UI
requirements.txt
.env.example        # GEMINI_API_KEY=  (empty placeholder only)
uploads/ outputs/ profiles/   # local-only, gitignored
```

---

## Privacy & security

- **Do not commit** `.env`, API keys, reference audio, consent audio, WAVs, or `profiles/*.json` (may contain `voice_id`).
- Keys in the UI stay in `localStorage`; the server does not log API keys.
- If you expose `HOST=0.0.0.0`, prefer a private network (e.g. Tailscale). Anyone who can open the UI can spend **your** API quota once they paste a key — treat the URL like a secret.

---

## License

MIT — see [`LICENSE`](LICENSE).

---

## Disclaimer

Voice cloning requires lawful ownership / consent. Follow Google’s terms and local law. This project is unofficial and not affiliated with Google.
