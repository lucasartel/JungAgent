"""Small deterministic guards for corrupted text extraction."""

import re


_OVERLAID_WORD = re.compile(r"([^\W\d_]{4,})\1{2,}", re.IGNORECASE)


def has_overlaid_words(text: str) -> bool:
    return bool(_OVERLAID_WORD.search(text or ""))
