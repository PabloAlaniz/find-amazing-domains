"""The ``_for-sale`` DNS record: a holder saying "this name can be bought".

A holder who wants to sell ``example.nl`` publishes a TXT record at
``_for-sale.example.nl`` whose content starts with the version tag
``v=FORSALE1;`` (the IETF draft "The '_for-sale' Underscored and Globally
Scoped DNS Node Name", draft-davids-forsalereg; SIDN shows these on .nl).
What follows the tag is optional and has changed between versions of the
draft (a URL, a price, free text), so it is kept as text and not interpreted.

This module is pure: give it the TXT strings found at the node.
"""

from __future__ import annotations

from collections.abc import Iterable

FOR_SALE_LABEL = "_for-sale"
_VERSION_TAG = "v=forsale1;"
# What a record says is the holder's text: bound it and keep it on one line.
MAX_CONTENT_LENGTH = 200
LISTED = "listed"


def for_sale_content(txt_records: Iterable[str]) -> str:
    """What the first valid ``_for-sale`` record says, ``"listed"`` when it says
    nothing more, or ``""`` when no record carries the version tag."""
    for record in txt_records:
        text = record.strip()
        if not text.lower().startswith(_VERSION_TAG):
            continue
        content = "".join(c for c in text[len(_VERSION_TAG) :] if c.isprintable()).strip()
        return content[:MAX_CONTENT_LENGTH] or LISTED
    return ""
