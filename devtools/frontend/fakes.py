"""Stand-ins for the browser E2E harness: OpenRouter, OAuth and a CORS server.

:class:`FakeOpenRouter` stands in for OpenRouter's chat-completions
endpoint, scripted for the E2E's one coach turn: a confirm-gated tool call,
then text. It also answers "Connect OpenRouter"'s code-for-key exchange.
:class:`FakeIntervalsOAuth` puts intervals.icu's token endpoint in front of
a ``FakeIntervals`` account, for "Connect intervals.icu".
:class:`CorsJsonServer` fronts a fake's ``handle`` — this one's or
:class:`~endury.intervals.fake.FakeIntervals`' — on a localhost port for a
browser page served from another origin. ``FakeIntervals`` lives in
``endury/intervals/fake.py`` (the demo athlete runs on it in the worker)
and is re-exported here.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from endury.intervals.fake import (
    _API_PREFIX,
    MAX_DESCRIPTION_CHARS,
    FakeIntervals,
    FakeResponse,
    _error,
)


@dataclass(frozen=True)
class Script:
    """One scripted turn: a trigger, the tool call it gets, the reply after."""

    trigger: str
    tool: str
    arguments: Mapping[str, Any]
    reply: str


_CARD_REPLY = "Here is that change as a card below. Tap Apply when you are ready."


class FakeOpenRouter:
    """OpenRouter's chat-completions endpoint, scripted for one proposed write.

    A request whose latest athlete message contains ``trigger`` is answered
    with one call of ``tool`` (a confirm-gated tool, so the coach turns it
    into an Apply card); the round that carries that tool's result back is
    answered with text. ``more`` adds scripts of the same shape (an analysis
    that draws a chart). Any other turn — the shell's own canned prompts —
    gets a short text reply, so the coach never proposes anything unasked.
    ``requests`` logs every request body served. ``POST /auth/keys`` (the
    Connect exchange) answers every code with ``key`` and logs its body in
    ``key_requests``; checking the PKCE verifier is the caller's job.
    """

    def __init__(
        self,
        *,
        trigger: str,
        tool: str,
        arguments: Mapping[str, Any],
        key: str = "fake-openrouter-key",
        more: Sequence[Script] = (),
    ) -> None:
        """Initialize the script.

        Args:
            trigger: Text that marks the athlete message to answer with the
                tool call.
            tool: Name of the tool to call.
            arguments: The tool call's arguments.
            key: The API key a Connect code exchange hands out.
            more: Further scripted turns.
        """
        self.trigger = trigger
        self.tool = tool
        self.arguments = dict(arguments)
        self.scripts = [Script(trigger, tool, self.arguments, _CARD_REPLY), *more]
        self.key = key
        self.requests: list[dict[str, Any]] = []
        self.key_requests: list[dict[str, Any]] = []

    def handle(
        self, method: str, path: str, params: Mapping[str, str], body: Any
    ) -> FakeResponse:
        """Answer one API request (same signature as ``FakeIntervals.handle``).

        Args:
            method: HTTP method.
            path: Request path, with or without the ``/api/v1`` prefix.
            params: Query parameters (unused).
            body: Parsed JSON body.

        Returns:
            A chat-completions body, a Connect key, or the API's error shape.
        """
        route = (method.upper(), path.removeprefix(_API_PREFIX))
        if route == ("POST", "/auth/keys") and isinstance(body, dict):
            self.key_requests.append(body)
            return FakeResponse(200, {"key": self.key})
        if route != ("POST", "/chat/completions") or not isinstance(body, dict):
            return _error(404, f"no route for {method} {path}")
        self.requests.append(body)
        last: dict[str, Any] = next(
            (
                m
                for m in reversed(body.get("messages") or [])
                if m.get("role") in {"user", "tool"}
            ),
            {},
        )
        script = next(
            (s for s in self.scripts if s.trigger in str(last.get("content"))), None
        )
        if last.get("role") == "user" and script is not None:
            call = {
                "id": f"call_{len(self.requests)}",
                "type": "function",
                "function": {
                    "name": script.tool,
                    "arguments": json.dumps(dict(script.arguments)),
                },
            }
            message = {"role": "assistant", "content": None, "tool_calls": [call]}
            return self._completion(message, "tool_calls")
        if last.get("role") == "tool":
            called = last.get("name") or self._last_tool(body.get("messages") or [])
            text = next(
                (s.reply for s in self.scripts if s.tool == called), _CARD_REPLY
            )
        else:
            text = "Looks good. Start easy and keep the first week steady."
        return self._completion({"role": "assistant", "content": text}, "stop")

    @staticmethod
    def _last_tool(messages: list[dict[str, Any]]) -> str:
        """The name of the last tool the conversation called."""
        for message in reversed(messages):
            for call in message.get("tool_calls") or []:
                return str(call["function"]["name"])
        return ""

    def _completion(self, message: dict[str, Any], finish_reason: str) -> FakeResponse:
        """Wrap one assistant message in a chat-completions response body."""
        return FakeResponse(
            200,
            {
                "id": f"gen-{len(self.requests)}",
                "object": "chat.completion",
                "created": 0,
                "model": "fake/coach",
                "choices": [
                    {"index": 0, "finish_reason": finish_reason, "message": message}
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )


#: The scopes Endury asks intervals.icu for, as its token reply lists them.
ENDURY_SCOPES = "ACTIVITY:WRITE,WELLNESS:READ,CALENDAR:WRITE,SETTINGS:WRITE"


class FakeIntervalsOAuth:
    """intervals.icu's OAuth token endpoint in front of a ``FakeIntervals`` account.

    ``POST /api/oauth/token`` answers a public client's form post
    (``client_id``, ``code``, ``code_verifier``) with ``token``, ``scope``
    and the athlete, and logs the form in ``token_requests``; checking the
    PKCE verifier is the caller's job. ``DELETE /api/v1/disconnect-app``
    counts in ``disconnects``. Every other request goes to the account.
    """

    def __init__(
        self,
        account: FakeIntervals,
        token: str = "fake-intervals-token",
        scope: str = ENDURY_SCOPES,
        athlete_name: str = "Connected Athlete",
    ) -> None:
        """Front ``account`` with the token endpoint.

        Args:
            account: The account every other request is answered from.
            token: The access token a code exchange hands out.
            scope: The granted scopes the exchange reports.
            athlete_name: The athlete name the exchange reports.
        """
        self.account = account
        self.token = token
        self.scope = scope
        self.athlete_name = athlete_name
        self.token_requests: list[dict[str, Any]] = []
        self.disconnects = 0

    def handle(
        self, method: str, path: str, params: Mapping[str, str], body: Any
    ) -> FakeResponse:
        """Answer one request (same signature as ``FakeIntervals.handle``).

        Args:
            method: HTTP method.
            path: Request path.
            params: Query parameters.
            body: Parsed form or JSON body.

        Returns:
            The token reply, the disconnect's empty answer, or the account's.
        """
        route = (method.upper(), path)
        if route == ("POST", "/api/oauth/token") and isinstance(body, dict):
            self.token_requests.append(body)
            reply = {
                "token_type": "Bearer",
                "access_token": self.token,
                "scope": self.scope,
                "athlete": {"id": "i1", "name": self.athlete_name},
            }
            return FakeResponse(200, reply)
        if route == ("DELETE", f"{_API_PREFIX}/disconnect-app"):
            self.disconnects += 1
            return FakeResponse(200, {})
        return self.account.handle(method, path, params, body)


#: A fake's transport-free core: ``(method, path, params, body)`` to an answer.
Responder = Callable[[str, str, Mapping[str, str], Any], FakeResponse]


class BurstHTTPServer(ThreadingHTTPServer):
    """A localhost server that survives a page's burst of parallel requests.

    ``http.server`` listens with a backlog of 5. A page that opens dozens of
    connections at once overflows it, and macOS resets the ones that do not
    fit, so a module or a stylesheet fails to load.
    """

    request_queue_size = 128


class CorsJsonServer:
    """Serve a fake's ``handle`` over HTTP on localhost, open to any origin.

    The browser page runs on another port, so every answer carries CORS
    headers echoing the request's ``Origin`` and preflight
    ``Access-Control-Request-Headers``, and ``OPTIONS`` answers 204. A body
    is read as JSON, or as a form when its content type says so.
    ``credentials`` collects every ``Authorization`` header served. Calls
    into the fake are serialized: the fakes are not thread-safe.
    """

    def __init__(self, respond: Responder, port: int = 0) -> None:
        """Bind the server (``port`` 0 picks a free one); ``start`` serves it.

        Args:
            respond: The fake's ``handle``.
            port: Port on 127.0.0.1 to listen on.
        """
        lock = threading.Lock()
        credentials = self.credentials = set[str]()

        class Handler(BaseHTTPRequestHandler):
            """One request: parse, call the fake, answer JSON with CORS headers."""

            def do_OPTIONS(self) -> None:
                """Answer a CORS preflight."""
                self._send(204, None)

            def do_GET(self) -> None:
                """Answer a GET (and, by alias, every other method)."""
                url = urlsplit(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                form = (self.headers.get("Content-Type") or "").startswith(
                    "application/x-www-form-urlencoded"
                )
                body: Any = dict(parse_qsl(raw.decode())) if form else None
                if raw and not form:
                    body = json.loads(raw)
                if self.headers.get("Authorization"):
                    credentials.add(self.headers["Authorization"])
                with lock:
                    answer = respond(
                        self.command, url.path, dict(parse_qsl(url.query)), body
                    )
                self._send(answer.status, answer.body)

            do_POST = do_PUT = do_DELETE = do_GET

            def _send(self, status: int, body: Any) -> None:
                """Write the status, CORS headers and JSON body."""
                data = b"" if body is None else json.dumps(body).encode()
                self.send_response(status)
                self.send_header(
                    "Access-Control-Allow-Origin", self.headers.get("Origin") or "*"
                )
                self.send_header("Access-Control-Allow-Credentials", "true")
                self.send_header(
                    "Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS"
                )
                requested = self.headers.get("Access-Control-Request-Headers")
                if requested:
                    self.send_header("Access-Control-Allow-Headers", requested)
                self.send_header("Vary", "Origin")
                if body is not None:
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args: Any) -> None:
                """Stay quiet: the harness reports what matters."""

        self._server = BurstHTTPServer(("127.0.0.1", port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        """The server's base URL, e.g. ``http://127.0.0.1:53211``."""
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def start(self) -> CorsJsonServer:
        """Serve in a daemon thread; returns ``self`` for chaining."""
        self._thread.start()
        return self

    def stop(self) -> None:
        """Stop serving and release the port."""
        self._server.shutdown()
        self._server.server_close()


__all__ = [
    "ENDURY_SCOPES",
    "MAX_DESCRIPTION_CHARS",
    "BurstHTTPServer",
    "CorsJsonServer",
    "FakeIntervals",
    "FakeIntervalsOAuth",
    "FakeOpenRouter",
    "FakeResponse",
    "Script",
]
