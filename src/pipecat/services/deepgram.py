#
# Copyright (c) 2024–2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import asyncio
from typing import AsyncGenerator

from loguru import logger

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    ErrorFrame,
    Frame,
    InterimTranscriptionFrame,
    StartFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.ai_services import STTService, TTSService
from pipecat.transcriptions.language import Language
from pipecat.utils.time import time_now_iso8601

# See .env.example for Deepgram configuration needed
try:
    from deepgram import (
        AsyncListenWebSocketClient,
        DeepgramClient,
        DeepgramClientOptions,
        LiveOptions,
        LiveResultResponse,
        LiveTranscriptionEvents,
        SpeakOptions,
    )
except ModuleNotFoundError as e:
    logger.error(f"Exception: {e}")
    logger.error(
        "In order to use Deepgram, you need to `pip install pipecat-ai[deepgram]`. Also, set `DEEPGRAM_API_KEY` environment variable."
    )
    raise Exception(f"Missing module: {e}")


class DeepgramTTSService(TTSService):
    def __init__(
        self,
        *,
        api_key: str,
        voice: str = "aura-helios-en",
        sample_rate: int = 24000,
        encoding: str = "linear16",
        **kwargs,
    ):
        super().__init__(sample_rate=sample_rate, **kwargs)

        self._settings = {
            "sample_rate": sample_rate,
            "encoding": encoding,
        }
        self.set_voice(voice)
        self._deepgram_client = DeepgramClient(api_key=api_key)

    def can_generate_metrics(self) -> bool:
        return True

    async def run_tts(self, text: str) -> AsyncGenerator[Frame, None]:
        logger.debug(f"Generating TTS: [{text}]")

        options = SpeakOptions(
            model=self._voice_id,
            encoding=self._settings["encoding"],
            sample_rate=self._settings["sample_rate"],
            container="none",
        )

        try:
            await self.start_ttfb_metrics()

            response = await asyncio.to_thread(
                self._deepgram_client.speak.v("1").stream, {"text": text}, options
            )

            await self.start_tts_usage_metrics(text)
            yield TTSStartedFrame()

            # The response.stream_memory is already a BytesIO object
            audio_buffer = response.stream_memory

            if audio_buffer is None:
                raise ValueError("No audio data received from Deepgram")

            # Read and yield the audio data in chunks
            audio_buffer.seek(0)  # Ensure we're at the start of the buffer
            chunk_size = 8192  # Use a fixed buffer size
            while True:
                await self.stop_ttfb_metrics()
                chunk = audio_buffer.read(chunk_size)
                if not chunk:
                    break
                frame = TTSAudioRawFrame(
                    audio=chunk, sample_rate=self._settings["sample_rate"], num_channels=1
                )
                yield frame

                yield TTSStoppedFrame()

        except Exception as e:
            logger.exception(f"{self} exception: {e}")
            yield ErrorFrame(f"Error getting audio: {str(e)}")


