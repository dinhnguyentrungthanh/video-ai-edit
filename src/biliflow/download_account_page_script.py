"""The one outside script a source's film page needs (M7, exception A; the user chose on 2026-10-10 to keep it;
docs/SOURCE_ACCOUNTS_PLAN.md §9.20 and the Network rule of AGENTS.md).

A source may configure ``page_script`` (download_account_config): the exact https URL of one script its film
page loads and without which the page's own gate opens a modal over everything. Only a hidden run of that
source that may ask for a ticket gets a ``PageScript`` (download_account_sources passes ``page_script=True`` to
download_account_runs for it; a list-only run does not); the sign-in window never does, so there every outside
script stays refused as before. The session browser's route handler hands every request to the script's host
here, and nothing else:

- answered only when it is exactly that script: resource type ``script``, GET without a body, https, the
  host and path of ``page_script``, the default port, no user name, and no query or only ``_=<digits>``
  (a timestamp the page adds: 10 to 16 digits). Anything else to that host is refused, a redirect too
  (even to the same URL): it is never followed;
- the request goes through ``SessionHttp`` for that one host: public addresses only, DNS pinned at the
  connection, TLS checked for the host name, a deadline, a byte limit (MAX_SCRIPT_BYTES) and the run's
  cancel. It is a GET of the configured URL itself: the page's timestamp query is accepted but never sent on,
  so nothing the page (or a script in it) writes into the URL reaches that host. It carries no cookie, no
  Authorization, no CSRF header, no Referer and no Origin: only Accept and the browser's User-Agent;
- only a 200 with a JavaScript content type is handed to the page, with no header of the answer but its
  content type (no Set-Cookie: nothing of that host goes into the browser's jar or the vault);
- at most MAX_SCRIPT_REQUESTS answers per run; past them it is refused.

The script then runs in the film page with that page's rights (it can read the page and its cookies that are
not HttpOnly); leaving cookies out of its own request does not take that away, and this is no sandbox. Every
request the script makes in turn goes through the route handler like any other: a host outside the source is
refused (HOST_NOT_ALLOWED). The script's host is never a host of the source: it is not in ``all_hosts``, so it
is never matched as the source's link, never saved in the vault and never fetched by ``LoginView.fetch``.
Refusals keep their code and host only (``SessionBrowser.refusals``); nothing here logs.
"""
from __future__ import annotations

import re
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from biliflow.download_account_http import SessionHttp, SessionNetwork, SessionReply
from biliflow.download_http import Interruptible

MAX_SCRIPT_REQUESTS = 4  # answers per run (the page loads it once; a reload or two at most)
MAX_SCRIPT_BYTES = 2 * 1024 * 1024
SCRIPT_SECONDS = 20.0
TIMESTAMP_QUERY = re.compile(r"_=[0-9]{10,16}")
JAVASCRIPT_TYPES = frozenset({"text/javascript", "application/javascript", "application/x-javascript",
                              "text/ecmascript", "application/ecmascript"})


class PageScript:
    """The configured ``page_script`` of one source for one hidden run (module docstring)."""

    def __init__(self, url: str, network: SessionNetwork | None = None, *, request_seconds: float = SCRIPT_SECONDS,
                 max_body: int = MAX_SCRIPT_BYTES):
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname or parts.query or parts.fragment or parts.username:
            raise ValueError("A page script is an exact https URL without query, fragment or user name")
        self.host = parts.hostname.lower()
        self.path = parts.path
        self.url = urlunsplit(("https", self.host, self.path, "", ""))  # what is fetched, whatever the query
        self.http = SessionHttp({self.host}, network, request_seconds=request_seconds, max_body=max_body)
        self.answered = 0

    def owns(self, url: str) -> bool:
        """``url`` is on the script's host: the route handler hands it here, whatever it is."""
        try:
            return (urlsplit(url).hostname or "").lower() == self.host
        except ValueError:
            return False

    def refusal(self, request: Any) -> str | None:
        """The code that refuses ``request`` (a Playwright request on the script's host), or None when it is
        exactly the configured script."""
        if request.resource_type != "script":
            return "PAGE_SCRIPT_TYPE"
        if request.method != "GET" or request.post_data_buffer:
            return "PAGE_SCRIPT_METHOD"
        try:
            parts = urlsplit(request.url)
            port = parts.port
        except ValueError:
            return "PAGE_SCRIPT_URL"
        if (parts.scheme != "https" or (parts.hostname or "").lower() != self.host or port not in (None, 443)
                or parts.username or parts.password or parts.path != self.path
                or (parts.query and not TIMESTAMP_QUERY.fullmatch(parts.query))):
            return "PAGE_SCRIPT_URL"
        if self.answered >= MAX_SCRIPT_REQUESTS:
            return "PAGE_SCRIPT_LIMIT"
        return None

    def fetch(self, headers: Mapping[str, str], control: Interruptible) -> SessionReply:
        """One checked GET of the configured script URL (never the page's query), with Accept and the browser's
        User-Agent only (module docstring)."""
        self.answered += 1
        outgoing = {"accept": "*/*"}
        agent = next((value for name, value in headers.items() if name.lower() == "user-agent"), None)
        if agent:
            outgoing["user-agent"] = agent
        return self.http.send(self.url, control, method="GET", headers=outgoing)

    @staticmethod
    def javascript(reply: SessionReply) -> bool:
        return reply.header("Content-Type").split(";")[0].strip().lower() in JAVASCRIPT_TYPES
