#
# Copyright (c) 2024, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import re

ENDOFSENTENCE_PATTERN_STR = r"""
    (?<![A-Z])       # Negative lookbehind: not preceded by an uppercase letter (e.g., "U.S.A.")
    (?<!\d)          # Negative lookbehind: not preceded by a digit (e.g., "1. Let's start")
    (?<!\d\s[ap])    # Negative lookbehind: not preceded by time (e.g., "3:00 a.m.")
    (?<!Mr|Ms|Dr)    # Negative lookbehind: not preceded by Mr, Ms, Dr (combined bc. length is the same)
    (?<!Mrs)         # Negative lookbehind: not preceded by "Mrs"
    (?<!Prof)        # Negative lookbehind: not preceded by "Prof"
    [\.\?\!:;]|      # Match a period, question mark, exclamation point, colon, or semicolon
    [。？！：；]       # the full-width version (mainly used in East Asian languages such as Chinese)
    $                # End of string
"""
ENDOFSENTENCE_PATTERN = re.compile(ENDOFSENTENCE_PATTERN_STR, re.VERBOSE)


def match_endofsentence(text: str) -> int:
    match = ENDOFSENTENCE_PATTERN.search(text.rstrip())
    return match.end() if match else 0


ENDOFSENTENCES_PATTERN_STR = r"""
    (?<![A-Z])       # Negative lookbehind: not preceded by an uppercase letter (e.g., "U.S.A.")
    (?<!\d)          # Negative lookbehind: not preceded by a digit (e.g., "1. Let's start")
    (?<!\d\s[ap])    # Negative lookbehind: not preceded by time (e.g., "3:00 a.m.")
    (?<!Mr|Ms|Dr)    # Negative lookbehind: not preceded by Mr, Ms, Dr (combined bc. length is the same)
    (?<!Mrs)         # Negative lookbehind: not preceded by "Mrs"
    (?<!Prof)        # Negative lookbehind: not preceded by "Prof"
    (?:[\.\?\!:;]|   # Match a period, question mark, exclamation point, colon, or semicolon
    [。？！：；])       # the full-width version (mainly used in East Asian languages such as Chinese)
"""
ENDOFSENTENCES_PATTERN = re.compile(ENDOFSENTENCES_PATTERN_STR, re.VERBOSE)


def find_endofsentences(text: str) -> list[int]:
    end_indices = [match.end() for match in ENDOFSENTENCES_PATTERN.finditer(text)]
    # debug print
    return end_indices[-1] if end_indices else 0
