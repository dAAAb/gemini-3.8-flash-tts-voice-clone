#!/usr/bin/env python3
"""
Local Gemini TTS Speaker Clone tool.
Uses official Voices API (voice replication) + gemini-3.8-flash-tts.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from google import genai
from google.genai import errors as genai_errors

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = BASE_DIR / "uploads"
OUTPUTS_DIR = BASE_DIR / "outputs"
PROFILES_DIR = BASE_DIR / "profiles"
STATIC_DIR = BASE_DIR / "static"

for d in (UPLOADS_DIR, OUTPUTS_DIR, PROFILES_DIR):
    d.mkdir(parents=True, exist_ok=True)

DEFAULT_MODEL = os.getenv("GEMINI_TTS_MODEL", "gemini-3.8-flash-tts")
LITE_MODEL = "gemini-3.8-flash-lite-tts"
ALLOWED_MODELS = {DEFAULT_MODEL, LITE_MODEL, "gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts"}

DEFAULT_TEXT_ZHTW = (
    "大家好，我是台灣 AI 語音測試。今天天氣不錯，"
    "我們一起用自然的台灣國語聊聊科技與生活。"
)
DEFAULT_STYLE = (
    "清晰自然的台灣國語（Taiwan Mandarin）口吻，溫暖親切、節奏穩穩的，"
    "發音清楚，不要大陸腔"
)

CONSENT_PHRASES = {
    "en-US": (
        "I am the owner of this voice and I consent to Google using this voice "
        "to create a synthetic voice model."
    ),
    "zh-CN": "我是此声音的拥有者并授权谷歌使用此声音创建语音合成模型",
}

ALLOWED_AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac", ".webm"}
MIME_BY_EXT = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".webm": "audio/webm",
}

app = FastAPI(title="Gemini TTS Speaker Clone", version="1.0.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/outputs", StaticFiles(directory=str(OUTPUTS_DIR)), name="outputs")


def _safe_name(name: str, fallback: str = "speaker") -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff\-]+", "_", (name or "").strip(), flags=re.UNICODE)
    cleaned = cleaned.strip("_")[:60]
    return cleaned or fallback


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _ffprobe_duration(path: Path) -> Optional[float]:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            return None
        return float(result.stdout.strip())
    except Exception:
        return None


def _convert_to_wav_24k_mono(src: Path, dst: Path) -> Path:
    """Convert any supported audio to 24kHz mono 16-bit WAV (API recommended)."""
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(src),
        "-ac",
        "1",
        "-ar",
        "24000",
        "-sample_fmt",
        "s16",
        str(dst),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
    if result.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        raise HTTPException(
            status_code=400,
            detail=(
                "無法轉換音訊為 WAV。請改上傳 wav/mp3/m4a。"
                f" ffmpeg: {(result.stderr or '')[-400:]}"
            ),
        )
    return dst


def _read_file_b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def _make_client(api_key: str) -> genai.Client:
    return genai.Client(api_key=api_key)


def _friendly_api_error(exc: Exception) -> tuple[int, str]:
    msg = str(exc)
    lower = msg.lower()
    status = 502

    if isinstance(exc, genai_errors.APIError):
        code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        if code:
            try:
                status = int(code)
            except Exception:
                status = 502

    if any(k in lower for k in ("api key", "api_key", "invalid x-goog-api-key", "permission_denied", "401", "403")):
        return 401, "API Key 無效或無權限。請到 https://aistudio.google.com/apikey 重新建立金鑰。"
    if any(k in lower for k in ("quota", "rate limit", "resource_exhausted", "429", "rpd", "exceeded")):
        return 429, (
            "已達配額／速率上限（Free tier 約 10 RPD、3 RPM）。"
            "請稍後再試，或改用 Lite 模型 / 付費方案。"
            f" 原始訊息：{msg[:300]}"
        )
    if any(k in lower for k in ("consent", "source_audio", "invalid_argument", "audio")):
        return 400, (
            "音訊或同意聲明驗證失敗。請確認：參考音 10–30 秒、同意聲明必須由同一人"
            "用支援語言「逐字」朗讀（建議 en-US 或 zh-CN），且兩段錄音條件相近。"
            f" 原始訊息：{msg[:300]}"
        )
    return status if 400 <= status < 600 else 502, f"Gemini API 錯誤：{msg[:500]}"


def _extract_audio_bytes(response: Any) -> bytes:
    """Extract WAV/audio bytes from generate_content response."""
    try:
        cand = response.candidates[0]
        part = cand.content.parts[0]
        inline = part.inline_data
        data = inline.data
        if isinstance(data, str):
            return base64.b64decode(data)
        if isinstance(data, (bytes, bytearray)):
            return bytes(data)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"無法從回應取出音訊：{exc}") from exc
    raise HTTPException(status_code=502, detail="回應中沒有音訊資料。")


def _save_profile(profile: dict) -> Path:
    name = _safe_name(profile.get("name", "speaker"))
    path = PROFILES_DIR / f"{name}_{profile.get('created_at', _now_stamp())}.json"
    path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    # also keep a latest pointer by name
    latest = PROFILES_DIR / f"{name}_latest.json"
    latest.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _prepare_audio_upload(
    upload: UploadFile,
    label: str,
    min_sec: float,
    max_sec: float,
    warn_only: bool = False,
) -> tuple[Path, float | None, list[str]]:
    warnings: list[str] = []
    if not upload or not upload.filename:
        raise HTTPException(status_code=400, detail=f"請上傳{label}音訊檔。")

    suffix = Path(upload.filename).suffix.lower() or ".bin"
    if suffix not in ALLOWED_AUDIO_EXT:
        raise HTTPException(
            status_code=400,
            detail=f"{label}格式不支援（{suffix}）。請使用 wav / mp3 / m4a。",
        )

    raw_path = UPLOADS_DIR / f"{_now_stamp()}_{label}_{_safe_name(Path(upload.filename).stem)}{suffix}"
    data = upload.file.read()
    if not data or len(data) < 100:
        raise HTTPException(status_code=400, detail=f"{label}檔案太小或損壞。")
    raw_path.write_bytes(data)

    wav_path = raw_path.with_suffix(".wav")
    if suffix == ".wav":
        # still normalize to 24k mono
        _convert_to_wav_24k_mono(raw_path, wav_path)
    else:
        _convert_to_wav_24k_mono(raw_path, wav_path)

    duration = _ffprobe_duration(wav_path)
    if duration is not None:
        if duration < min_sec or duration > max_sec:
            msg = (
                f"{label}長度約 {duration:.1f} 秒，官方建議 {min_sec:.0f}–{max_sec:.0f} 秒。"
            )
            if warn_only:
                warnings.append(msg)
            else:
                warnings.append(msg + " 仍會嘗試送出，但可能失敗。")
    else:
        warnings.append(f"無法偵測{label}時長，將直接送出。")

    return wav_path, duration, warnings


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html_path = STATIC_DIR / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "model_default": DEFAULT_MODEL,
        "models": sorted(ALLOWED_MODELS),
        "has_env_key": bool(os.getenv("GEMINI_API_KEY")),
        "consent_locales": list(CONSENT_PHRASES.keys()),
    }


@app.get("/api/consent")
def consent_phrases() -> dict:
    return {"phrases": CONSENT_PHRASES}


@app.get("/api/profiles")
def list_profiles() -> dict:
    items = []
    for p in sorted(PROFILES_DIR.glob("*_latest.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            items.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    return {"profiles": items}


@app.post("/api/generate")
async def generate_speaker(
    api_key: str = Form(""),
    speaker_name: str = Form(...),
    text: str = Form(""),
    style: str = Form(""),
    model: str = Form(DEFAULT_MODEL),
    store_voice: str = Form("true"),
    reuse_voice_id: str = Form(""),
    reference_audio: Optional[UploadFile] = File(None),
    consent_audio: Optional[UploadFile] = File(None),
) -> JSONResponse:
    """
    Create (or reuse) a replicated voice, then synthesize sample speech.
    Official API requires BOTH source_audio (10-30s) and consent_audio.
    """
    key = (api_key or "").strip() or (os.getenv("GEMINI_API_KEY") or "").strip()
    if not key:
        raise HTTPException(
            status_code=400,
            detail="請提供 Gemini API Key（表單或 .env 的 GEMINI_API_KEY）。",
        )

    model = (model or DEFAULT_MODEL).strip()
    if model not in ALLOWED_MODELS:
        raise HTTPException(status_code=400, detail=f"不支援的模型：{model}")

    name = (speaker_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="請填寫 Speaker 名稱。")

    speak_text = (text or "").strip() or DEFAULT_TEXT_ZHTW
    style_text = (style or "").strip() or DEFAULT_STYLE
    store = (store_voice or "true").lower() in {"1", "true", "yes", "on"}
    existing_voice = (reuse_voice_id or "").strip()

    warnings: list[str] = []
    source_wav: Optional[Path] = None
    consent_wav: Optional[Path] = None
    source_hash = ""
    voice_id_or_key = existing_voice
    voice_meta: dict[str, Any] = {}

    try:
        if not existing_voice:
            if reference_audio is None or not reference_audio.filename:
                raise HTTPException(
                    status_code=400,
                    detail="請上傳參考音訊（10–30 秒），或填入既有 voice_id / voicekey_。",
                )
            if consent_audio is None or not consent_audio.filename:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "官方 Voice Replication 需要「同意聲明錄音」。"
                        "請用同一人朗讀下方同意全文並上傳（en-US 或 zh-CN）。"
                    ),
                )

            source_wav, src_dur, w1 = _prepare_audio_upload(
                reference_audio, "參考", 10.0, 30.0, warn_only=True
            )
            consent_wav, cons_dur, w2 = _prepare_audio_upload(
                consent_audio, "同意聲明", 2.0, 60.0, warn_only=True
            )
            warnings.extend(w1)
            warnings.extend(w2)
            source_hash = _sha256_bytes(source_wav.read_bytes())

            client = _make_client(key)
            source_b64 = _read_file_b64(source_wav)
            consent_b64 = _read_file_b64(consent_wav)

            try:
                replicated = client.voices.create(
                    store=store,
                    voice={
                        "model": model,
                        "type": "replicated",
                        "display_name": name,
                        "replicated": {
                            "source_audio": {
                                "mime_type": "audio/wav",
                                "data": source_b64,
                            },
                            "consent_audio": {
                                "mime_type": "audio/wav",
                                "data": consent_b64,
                            },
                        },
                    },
                )
            except Exception as exc:
                code, detail = _friendly_api_error(exc)
                raise HTTPException(status_code=code, detail=detail) from exc

            voice_id_or_key = getattr(replicated, "id", None) or getattr(replicated, "key", None)
            if not voice_id_or_key:
                raise HTTPException(status_code=502, detail="建立語音成功但未回傳 voice id/key。")
            voice_meta = {
                "id": getattr(replicated, "id", None),
                "key": getattr(replicated, "key", None),
                "display_name": getattr(replicated, "display_name", name),
                "store": store,
                "source_duration_sec": src_dur,
                "consent_duration_sec": cons_dur,
            }
        else:
            client = _make_client(key)
            voice_meta = {"id_or_key": existing_voice, "reused": True}
            if reference_audio and reference_audio.filename:
                # optional: still save hash for profile
                tmp = UPLOADS_DIR / f"{_now_stamp()}_ref_optional{Path(reference_audio.filename).suffix}"
                tmp.write_bytes(await reference_audio.read())
                source_hash = _sha256_bytes(tmp.read_bytes())

        # Synthesize sample
        try:
            response = client.models.generate_content(
                model=model,
                contents=[
                    {
                        "role": "user",
                        "parts": [
                            {
                                "text": speak_text,
                                "speech_metadata": {"style": style_text},
                            }
                        ],
                    }
                ],
                config={
                    "response_modalities": ["AUDIO"],
                    "speech_config": {
                        "voice_config": {"voice": voice_id_or_key},
                    },
                },
            )
        except Exception as exc:
            code, detail = _friendly_api_error(exc)
            raise HTTPException(status_code=code, detail=detail) from exc

        audio_bytes = _extract_audio_bytes(response)
        stamp = _now_stamp()
        safe = _safe_name(name)
        out_name = f"{safe}_{stamp}.wav"
        out_path = OUTPUTS_DIR / out_name
        out_path.write_bytes(audio_bytes)

        profile = {
            "name": name,
            "model": model,
            "voice_id": voice_meta.get("id") or (voice_id_or_key if str(voice_id_or_key).startswith("voice_") else None),
            "voice_key": voice_meta.get("key")
            or (voice_id_or_key if str(voice_id_or_key).startswith("voicekey_") else None),
            "voice_ref": voice_id_or_key,
            "store": store if not existing_voice else None,
            "reference_hash": source_hash or None,
            "sample_path": f"/outputs/{out_name}",
            "sample_text": speak_text,
            "style": style_text,
            "created_at": stamp,
            "warnings": warnings,
        }
        profile_path = _save_profile(profile)

        return JSONResponse(
            {
                "ok": True,
                "voice_ref": voice_id_or_key,
                "voice_meta": voice_meta,
                "audio_url": f"/outputs/{out_name}",
                "audio_filename": out_name,
                "profile_path": str(profile_path.relative_to(BASE_DIR)),
                "profile": profile,
                "warnings": warnings,
                "model": model,
            }
        )
    except HTTPException:
        raise
    except Exception as exc:
        traceback.print_exc()
        code, detail = _friendly_api_error(exc)
        raise HTTPException(status_code=code, detail=detail) from exc


@app.post("/api/synthesize")
async def synthesize_only(
    api_key: str = Form(""),
    voice_ref: str = Form(...),
    text: str = Form(""),
    style: str = Form(""),
    model: str = Form(DEFAULT_MODEL),
    speaker_name: str = Form("sample"),
) -> JSONResponse:
    """Synthesize with an existing voice_... or voicekey_... without re-cloning."""
    key = (api_key or "").strip() or (os.getenv("GEMINI_API_KEY") or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="請提供 Gemini API Key。")
    model = (model or DEFAULT_MODEL).strip()
    if model not in ALLOWED_MODELS:
        raise HTTPException(status_code=400, detail=f"不支援的模型：{model}")
    ref = (voice_ref or "").strip()
    if not ref:
        raise HTTPException(status_code=400, detail="請提供 voice_id 或 voicekey_。")

    speak_text = (text or "").strip() or DEFAULT_TEXT_ZHTW
    style_text = (style or "").strip() or DEFAULT_STYLE
    client = _make_client(key)

    try:
        response = client.models.generate_content(
            model=model,
            contents=[
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": speak_text,
                            "speech_metadata": {"style": style_text},
                        }
                    ],
                }
            ],
            config={
                "response_modalities": ["AUDIO"],
                "speech_config": {"voice_config": {"voice": ref}},
            },
        )
    except Exception as exc:
        code, detail = _friendly_api_error(exc)
        raise HTTPException(status_code=code, detail=detail) from exc

    audio_bytes = _extract_audio_bytes(response)
    stamp = _now_stamp()
    safe = _safe_name(speaker_name)
    out_name = f"{safe}_{stamp}.wav"
    out_path = OUTPUTS_DIR / out_name
    out_path.write_bytes(audio_bytes)

    return JSONResponse(
        {
            "ok": True,
            "voice_ref": ref,
            "audio_url": f"/outputs/{out_name}",
            "audio_filename": out_name,
            "model": model,
        }
    )


def main() -> None:
    import uvicorn

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "7860"))
    print(f"Starting Gemini TTS Speaker Clone on http://{host}:{port}")
    print(f"Default model: {DEFAULT_MODEL}")
    print("API key is never logged. Prefer UI localStorage or local .env.")
    uvicorn.run("app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
