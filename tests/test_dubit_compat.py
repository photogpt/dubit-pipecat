#
# Copyright (c) 2024-2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import unittest
from unittest.mock import AsyncMock

from pipecat.frames.frames import (
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.services.stt_service import STTService
from pipecat.turns.user_turn_strategies import DubitExternalUserTurnStrategies


class DummySTTService(STTService):
    async def run_stt(self, audio: bytes):
        if False:
            yield audio


class TestDubitCompat(unittest.IsolatedAsyncioTestCase):
    async def test_push_transcription_with_turn_frames_pushes_only_transcription_by_default(
        self,
    ):
        service = DummySTTService.__new__(DummySTTService)
        service.push_frame = AsyncMock()
        frame = TranscriptionFrame("hello", "user", "ts")

        await STTService._push_transcription_with_turn_frames(service, frame)

        self.assertEqual(service.push_frame.await_count, 1)
        self.assertIs(service.push_frame.await_args_list[0].args[0], frame)

    async def test_push_transcription_with_turn_frames_wraps_with_standard_markers(self):
        service = DummySTTService.__new__(DummySTTService)
        service.push_frame = AsyncMock()
        frame = TranscriptionFrame("hello", "user", "ts")

        await STTService._push_transcription_with_turn_frames(
            service, frame, wrap_with_turn_frames=True
        )

        calls = service.push_frame.await_args_list
        self.assertEqual(len(calls), 3)
        self.assertIsInstance(calls[0].args[0], UserStartedSpeakingFrame)
        self.assertIs(calls[1].args[0], frame)
        self.assertIsInstance(calls[2].args[0], UserStoppedSpeakingFrame)

    async def test_llm_context_aggregator_pair_uses_dubit_strategies(self):
        pair = LLMContextAggregatorPair(LLMContext(), aggregator_type="dubit")

        self.assertIsInstance(
            pair.user()._params.user_turn_strategies, DubitExternalUserTurnStrategies
        )

    async def test_llm_context_aggregator_pair_rejects_unknown_aggregator_type(self):
        with self.assertRaisesRegex(ValueError, "Unknown aggregator_type"):
            LLMContextAggregatorPair(LLMContext(), aggregator_type="unknown")
