import asyncio
import hashlib
import json
import time

import httpx
import openai
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from app.core.app_logger import get_logger
from app.services.prompts.speech import build_speech_instructions

logger = get_logger(__name__)


# Kokoro v1.0 picks pronunciation from the voice prefix; German and Korean have no voice.
KOKORO_LANGUAGE_VOICES = {
    "en-GB": "bf_emma",
    "es": "ef_dora",
    "fr": "ff_siwis",
    "it": "if_sara",
    "pt": "pf_dora",
    "ja": "jf_alpha",
    "zh": "zf_xiaobei",
}


def kokoro_voice_for(language: str | None, default: str) -> str:
    if not language:
        return default
    return KOKORO_LANGUAGE_VOICES.get(
        language, KOKORO_LANGUAGE_VOICES.get(language.split("-")[0].lower(), default)
    )


class KokoroTTSService:
    def __init__(self, base_url: str, voice: str) -> None:
        self.base_url = base_url
        self.voice = voice

    async def health(self) -> None:
        """Raise if Kokoro is unreachable."""
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{self.base_url}/v1/models", timeout=5.0)
            r.raise_for_status()

    async def synthesize(
        self, text: str, voice: str | None = None, language: str | None = None
    ) -> bytes:
        """Call Kokoro-FastAPI and return MP3 audio bytes."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/v1/audio/speech",
                json={
                    "model": "kokoro",
                    "input": text,
                    "voice": voice or kokoro_voice_for(language, self.voice),
                    "response_format": "mp3",
                },
                timeout=30.0,
            )
            response.raise_for_status()
            return response.content


class OpenAITTSService:
    def __init__(
        self,
        api_key: str,
        model: str,
        voice: str,
        speed: float = 1.0,
        timeout: float | None = None,
    ) -> None:
        self._client = openai.AsyncOpenAI(api_key=api_key)
        self.model = model
        self.voice = voice
        self.speed = speed
        self.timeout = timeout

    async def health(self) -> None:
        """Raise if OpenAI TTS is unreachable (lightweight models list call)."""
        await self._client.models.list()

    def get_instructions(self, language: str | None = None) -> str | None:
        if self.model == "gpt-4o-mini-tts" or self.model.startswith("gpt-4o-mini-tts-"):
            return build_speech_instructions(language)
        return None

    def get_cache_key(
        self, text: str, voice: str | None = None, language: str | None = None
    ) -> str:
        identity = {
            "text": text,
            "locale": language,
            "provider": "openai",
            "model": self.model,
            "voice": voice or self.voice,
            "speed": self.speed,
            "format": "mp3",
        }
        instructions = self.get_instructions(language)
        if instructions:
            identity["instructions"] = instructions
        return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

    async def synthesize(
        self, text: str, voice: str | None = None, language: str | None = None
    ) -> bytes:
        """Call OpenAI TTS API and return MP3 audio bytes."""
        text = text.strip()
        if not text:
            logger.warning("[tts-openai] Empty text received for synthesis")
            return b""

        req_voice = (voice or self.voice).strip()
        input_len = len(text)
        start_t = time.perf_counter()
        logger.info(
            "[tts-openai] request_start model=%s voice=%s chars=%d",
            self.model,
            req_voice,
            input_len,
        )
        request_payload = {
            "model": self.model,
            "voice": req_voice,
            "input": text,
            "response_format": "mp3",
            "speed": self.speed,
        }
        instructions = self.get_instructions(language)
        if instructions:
            request_payload["instructions"] = instructions
        if self.timeout is not None:
            request_payload["timeout"] = self.timeout

        response = await self._client.audio.speech.create(**request_payload)
        audio = response.content
        if not audio:
            raise RuntimeError("OpenAI TTS returned empty audio payload")
        elapsed_ms = (time.perf_counter() - start_t) * 1000
        request_id = getattr(response, "request_id", None)
        if request_id is None:
            headers = getattr(response, "headers", None)
            if headers is not None:
                request_id = headers.get("x-request-id")
        logger.info(
            "[tts-openai] request_ok model=%s voice=%s chars=%d bytes=%d ms=%.1f request_id=%s",
            self.model,
            req_voice,
            input_len,
            len(audio),
            round(elapsed_ms, 1),
            request_id,
        )
        return audio


async def wav_to_mp3(wav: bytes) -> bytes:
    """Encode WAV audio as MP3 with ffmpeg; the app serves and caches all TTS as audio/mpeg."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        "pipe:0",
        "-f",
        "mp3",
        "-b:a",
        "64k",
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate(wav)
    if proc.returncode != 0 or not out:
        raise RuntimeError(f"ffmpeg MP3 encoding failed: {err.decode(errors='replace')[:200]}")
    return out


