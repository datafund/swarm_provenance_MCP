"""Keep RPC URL paths and queries out of log lines.

RPC providers put API keys in the URL path (``/v2/<key>``) or query
(``?apikey=<key>``). Log lines show the host only. Exception text from web3,
requests and urllib3 often quotes the full URL, so it goes through
``redact_urls`` before it is logged.
"""

import re
from typing import Any
from urllib.parse import urlparse

# scheme://authority, then an optional path, query or fragment. The authority
# stops at "/", "?", "#", whitespace, quotes and brackets; so does the rest,
# which also leaves out trailing sentence punctuation.
_URL = re.compile(
    r"(?P<base>\b[a-zA-Z][a-zA-Z0-9+.-]*://[^/?#\s'\"<>()\[\]]+)"
    r"(?P<rest>[/?#](?:[^\s'\"<>()\[\]]*[^\s'\"<>()\[\].,;:])?)?"
)

# urllib3 errors name the path on its own: "... with url: /v2/<key> (Caused by ...".
_URL_PATH = re.compile(r"(\bwith url: )\S+")


def url_host(url: Any, default: str = "<rpc>") -> str:
    """The hostname of a URL, or ``default`` if it has none."""
    try:
        return urlparse(str(url)).hostname or default
    except Exception:
        return default


def redact_urls(value: Any) -> str:
    """``str(value)`` with every URL cut down to scheme, host and port.

    Credentials in the authority (``user:pass@``) are removed as well.
    """
    text = str(value)

    def _base(match: "re.Match[str]") -> str:
        base = match.group("base")
        scheme, _, authority = base.partition("://")
        return f"{scheme}://{authority.rpartition('@')[2]}"

    text = _URL.sub(_base, text)
    return _URL_PATH.sub(r"\1<path>", text)
