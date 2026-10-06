"""HTTP identity shared by every adapter that talks to a registry over HTTP.

RFC 9110 §10.1.5: the User-Agent names the client honestly (no browser
tokens) and points to the project. §10.1.2: a robotic client SHOULD send a
``From`` header with a contact address, so an operator can reach the user
instead of silently blocking the IP; it is opt-in (``--contact``).
"""

from __future__ import annotations

from domainhack import __version__

PROJECT_URL = "https://github.com/PabloAlaniz/find-amazing-domains"
USER_AGENT = f"domainhack/{__version__} (+{PROJECT_URL})"


def identity_headers(contact: str | None = None) -> dict[str, str]:
    """``User-Agent`` (always) plus ``From: <contact>`` when a contact is given."""
    headers = {"User-Agent": USER_AGENT}
    if contact:
        headers["From"] = contact
    return headers