def pcm16_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    """Wrap mono 16-bit little-endian PCM in a WAV header."""
    import struct  # noqa: PLC0415

    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(pcm),
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        sample_rate,
        sample_rate * 2,
        2,
        16,
        b"data",
        len(pcm),
    )
    return header + pcm


GEMINI_VOICES = frozenset(
    {
        "Kore",
        "Aoede",
        "Leda",
        "Zephyr",
        "Puck",
        "Charon",
        "Fenrir",
        "Orus",
    }
)


class GeminiTTSService:
    """Gemini TTS; accent and pronunciation are steered by the speech instructions."""

    def __init__(
        self,
        api_key: str,
        model: str,
        voice: str,
        timeout: float = 60.0,
        fallback_models: list[str] | None = None,
    ) -> None:
        self._client = genai.Client(api_key=api_key)
        self.model = model
        self.voice = voice
        self.timeout = timeout
        # Tried in order when a model is out of quota or overloaded; quotas are per model.
        self.fallback_models = [m for m in fallback_models or [] if m != model]

    async def health(self) -> None:
        """Raise if the Gemini API is unreachable or the key is rejected."""
        await self._client.aio.models.get(model=self.model)

    def get_instructions(self, language: str | None = None) -> str:
        return build_speech_instructions(language)

    def get_cache_key(
        self, text: str, voice: str | None = None, language: str | None = None
    ) -> str:
        identity = {
            "text": text,
            "locale": language,
            "provider": "gemini",
            "model": self.model,
            "voice": voice or self.voice,
            "format": "mp3",
            "instructions": self.get_instructions(language),
        }
        return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

    async def synthesize(
        self, text: str, voice: str | None = None, language: str | None = None
    ) -> bytes:
        """Call Gemini TTS and return MP3 audio bytes."""
        text = text.strip()
        if not text:
            logger.warning("[tts-gemini] Empty text received for synthesis")
            return b""
        req_voice = voice if voice in GEMINI_VOICES else self.voice
        start_t = time.perf_counter()
        prompt = f"{self.get_instructions(language)}\n\nText:\n{text}"
        models = [self.model, *self.fallback_models]
        for index, model in enumerate(models):
            try:
                response = await self._generate(model, prompt, req_voice)
                break
            except genai_errors.APIError as exc:
                if index + 1 < len(models) and exc.code in (429, 503):
                    logger.warning(
                        "[tts-gemini] model %s unavailable (%s); falling back to %s",
                        model,
                        exc.code,
                        models[index + 1],
                    )
                    continue
                raise
        audio = b""
        mime_type = ""
        for part in response.candidates[0].content.parts or []:
            if part.inline_data and part.inline_data.data:
                audio += part.inline_data.data
                mime_type = part.inline_data.mime_type or mime_type
        if not audio:
            raise RuntimeError("Gemini TTS returned empty audio payload")
        if not audio.startswith(b"RIFF"):
            # Raw PCM, e.g. "audio/L16;codec=pcm;rate=24000".
            rate = 24000
            if "rate=" in mime_type:
                rate = int(mime_type.split("rate=")[1].split(";")[0])
            audio = pcm16_to_wav(audio, rate)
        mp3 = await wav_to_mp3(audio)
        logger.info(
            "[tts-gemini] request_ok model=%s voice=%s chars=%d bytes=%d ms=%.1f",
            model,
            req_voice,
            len(text),
            len(mp3),
            round((time.perf_counter() - start_t) * 1000, 1),
        )
        return mp3

    async def _generate(self, model: str, prompt: str, voice: str):
        return await asyncio.wait_for(
            self._client.aio.models.generate_content(
                model=model,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=genai_types.SpeechConfig(
                        voice_config=genai_types.VoiceConfig(
                            prebuilt_voice_config=genai_types.PrebuiltVoiceConfig(voice_name=voice)
                        )
                    ),
                ),
            ),
            timeout=self.timeout,
        )
