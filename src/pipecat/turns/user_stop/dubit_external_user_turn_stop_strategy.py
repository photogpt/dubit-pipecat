#
# Copyright (c) 2024-2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""User turn stop strategy triggered by Dubit-specific ordered frames."""

import asyncio
from typing import Optional

from pipecat.frames.frames import (
    DubitUserStartedSpeakingFrame,
    DubitUserStoppedSpeakingFrame,
    Frame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
)
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_stop.base_user_turn_stop_strategy import BaseUserTurnStopStrategy
from pipecat.utils.asyncio.task_manager import BaseTaskManager


class DubitExternalUserTurnStopStrategy(BaseUserTurnStopStrategy):
    """User turn stop strategy controlled by ordered Dubit frames."""

    def __init__(self, *, timeout: float = 0.5, **kwargs):
        super().__init__(enable_user_speaking_frames=False, **kwargs)
        self._timeout = timeout
        self._text = ""
        self._user_speaking = False
        self._seen_interim_results = False
        self._event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    async def reset(self):
        await super().reset()
        self._text = ""
        self._user_speaking = False
        self._seen_interim_results = False
        self._event.clear()

    async def setup(self, task_manager: BaseTaskManager):
        await super().setup(task_manager)
        self._task = task_manager.create_task(self._task_handler(), f"{self}::_task_handler")

    async def cleanup(self):
        await super().cleanup()
        if self._task:
            await self.task_manager.cancel_task(self._task)
            self._task = None

    async def process_frame(self, frame: Frame) -> ProcessFrameResult:
        if isinstance(frame, DubitUserStartedSpeakingFrame):
            await self._handle_user_started_speaking()
        elif isinstance(frame, DubitUserStoppedSpeakingFrame):
            await self._handle_user_stopped_speaking()
        elif isinstance(frame, InterimTranscriptionFrame):
            await self._handle_interim_transcription()
        elif isinstance(frame, TranscriptionFrame):
            await self._handle_transcription(frame)

        return ProcessFrameResult.CONTINUE

    async def _handle_user_started_speaking(self):
        self._user_speaking = True

    async def _handle_user_stopped_speaking(self):
        self._user_speaking = False
        await self._maybe_trigger_user_turn_stopped()

    async def _handle_interim_transcription(self):
        self._seen_interim_results = True

    async def _handle_transcription(self, frame: TranscriptionFrame):
        self._text += frame.text
        self._seen_interim_results = False
        self._event.set()

    async def _task_handler(self):
        while True:
            try:
                await asyncio.wait_for(self._event.wait(), timeout=self._timeout)
                self._event.clear()
            except asyncio.TimeoutError:
                await self._maybe_trigger_user_turn_stopped()

    async def _maybe_trigger_user_turn_stopped(self):
        if not self._user_speaking and not self._seen_interim_results and self._text:
            await self.trigger_user_turn_stopped()
