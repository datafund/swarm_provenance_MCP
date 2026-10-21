"""Keep RPC URL paths and queries out of log lines.

RPC providers put API keys in the URL path (``/v2/<key>``) or query
(``?apikey=<key>``). Log lines show the host only. Exception text from web3,
requests and urllib3 often quotes the full URL, so it goes through
``redact_urls`` before it is logged.
"""

import re
from typing import Any
from urllib.parse import urlparse

# scheme://authority, then an optional path, query or fragment.
# - The scheme is the up to 32 letters, digits, "+", "." or "-" right before
#   "://", with no word boundary required, so "foo_https://" and
#   "500https://" match too. A longer word gives its last 32 characters as
#   the scheme. The cap keeps the scan from each start position short.
# - The separator is "://" or its JSON-escaped form ":\/\/".
# - The authority is an optional "user:pass@", then a bracketed IPv6 host with
#   an optional port, or a name or IPv4 host (with any port) that stops at
#   "/", "\", "?", "#", whitespace, quotes and brackets.
# - The rest (path, query, fragment) stops at whitespace, quotes and angle
#   brackets, and leaves out trailing punctuation and closing parentheses.
# Every repetition is a single character class, so matching stays linear.
_URL = re.compile(
    r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]{0,31})(?P<sep>://|:\\/\\/)"
    r"(?:[^/\\?#\s'\"<>()\[\]@]*@)?"
    r"(?P<host>\[[0-9A-Za-z:.%_~-]+\](?::[0-9]*)?|[^/\\?#\s'\"<>()\[\]]+)"
    r"(?P<rest>[/\\?#](?:[^\s'\"<>]*[^\s'\"<>.,;:()\[\]])?)?"
)

# urllib3 errors name the path on its own: "... with url: /v2/<key> (Caused by ...".
_URL_PATH = re.compile(r"(\bwith url: ?)\S+")


def url_host(url: Any, default: str = "<rpc>") -> str:
    """The hostname of a URL, or ``default`` if it has none.

    A bracketed IPv6 host comes back without the brackets (``::1``).
    """
    try:
        return urlparse(str(url)).hostname or default
    except Exception:
        return default


def redact_urls(value: Any) -> str:
    """``str(value)`` with every URL cut down to scheme, host and port.

    Credentials in the authority (``user:pass@``) are removed as well.
    """
    text = str(value)
    text = _URL.sub(r"\g<scheme>\g<sep>\g<host>", text)
    return _URL_PATH.sub(r"\1<path>", text)
