#
# Copyright (c) 2024–2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Azure Cognitive Services Text-to-Speech service implementations."""

import asyncio
import re
from typing import AsyncGenerator, List, Optional, Tuple

from loguru import logger
from pydantic import BaseModel

from pipecat.frames.frames import (
    ErrorFrame,
    Frame,
    StartFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.services.azure.common import language_to_azure_language
from pipecat.services.tts_service import TTSService
from pipecat.transcriptions.language import Language
from pipecat.utils.tracing.service_decorators import traced_tts

try:
    from azure.cognitiveservices.speech import (
        CancellationReason,
        ResultReason,
        ServicePropertyChannel,
        SpeechConfig,
        SpeechSynthesisOutputFormat,
        SpeechSynthesizer,
    )
except ModuleNotFoundError as e:
    logger.error(f"Exception: {e}")
    logger.error("In order to use Azure, you need to `pip install pipecat-ai[azure]`.")
    raise Exception(f"Missing module: {e}")


def sample_rate_to_output_format(sample_rate: int) -> SpeechSynthesisOutputFormat:
    """Convert sample rate to Azure speech synthesis output format.

    Args:
        sample_rate: Sample rate in Hz.

    Returns:
        Corresponding Azure SpeechSynthesisOutputFormat enum value.
        Defaults to Raw24Khz16BitMonoPcm if sample rate not found.
    """
    sample_rate_map = {
        8000: SpeechSynthesisOutputFormat.Raw8Khz16BitMonoPcm,
        16000: SpeechSynthesisOutputFormat.Raw16Khz16BitMonoPcm,
        22050: SpeechSynthesisOutputFormat.Raw22050Hz16BitMonoPcm,
        24000: SpeechSynthesisOutputFormat.Raw24Khz16BitMonoPcm,
        44100: SpeechSynthesisOutputFormat.Raw44100Hz16BitMonoPcm,
        48000: SpeechSynthesisOutputFormat.Raw48Khz16BitMonoPcm,
    }
    return sample_rate_map.get(sample_rate, SpeechSynthesisOutputFormat.Raw24Khz16BitMonoPcm)


class AzureBaseTTSService(TTSService):
    """Base class for Azure Cognitive Services text-to-speech implementations.

    Provides common functionality for Azure TTS services including SSML
    construction, voice configuration, and parameter management.
    """

    # Define SSML escape mappings based on SSML reserved characters
    # See - https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-synthesis-markup-structure
    SSML_ESCAPE_CHARS = {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&apos;",
    }

    class InputParams(BaseModel):
        """Input parameters for Azure TTS voice configuration.

        Parameters:
            emphasis: Emphasis level for speech ("strong", "moderate", "reduced").
            language: Language for synthesis. Defaults to English (US).
            pitch: Voice pitch adjustment (e.g., "+10%", "-5Hz", "high").
            rate: Speech rate multiplier. Defaults to "1.05".
            role: Voice role for expression (e.g., "YoungAdultFemale").
            style: Speaking style (e.g., "cheerful", "sad", "excited").
            style_degree: Intensity of the speaking style (0.01 to 2.0).
            volume: Volume level (e.g., "+20%", "loud", "x-soft").
        """

        emphasis: Optional[str] = None
        language_code: Optional[str] = (
            "en-US"  # FIXME: DEPRICATE THIS. necessary for compatibility with languages supported by azure but not by other service
        )
        language: Optional[Language] = Language.EN_US
        pitch: Optional[str] = None
        rate: Optional[str] = "1.05"
        role: Optional[str] = None
        style: Optional[str] = None
        style_degree: Optional[str] = None
        volume: Optional[str] = None

    def __init__(
        self,
        *,
        api_key: str,
        region: str,
        voice="en-US-SaraNeural",
        sample_rate: Optional[int] = None,
        params: Optional[InputParams] = None,
        **kwargs,
    ):
        """Initialize the Azure TTS service with configuration parameters.

        Args:
            api_key: Azure Cognitive Services subscription key.
            region: Azure region identifier (e.g., "eastus", "westus2").
            voice: Voice name to use for synthesis. Defaults to "en-US-SaraNeural".
            sample_rate: Audio sample rate in Hz. If None, uses service default.
            params: Voice and synthesis parameters configuration.
            **kwargs: Additional arguments passed to parent TTSService.
        """
        super().__init__(sample_rate=sample_rate, **kwargs)

        params = params or AzureBaseTTSService.InputParams()

        self._settings = {
            "emphasis": params.emphasis,
            "language": self.language_to_service_language(params.language)
            if params.language
            else "en-US",
            "language_code": params.language_code,
            "pitch": params.pitch,
            "rate": params.rate,
            "role": params.role,
            "style": params.style,
            "style_degree": params.style_degree,
            "volume": params.volume,
        }

        self._api_key = api_key
        self._region = region
        self._voice_id = voice
        self._speech_synthesizer = None

    def can_generate_metrics(self) -> bool:
        """Check if this service can generate processing metrics.

        Returns:
            True, as Azure TTS service supports metrics generation.
        """
        return True

    def language_to_service_language(self, language: Language) -> Optional[str]:
        """Convert a Language enum to Azure language format.

        Args:
            language: The language to convert.

        Returns:
            The Azure-specific language code, or None if not supported.
        """
        return language_to_azure_language(language)

    def _construct_ssml(self, text: str) -> str:
        language = self._settings["language_code"]

        # Escape special characters
        escaped_text = self._escape_text_with_tag_support(text, [("<say-as>", "</say-as>")])

        ssml = (
            f"<speak version='1.0' xml:lang='{language}' "
            "xmlns='http://www.w3.org/2001/10/synthesis' "
            "xmlns:mstts='http://www.w3.org/2001/mstts'>"
            f"<voice name='{self._voice_id}'>"
            "<mstts:silence type='Sentenceboundary' value='20ms' />"
        )

        if self._settings["style"]:
            ssml += f"<mstts:express-as style='{self._settings['style']}'"
            if self._settings["style_degree"]:
                ssml += f" styledegree='{self._settings['style_degree']}'"
            if self._settings["role"]:
                ssml += f" role='{self._settings['role']}'"
            ssml += ">"

        prosody_attrs = []
        if self._settings["rate"]:
            prosody_attrs.append(f"rate='{self._settings['rate']}'")
        if self._settings["pitch"]:
            prosody_attrs.append(f"pitch='{self._settings['pitch']}'")
        if self._settings["volume"]:
            prosody_attrs.append(f"volume='{self._settings['volume']}'")

        ssml += f"<prosody {' '.join(prosody_attrs)}>"

        if self._settings["emphasis"]:
            ssml += f"<emphasis level='{self._settings['emphasis']}'>"

        ssml += escaped_text

        if self._settings["emphasis"]:
            ssml += "</emphasis>"

        ssml += "</prosody>"

        if self._settings["style"]:
            ssml += "</mstts:express-as>"

        ssml += "</voice></speak>"

        return ssml

    def _escape_text_with_tag_support(
        self, text: str, tag_pairs: Optional[List[Tuple[str, str]]] = None
    ) -> str:
        """Extends self._escape_text with support for text containing tags like <say-as>...</say-as>.

        Escaping is applied only to text outside of the specified tag pairs (including
        the tags themselves, which are left unescaped). Assumes no nesting of the same
        tag type and properly paired tags. Unmatched opening tags are treated as plain
        text and escaped. Stray closing tags are escaped as plain text.

        Args:
            text: The text to escape.
            tag_pairs: List of (start_tag_example, end_tag_example) tuples, e.g.,
                       [("<say-as>", "</say-as>"), ("<abc>", "</abc>")].

        Returns:
            The escaped text.
        """
        if not text:
            return text

        if tag_pairs is None:
            return self._escape_text(text)

        # Build regexes for each pair
        tag_configs = []
        _SIMPLE_TAG_PATTERN = re.compile(r"^<([a-zA-Z0-9_:-]+)>$")

        for start_ex, end_ex in tag_pairs:
            simple_match = _SIMPLE_TAG_PATTERN.match(start_ex)
            if not simple_match:
                continue
            tag_name = simple_match.group(1)
            start_regex = re.compile(rf"<{re.escape(tag_name)}(?:\s+[^>]*)?>")
            end_regex = re.compile(rf"</{re.escape(tag_name)}\s*>")
            tag_configs.append((start_regex, end_regex))

        if not tag_configs:
            return self._escape_text(text)

        escaped_parts = []
        current_pos = 0
        while current_pos < len(text):
            # Find the next start of any type after current_pos
            next_start = None
            next_start_pos = float("inf")
            next_config_idx = -1
            for idx, (start_regex, _) in enumerate(tag_configs):
                match = start_regex.search(text, current_pos)
                if match and match.start() < next_start_pos:
                    next_start_pos = match.start()
                    next_start = match
                    next_config_idx = idx

            if next_start is None:
                # No more starts, escape the rest
                escaped_parts.append(self._escape_text(text[current_pos:]))
                break

            # Append escaped up to it
            escaped_parts.append(self._escape_text(text[current_pos : next_start.start()]))

            # Now, get the config
            start_regex, end_regex = tag_configs[next_config_idx]

            # Search for end after this start's end
            end_match = end_regex.search(text, next_start.end())
            if end_match and end_match.start() >= next_start.end():
                # Matched, append raw from start to end
                escaped_parts.append(text[next_start.start() : end_match.end()])
                current_pos = end_match.end()
            else:
                # No match, escape the start tag itself
                start_tag_text = text[next_start.start() : next_start.end()]
                escaped_parts.append(self._escape_text(start_tag_text))
                current_pos = next_start.end()

        return "".join(escaped_parts)

    def _escape_text(self, text: str) -> str:
        """Escapes XML/SSML reserved characters according to Microsoft documentation.

        This method escapes the following characters:
        - & becomes &amp;
        - < becomes &lt;
        - > becomes &gt;
        - " becomes &quot;
        - ' becomes &apos;

        Args:
            text: The text to escape.

        Returns:
            The escaped text.
        """
        escaped_text = text
        for char, escape_code in AzureBaseTTSService.SSML_ESCAPE_CHARS.items():
            escaped_text = escaped_text.replace(char, escape_code)
        return escaped_text


class AzureTTSService(AzureBaseTTSService):
    """Azure Cognitive Services streaming TTS service.

    Provides real-time text-to-speech synthesis using Azure's WebSocket-based
    streaming API. Audio chunks are streamed as they become available for
    lower latency playback.
    """

    def __init__(self, **kwargs):
        """Initialize the Azure streaming TTS service.

        Args:
            **kwargs: All arguments passed to AzureBaseTTSService parent class.
        """
        super().__init__(**kwargs)
        self._speech_config = None
        self._speech_synthesizer = None
        self._audio_queue = asyncio.Queue()

    async def start(self, frame: StartFrame):
        """Start the Azure TTS service and initialize speech synthesizer.

        Args:
            frame: Start frame containing initialization parameters.
        """
        await super().start(frame)

        if self._speech_config:
            return

        # Now self.sample_rate is properly initialized
        self._speech_config = SpeechConfig(
            subscription=self._api_key,
            region=self._region,
        )
        self._speech_config.speech_synthesis_language = self._settings["language_code"]
        self._speech_config.set_speech_synthesis_output_format(
            sample_rate_to_output_format(self.sample_rate)
        )
        self._speech_config.set_service_property(
            "synthesizer.synthesis.connection.synthesisConnectionImpl",
            "websocket",
            ServicePropertyChannel.UriQueryParameter,
        )

        self._speech_synthesizer = SpeechSynthesizer(
            speech_config=self._speech_config, audio_config=None
        )

        # Set up event handlers
        self._speech_synthesizer.synthesizing.connect(self._handle_synthesizing)
        self._speech_synthesizer.synthesis_completed.connect(self._handle_completed)
        self._speech_synthesizer.synthesis_canceled.connect(self._handle_canceled)

    def _handle_synthesizing(self, evt):
        """Handle audio chunks as they arriv."""
        if evt.result and evt.result.audio_data:
            self._audio_queue.put_nowait(evt.result.audio_data)

    def _handle_completed(self, evt):
        """Handle synthesis completion."""
        self._audio_queue.put_nowait(None)  # Signal completion

    def _handle_canceled(self, evt):
        """Handle synthesis cancellation."""
        logger.error(f"Speech synthesis canceled: {evt.result.cancellation_details.reason}")
        self._audio_queue.put_nowait(None)

    async def flush_audio(self):
        """Flush any pending audio data."""
        logger.trace(f"{self}: flushing audio")

    @traced_tts
    async def run_tts(self, text: str) -> AsyncGenerator[Frame, None]:
        """Generate speech from text using Azure's streaming synthesis.

        Args:
            text: The text to synthesize into speech.

        Yields:
            Frame: Audio frames containing synthesized speech data.
        """
        logger.debug(f"{self}: Generating TTS [{text}]")

        # Clear the audio queue in case there's still audio in it, causing the next audio response
        # to be cut off by the 'None' element returned at the end of the previous audio synthesis.
        # Empty the audio queue before processing the new text
        while not self._audio_queue.empty():
            self._audio_queue.get_nowait()
            self._audio_queue.task_done()

        try:
            if self._speech_synthesizer is None:
                error_msg = "Speech synthesizer not initialized."
                logger.error(error_msg)
                yield ErrorFrame(error_msg)
                return

            try:
                await self.start_ttfb_metrics()
                yield TTSStartedFrame()

                ssml = self._construct_ssml(text)
                self._speech_synthesizer.speak_ssml_async(ssml)
                await self.start_tts_usage_metrics(text)

                # Stream audio chunks as they arrive
                while True:
                    chunk = await self._audio_queue.get()
                    if chunk is None:  # End of stream
                        break

                    await self.stop_ttfb_metrics()
                    yield TTSAudioRawFrame(
                        audio=chunk,
                        sample_rate=self.sample_rate,
                        num_channels=1,
                    )

                yield TTSStoppedFrame()

            except Exception as e:
                logger.error(f"{self} error during synthesis: {e}")
                yield TTSStoppedFrame()
                # Could add reconnection logic here if needed
                return

        except Exception as e:
            logger.error(f"{self} exception: {e}")


class AzureHttpTTSService(AzureBaseTTSService):
    """Azure Cognitive Services HTTP-based TTS service.

    Provides text-to-speech synthesis using Azure's HTTP API for simpler,
    non-streaming synthesis. Suitable for use cases where streaming is not
    required and simpler integration is preferred.
    """

    def __init__(self, **kwargs):
        """Initialize the Azure HTTP TTS service.

        Args:
            **kwargs: All arguments passed to AzureBaseTTSService parent class.
        """
        super().__init__(**kwargs)
        self._speech_config = None
        self._speech_synthesizer = None

    async def start(self, frame: StartFrame):
        """Start the Azure HTTP TTS service and initialize speech synthesizer.

        Args:
            frame: Start frame containing initialization parameters.
        """
        await super().start(frame)

        if self._speech_config:
            return

        self._speech_config = SpeechConfig(
            subscription=self._api_key,
            region=self._region,
        )
        self._speech_config.speech_synthesis_language = self._settings["language"]
        self._speech_config.set_speech_synthesis_output_format(
            sample_rate_to_output_format(self.sample_rate)
        )
        self._speech_synthesizer = SpeechSynthesizer(
            speech_config=self._speech_config, audio_config=None
        )

    @traced_tts
    async def run_tts(self, text: str) -> AsyncGenerator[Frame, None]:
        """Generate speech from text using Azure's HTTP synthesis API.

        Args:
            text: The text to synthesize into speech.

        Yields:
            Frame: Audio frames containing the complete synthesized speech.
        """
        logger.debug(f"{self}: Generating TTS [{text}]")

        await self.start_ttfb_metrics()

        ssml = self._construct_ssml(text)

        result = await asyncio.to_thread(self._speech_synthesizer.speak_ssml, ssml)

        if result.reason == ResultReason.SynthesizingAudioCompleted:
            await self.start_tts_usage_metrics(text)
            await self.stop_ttfb_metrics()
            yield TTSStartedFrame()
            # Azure always sends a 44-byte header. Strip it off.
            yield TTSAudioRawFrame(
                audio=result.audio_data[44:],
                sample_rate=self.sample_rate,
                num_channels=1,
            )
            yield TTSStoppedFrame()
        elif result.reason == ResultReason.Canceled:
            cancellation_details = result.cancellation_details
            logger.warning(f"Speech synthesis canceled: {cancellation_details.reason}")
            if cancellation_details.reason == CancellationReason.Error:
                logger.error(f"{self} error: {cancellation_details.error_details}")
