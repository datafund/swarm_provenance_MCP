"""Keep RPC URL paths and queries out of log lines and tool results.

RPC providers put API keys in the URL path (``/v2/<key>``) or query
(``?apikey=<key>``). Log lines and tool results show the host only. Exception
text from web3, requests and urllib3 often quotes the full URL, so it goes
through ``redact_urls`` before it is logged or returned to the agent.
"""

import re
from typing import Any, Iterable, Optional
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

# ... and the host earlier in the same message: "HTTPSConnectionPool(host='h', ...".
_POOL_HOST = re.compile(r"\bhost='([^']*)'")


def url_host(url: Any, default: str = "<rpc>") -> str:
    """The hostname of a URL, or ``default`` if it has none.

    A bracketed IPv6 host comes back without the brackets (``::1``).
    """
    try:
        return urlparse(str(url)).hostname or default
    except Exception:
        return default


def redact_urls(value: Any, hosts: Optional[Iterable[str]] = None) -> str:
    """``str(value)`` with every URL cut down to scheme, host and port.

    Credentials in the authority (``user:pass@``) are removed as well.

    With ``hosts``, only URLs on those hosts (compared without case) are cut,
    and urllib3's ``with url: <path>`` only when the text names one of them
    as ``host='...'``. Other URLs, such as faucet or explorer links, are kept.
    """
    text = str(value)
    if hosts is None:
        text = _URL.sub(r"\g<scheme>\g<sep>\g<host>", text)
        return _URL_PATH.sub(r"\1<path>", text)

    wanted = {h.lower().strip("[]") for h in hosts if h}
    if not wanted:
        return text

    def cut(match: "re.Match[str]") -> str:
        if _bare_host(match.group("host")) not in wanted:
            return match.group(0)
        return match.group("scheme") + match.group("sep") + match.group("host")

    text = _URL.sub(cut, text)
    if any(h.lower() in wanted for h in _POOL_HOST.findall(text)):
        text = _URL_PATH.sub(r"\1<path>", text)
    return text


def _bare_host(authority_host: str) -> str:
    """The host of ``host[:port]`` or ``[v6][:port]``, lower case, no brackets."""
    if authority_host.startswith("["):
        return authority_host[1 : authority_host.find("]")].lower()
    return authority_host.split(":", 1)[0].lower()
