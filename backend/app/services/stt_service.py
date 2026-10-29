import asyncio
import io

import httpx
import openai
from google import genai
from google.genai import types as genai_types

from app.core.app_logger import get_logger
from app.services.ai_errors import reports_ai_failure
from app.services.user_api_keys import client_for_key, current_user_api_key

logger = get_logger(__name__)


class WhisperSTTService:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    async def health(self) -> None:
        """Raise if Whisper ASR is unreachable."""
        async with httpx.AsyncClient() as client:
            # The ASR webservice redirects / to its docs page.
            r = await client.get(f"{self.base_url}/", timeout=5.0, follow_redirects=True)
            r.raise_for_status()

    @reports_ai_failure("stt")
    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str = "audio.wav",
        mime_type: str = "audio/wav",
        *,
        language: str,
    ) -> str:
        """Send audio to Whisper ASR and return the transcribed text.

        Compatible with onerahmet/openai-whisper-asr-webservice which exposes
        POST /asr?output=json&language=<code> (not the OpenAI /v1/audio/transcriptions path).
        """
        async with httpx.AsyncClient() as client:
            logger.debug(
                "[stt] POST /asr — %d bytes, filename=%s lang=%s",
                len(audio_bytes),
                filename,
                language,
            )
            response = await client.post(
                f"{self.base_url}/asr",
                params={"output": "json", "language": language, "task": "transcribe"},
                files={"audio_file": (filename, audio_bytes, mime_type)},
                timeout=60.0,
            )
            logger.debug("[stt] Response status: %s", response.status_code)
            response.raise_for_status()
            data = response.json()
            # Response: {"text": "...", ...}
            text = data.get("text", "").strip()
            logger.info("[stt] Transcribed: %r", text)
            return text


class OpenAISTTService:
    def __init__(self, api_key: str, model: str) -> None:
        self._client = openai.AsyncOpenAI(api_key=api_key)
        self.model = model

    async def health(self) -> None:
        """Raise if OpenAI STT is unreachable (lightweight models list call)."""
        await self._client.models.list()

    @reports_ai_failure("stt")
    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str = "audio.wav",
        mime_type: str = "audio/wav",
        *,
        language: str,
    ) -> str:
        """Transcribe audio using OpenAI Whisper API."""
        audio_file = (filename, io.BytesIO(audio_bytes), mime_type)
        response = await self._client.audio.transcriptions.create(
            model=self.model,
            file=audio_file,
            language=language,
            timeout=60.0,
        )
        text = response.text.strip()
        logger.info(
            "[stt-openai] Transcribed model=%s lang=%s: %r",
            self.model,
            language,
            text,
        )
        return text


class GeminiSTTService:
    def __init__(self, api_key: str, model: str, timeout: float = 60.0) -> None:
        self._server_client = genai.Client(api_key=api_key)
        self.model = model
        self.timeout = timeout

    @property
    def _client(self) -> genai.Client:
        """The learner's own key when they saved one, otherwise the server's."""
        api_key = current_user_api_key()
        if api_key is None:
            return self._server_client
        return client_for_key("genai", api_key, lambda key: genai.Client(api_key=key))

    async def health(self) -> None:
        """Raise if the Gemini API is unreachable or the key is rejected."""
        await self._server_client.aio.models.get(model=self.model)

    @reports_ai_failure("stt")
    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str = "audio.wav",
        mime_type: str = "audio/wav",
        *,
        language: str,
    ) -> str:
        """Transcribe audio with Gemini's dedicated transcription model.

        The model detects the language itself and rejects developer instructions,
        so ``language`` is only logged.
        """
        response = await asyncio.wait_for(
            self._client.aio.models.generate_content(
                model=self.model,
                contents=[genai_types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)],
            ),
            timeout=self.timeout,
        )
        pieces: list[str] = []
        for candidate in response.candidates or []:
            for part in (candidate.content.parts if candidate.content else None) or []:
                transcription = getattr(part, "audio_transcription", None)
                if transcription is not None and transcription.text:
                    pieces.append(transcription.text)
                elif part.text:
                    pieces.append(part.text)
        text = " ".join(p.strip() for p in pieces if p.strip())
        logger.info("[stt-gemini] Transcribed model=%s lang=%s: %r", self.model, language, text)
        return text
