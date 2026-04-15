#
# Copyright (c) 2024-2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""User turn start strategy triggered by Dubit-specific ordered frames."""

from pipecat.frames.frames import DubitUserStartedSpeakingFrame, Frame
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_start.base_user_turn_start_strategy import BaseUserTurnStartStrategy


class DubitExternalUserTurnStartStrategy(BaseUserTurnStartStrategy):
    """User turn start strategy controlled by ordered Dubit frames."""

    def __init__(self, **kwargs):
        super().__init__(enable_interruptions=False, enable_user_speaking_frames=False, **kwargs)

    async def process_frame(self, frame: Frame) -> ProcessFrameResult:
        if isinstance(frame, DubitUserStartedSpeakingFrame):
            await self.trigger_user_turn_started()
            return ProcessFrameResult.STOP

        return ProcessFrameResult.CONTINUE
