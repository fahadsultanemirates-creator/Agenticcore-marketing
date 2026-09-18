"""Order-independent parsing of the labelled blocks research agents return.

A first version matched fields in a fixed sequence, which quietly broke the
moment a model emitted PRIORITY before INTENT: the optional groups failed
and the preceding field swallowed the rest of the line. Models vary field
order freely, so position must not carry meaning — each label is found
independently and its value runs to whichever label comes next.
"""

from __future__ import annotations

import re
from typing import Iterable


def split_blocks(text: str, primary_label: str) -> list[str]:
    """Split a reply into one chunk per record.

    Splits on a ``###`` heading where the model used one, and otherwise on
    each occurrence of the primary label, so a reply that drops the headings
    still yields separate records instead of one giant blob.
    """

    if "###" in text:
        chunks = [c for c in re.split(r"^\s*#{2,}.*$", text, flags=re.MULTILINE)]
    else:
        chunks = re.split(rf"(?=^\s*{primary_label}\s*:)", text, flags=re.MULTILINE | re.IGNORECASE)
    return [c for c in chunks if re.search(rf"^\s*{primary_label}\s*:", c,
                                           re.MULTILINE | re.IGNORECASE)]


def parse_fields(block: str, labels: Iterable[str]) -> dict[str, str]:
    """Pull ``labels`` out of one block regardless of the order they appear in."""

    labels = list(labels)
    alternation = "|".join(re.escape(l) for l in labels)
    found: dict[str, str] = {}
    for label in labels:
        match = re.search(
            rf"^\s*{re.escape(label)}\s*:\s*(?P<value>.*?)"
            rf"(?=^\s*(?:{alternation})\s*:|\Z)",
            block,
            re.IGNORECASE | re.DOTALL | re.MULTILINE,
        )
        if match:
            found[label.lower()] = " ".join(match.group("value").split())
    return found