class DeepgramSTTService(STTService):
    def __init__(
        self,
        *,
        api_key: str,
        url: str = "",
        live_options: LiveOptions = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        default_options = LiveOptions(
            encoding="linear16",
            language=Language.EN,
            model="nova-2-general",
            sample_rate=16000,
            channels=1,
            interim_results=True,
            smart_format=True,
            punctuate=True,
            profanity_filter=True,
            vad_events=False,
        )

        merged_options = default_options
        if live_options:
            merged_options = LiveOptions(**{**default_options.to_dict(), **live_options.to_dict()})

        # deepgram connection requires language to be a string
        if isinstance(merged_options.language, Language) and hasattr(
            merged_options.language, "value"
        ):
            merged_options.language = merged_options.language.value

        self._settings = merged_options.to_dict()

        self._client = DeepgramClient(
            api_key,
            config=DeepgramClientOptions(
                url=url,
                options={"keepalive": "true"},  # verbose=logging.DEBUG
            ),
        )
        self._connection: AsyncListenWebSocketClient = self._client.listen.asyncwebsocket.v("1")
        self._connection.on(LiveTranscriptionEvents.Transcript, self._on_message)
        if self.vad_enabled:
            self._register_event_handler("on_speech_started")
            self._register_event_handler("on_utterance_end")
            self._connection.on(LiveTranscriptionEvents.SpeechStarted, self._on_speech_started)
            self._connection.on(LiveTranscriptionEvents.UtteranceEnd, self._on_utterance_end)

    @property
    def vad_enabled(self):
        return self._settings["vad_events"]

    def can_generate_metrics(self) -> bool:
        return True

    async def set_model(self, model: str):
        await super().set_model(model)
        logger.info(f"Switching STT model to: [{model}]")
        self._settings["model"] = model
        await self._disconnect()
        await self._connect()

    async def set_language(self, language: Language):
        logger.info(f"Switching STT language to: [{language}]")
        self._settings["language"] = language
        await self._disconnect()
        await self._connect()

    async def start(self, frame: StartFrame):
        await super().start(frame)
        await self._connect()

    async def stop(self, frame: EndFrame):
        await super().stop(frame)
        await self._disconnect()

    async def cancel(self, frame: CancelFrame):
        await super().cancel(frame)
        await self._disconnect()

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        await self._connection.send(audio)
        yield None

    async def _connect(self):
        logger.debug("Connecting to Deepgram")
        if not await self._connection.start(self._settings):
            logger.error(f"{self}: unable to connect to Deepgram")

    async def _disconnect(self):
        if self._connection.is_connected:
            logger.debug("Disconnecting from Deepgram")
            await self._connection.finish()

    async def start_metrics(self):
        await self.start_ttfb_metrics()
        await self.start_processing_metrics()

    async def _on_speech_started(self, *args, **kwargs):
        if self.vad_enabled:
            logger.debug("=========== Deepgram =========== Speech started")
            await self.push_frame(UserStartedSpeakingFrame())
        await self.start_metrics()
        await self._call_event_handler("on_speech_started", *args, **kwargs)

    async def _on_utterance_end(self, *args, **kwargs):
        await self._call_event_handler("on_utterance_end", *args, **kwargs)

    async def _on_message(self, *args, **kwargs):
        result: LiveResultResponse = kwargs["result"]
        if len(result.channel.alternatives) == 0:
            return
        is_final = result.is_final
        transcript = result.channel.alternatives[0].transcript
        language = None
        if result.channel.alternatives[0].languages:
            language = result.channel.alternatives[0].languages[0]
            language = Language(language)
        if len(transcript) > 0:
            await self.stop_ttfb_metrics()
            if is_final:
                await self.push_frame(
                    TranscriptionFrame(transcript, "", time_now_iso8601(), language)
                )
                logger.debug(f">> Deepgram: {transcript}")
                if self.vad_enabled:
                    logger.debug("~~~~~~~~~~~ Deepgram ~~~~~~~~~~~ Speech stopped")
                    await self.push_frame(UserStoppedSpeakingFrame())
                    # Below line is really important; do not remove this as this is necessary to keep the conversation flow
                    # while using deepgram as a vad we need to make sure that we correctly trigger UserStartedSpeakingFrame
                    # as it's necessary for user response aggregators; if this frame is not pushed from here and deepgram also
                    # doesn't trigger the on_speech_started event we will lose the transcribed text as the flow of frames would be
                    # Previous UserStoppedSpeakingFrame
                    # Current Transcription/InterimTranscription Frames
                    # Another UserStoppedSpeakingFrame
                    # However the flow should be: Prev Stop; Curr Start -> Curr Transcription -> Curr Stop; Next Start and so on...
                    # logger.debug("=========== Deepgram =========== Forced Speech started")
                    # await self.push_frame(UserStartedSpeakingFrame())
                await self.stop_processing_metrics()
            else:
                await self.push_frame(
                    InterimTranscriptionFrame(transcript, "", time_now_iso8601(), language)
                )

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, UserStartedSpeakingFrame) and not self.vad_enabled:
            # Start metrics if Deepgram VAD is disabled & pipeline VAD has detected speech
            await self.start_metrics()
        elif isinstance(frame, UserStoppedSpeakingFrame):
            # https://developers.deepgram.com/docs/finalize
            if not self.vad_enabled:
                await self._connection.finalize()
                logger.trace(f"Triggered finalize event on: {frame.name=}, {direction=}")
