"""HTTP page fetcher.

Retrieves text only. It does not execute scripts, does not follow arbitrary
redirects off-host, and strips markup, because retrieved content is hostile input
and the fetcher's job is to hand the guard something inert to classify.
"""

from __future__ import annotations

import re
from html import unescape
from urllib.parse import urljoin, urlparse

_USER_AGENT = "architecture-agent/0.1 (+https://github.com/architecture-agent)"
_ALLOWED_SCHEMES = frozenset({"https", "http"})
_SCRIPT = re.compile(r"<(script|style|noscript)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\n{3,}")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")


class HttpPageFetcher:
    """Fetch a page and return plain text.

    Args:
        timeout: Per-request timeout in seconds.
        max_redirects: Redirect hops allowed before giving up.
    """

    def __init__(self, *, timeout: float = 20.0, max_redirects: int = 3) -> None:
        """Configure the timeout and redirect budget."""
        self._timeout = timeout
        self._max_redirects = max_redirects

    def fetch(self, url: str, *, max_chars: int) -> str:
        """Return the page's text content.

        Raises:
            ValueError: if the scheme is not http(s).
            RuntimeError: if the request fails or exceeds the redirect budget.
        """
        import httpx

        parsed = urlparse(url)
        if parsed.scheme not in _ALLOWED_SCHEMES:
            msg = f"refusing to fetch {url!r}: scheme {parsed.scheme!r} is not allowed"
            raise ValueError(msg)

        try:
            with httpx.Client(
                timeout=self._timeout,
                follow_redirects=True,
                max_redirects=self._max_redirects,
                headers={"User-Agent": _USER_AGENT},
            ) as client:
                response = client.get(url)
                response.raise_for_status()
                final = str(response.url)
        except httpx.HTTPError as error:
            msg = f"could not fetch {url!r}: {type(error).__name__}"
            raise RuntimeError(msg) from error

        if (urlparse(final).hostname or "").lower() != (parsed.hostname or "").lower():
            msg = f"refusing {url!r}: redirected off-host to {final!r}"
            raise RuntimeError(msg)

        return to_text(response.text)[:max_chars]


def to_text(html: str) -> str:
    """Convert HTML to inert text.

    Script, style and noscript content is removed rather than escaped, because an
    escaped ``<script>`` is still a ``<script>`` to a model scanning for
    instructions. Image and link syntax is rewritten so a Markdown image cannot
    become a tracking pixel in a rendered document.
    """
    without_code = _SCRIPT.sub(" ", html)
    unlinked = _IMAGE.sub("[image]", without_code)
    unlinked = _LINK.sub(r"\1", unlinked)
    text = unescape(_TAG.sub(" ", unlinked))
    return _WHITESPACE.sub("\n\n", text).strip()


def absolutise(base: str, links: tuple[str, ...]) -> tuple[str, ...]:
    """Resolve relative links against ``base``."""
    return tuple(urljoin(base, link) for link in links)


__all__ = ["HttpPageFetcher", "absolutise", "to_text"]
