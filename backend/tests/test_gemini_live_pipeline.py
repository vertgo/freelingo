"""Unit tests for GeminiLivePipeline: turn bookkeeping, barge-in and audio relay."""

from __future__ import annotations

import io
import struct
import wave
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.genai import types

from app.services.gemini_live_pipeline import (
    FIRST_AUDIO_FLUSH_BYTES,
    GeminiLivePipeline,
    wav_to_pcm16,
)


class FakeWS:
    def __init__(self):
        self.sent: list[tuple[str, object]] = []

    async def send_json(self, data: object) -> None:
        self.sent.append(("json", data))

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(("bytes", data))

    def json(self, kind: str | None = None) -> list[dict]:
        return [d for t, d in self.sent if t == "json" and (kind is None or d["type"] == kind)]

    def audio(self) -> list[bytes]:
        return [d for t, d in self.sent if t == "bytes"]


def _wav(samples: list[int], rate: int = 16000, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buf.getvalue()


def _pipeline() -> GeminiLivePipeline:
    with patch("app.services.gemini_live_pipeline.genai.Client"):
        pipeline = GeminiLivePipeline(
            api_key="test",
            model="gemini-3.8-live",
            live_voice="Kore",
            cefr_level="A2",
            target_language="es-ES",
            native_language="en",
        )
    pipeline._session = MagicMock()
    pipeline._session.send_realtime_input = AsyncMock()
    pipeline._session_ready.set()
    return pipeline


def _content(**kwargs) -> types.LiveServerContent:
    return types.LiveServerContent(**kwargs)


def _audio(n: int) -> types.LiveServerContent:
    return _content(
        model_turn=types.Content(
            role="model",
            parts=[types.Part(inline_data=types.Blob(data=b"\x01\x00" * (n // 2)))],
        )
    )


def test_wav_to_pcm16_passes_16k_mono_through():
    assert wav_to_pcm16(_wav([1, 2, 3])) == struct.pack("<3h", 1, 2, 3)


def test_wav_to_pcm16_downmixes_and_resamples():
    stereo_32k = _wav([10, 30] * 8, rate=32000, channels=2)
    pcm = wav_to_pcm16(stereo_32k)
    assert struct.unpack(f"<{len(pcm) // 2}h", pcm) == (20,) * 4


@pytest.mark.asyncio
async def test_utterance_is_sent_as_one_manual_activity():
    pipeline, ws = _pipeline(), FakeWS()
    await pipeline.handle_audio(_wav([0] * 4000), ws)

    calls = pipeline._session.send_realtime_input.await_args_list
    assert "activity_start" in calls[0].kwargs
    assert "activity_end" in calls[-1].kwargs
    sent = b"".join(c.kwargs["audio"].data for c in calls[1:-1])
    assert len(sent) == 8000
    assert [m["value"] for m in ws.json("status")] == ["transcribing", "thinking"]


@pytest.mark.asyncio
async def test_full_turn_relays_transcripts_audio_and_saves_history():
    pipeline, ws = _pipeline(), FakeWS()
    pipeline._save_turn = AsyncMock()
    await pipeline.handle_audio(_wav([0] * 1600), ws)
    turn_id = pipeline._turns[0].turn_id

    await pipeline._handle_server_content(
        ws, _content(input_transcription=types.Transcription(text="Ayer voy al mercado"))
    )
    await pipeline._handle_server_content(ws, _audio(FIRST_AUDIO_FLUSH_BYTES))
    await pipeline._handle_server_content(
        ws, _content(output_transcription=types.Transcription(text="¿Fuiste al mercado?"))
    )
    await pipeline._handle_server_content(ws, _audio(100))
    await pipeline._handle_server_content(ws, _content(generation_complete=True))
    await pipeline._handle_server_content(ws, _content(turn_complete=True))

    transcripts = ws.json("transcript")
    assert transcripts[0] == {
        "type": "transcript",
        "role": "user",
        "text": "Ayer voy al mercado",
        "final": True,
        "turn_id": turn_id,
    }
    assert transcripts[-1]["role"] == "assistant"
    assert transcripts[-1]["final"] is True
    assert transcripts[-1]["text"] == "¿Fuiste al mercado?"
    chunks = ws.audio()
    assert len(chunks) == 2 and all(c.startswith(b"RIFF") for c in chunks)
    assert ws.json("turn_complete") == [{"type": "turn_complete", "turn_id": turn_id}]
    assert pipeline.history[-2:] == [
        {"role": "user", "content": "Ayer voy al mercado"},
        {"role": "assistant", "content": "¿Fuiste al mercado?"},
    ]
    assert pipeline._turns == []


@pytest.mark.asyncio
async def test_barge_in_drops_stale_reply_and_answers_new_utterance():
    pipeline, ws = _pipeline(), FakeWS()
    pipeline._save_turn = AsyncMock()
    await pipeline.handle_audio(_wav([0] * 1600), ws)
    await pipeline._handle_server_content(ws, _audio(FIRST_AUDIO_FLUSH_BYTES))
    assert len(ws.audio()) == 1

    await pipeline.handle_audio(_wav([0] * 1600), ws)
    assert ws.json("barge_in") == [{"type": "barge_in"}]
    new_turn = pipeline._turns[-1]

    # Stale audio that was already in flight is not forwarded.
    await pipeline._handle_server_content(ws, _audio(FIRST_AUDIO_FLUSH_BYTES))
    await pipeline._handle_server_content(ws, _content(interrupted=True))
    await pipeline._handle_server_content(ws, _content(turn_complete=True))
    assert len(ws.audio()) == 1
    assert pipeline._turns == [new_turn]

    await pipeline._handle_server_content(
        ws, _content(input_transcription=types.Transcription(text="¿Cómo se dice car?"))
    )
    await pipeline._handle_server_content(ws, _audio(FIRST_AUDIO_FLUSH_BYTES))
    await pipeline._handle_server_content(
        ws, _content(output_transcription=types.Transcription(text="Carro."))
    )
    await pipeline._handle_server_content(ws, _content(turn_complete=True))
    assert len(ws.audio()) == 2
    assert ws.json("turn_complete")[-1]["turn_id"] == new_turn.turn_id
    pipeline._save_turn.assert_called_once()


@pytest.mark.asyncio
async def test_unanswered_utterance_is_merged_into_the_next():
    pipeline, ws = _pipeline(), FakeWS()
    await pipeline.handle_audio(_wav([0] * 1600), ws)
    await pipeline._handle_server_content(
        ws, _content(input_transcription=types.Transcription(text="Hola, "))
    )
    await pipeline.handle_audio(_wav([0] * 1600), ws)

    assert ws.json("barge_in") == []
    assert len(pipeline._turns) == 1
    assert pipeline._turns[0].user_text == "Hola, "


@pytest.mark.asyncio
async def test_unmarked_interrupt_keeps_turn_open_for_real_answer():
    pipeline, ws = _pipeline(), FakeWS()
    pipeline._save_turn = AsyncMock()
    await pipeline.handle_audio(_wav([0] * 1600), ws)
    turn = pipeline._turns[0]
    turn.user_text = "Hola"
    await pipeline._handle_server_content(ws, _content(interrupted=True))
    await pipeline._handle_server_content(ws, _content(turn_complete=True))
    assert pipeline._turns == [turn]
    assert ws.json("turn_complete") == []

    await pipeline._handle_server_content(ws, _audio(FIRST_AUDIO_FLUSH_BYTES))
    await pipeline._handle_server_content(
        ws, _content(output_transcription=types.Transcription(text="¡Hola!"))
    )
    await pipeline._handle_server_content(ws, _content(turn_complete=True))
    assert ws.json("turn_complete") == [{"type": "turn_complete", "turn_id": turn.turn_id}]


@pytest.mark.asyncio
async def test_interrupt_message_marks_reply_stale():
    pipeline, ws = _pipeline(), FakeWS()
    await pipeline.handle_audio(_wav([0] * 1600), ws)
    await pipeline._handle_server_content(ws, _audio(FIRST_AUDIO_FLUSH_BYTES))
    await pipeline.cancel_current()
    await pipeline._handle_server_content(ws, _audio(FIRST_AUDIO_FLUSH_BYTES))
    assert len(ws.audio()) == 1


def test_live_config_disables_gemini_vad_and_enables_resumption():
    pipeline = _pipeline()
    pipeline._resume_handle = "handle-1"
    config = pipeline._live_config()
    assert config.realtime_input_config.automatic_activity_detection.disabled is True
    assert config.session_resumption.handle == "handle-1"
    assert config.context_window_compression.sliding_window is not None
    assert "Mexican Spanish" in config.system_instruction or "Spanish" in config.system_instruction
