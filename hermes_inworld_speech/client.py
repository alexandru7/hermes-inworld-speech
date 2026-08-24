from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from . import __version__

logger = logging.getLogger(__name__)

DEFAULT_API_ROOT = "https://api.inworld.ai"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_ATTEMPTS = 3

_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
_MAX_RETRY_SLEEP_SECONDS = 10.0

# Socket read size while consuming an NDJSON stream.  Inworld audio chunks are
# large base64 strings, so a single line routinely spans several reads.
STREAM_READ_BLOCK = 16384

# ``http://`` is tolerated only for a loopback proxy, where the credential never
# leaves the host.  Anything else must be TLS.
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def get_api_key() -> str:
    """Return the Inworld Base64 API credential without the ``Basic`` prefix.

    Two sources are consulted, in order: the active Hermes profile (``.env``)
    and the process environment.  The environment fallback matters for
    container deployments, where the credential is injected at runtime and
    never written to a profile — so an empty Hermes result must fall through
    rather than short-circuit.
    """

    value: Any = None
    try:
        from hermes_cli.config import get_env_value

        value = get_env_value("INWORLD_API_KEY")
    except Exception as exc:
        logger.debug("Hermes config lookup unavailable (%s); using process environment.", exc)

    key = str(value or "").strip()
    if not key:
        key = str(os.getenv("INWORLD_API_KEY") or "").strip()

    if key.lower().startswith("basic "):
        key = key[6:].strip()
    return key


def api_root() -> str:
    """Return the API root, rejecting overrides that would leak the credential.

    ``INWORLD_API_BASE_URL`` exists mainly for integration tests and controlled
    proxy deployments.  Normal users should leave it unset.
    """

    override = os.getenv("INWORLD_API_BASE_URL")
    if not override or not override.strip():
        return DEFAULT_API_ROOT

    root = override.strip().rstrip("/")
    parts = urlsplit(root)
    host = (parts.hostname or "").lower()

    if parts.scheme == "https" or (parts.scheme == "http" and host in _LOOPBACK_HOSTS):
        logger.debug("Using INWORLD_API_BASE_URL override: %s", root)
        return root

    raise RuntimeError(
        "INWORLD_API_BASE_URL must use https (plain http is allowed only for "
        f"loopback addresses). Refusing to send the Inworld credential to {root!r}."
    )


def _safe_error_body(exc: HTTPError, limit: int = 4096) -> str:
    try:
        data = exc.read(limit + 1)
    except Exception:
        return ""
    text = data[:limit].decode("utf-8", errors="replace")
    if len(data) > limit:
        text += "…"
    return text


def _decode_stream_line(line: bytes) -> dict[str, Any]:
    """Parse one NDJSON line, surfacing an in-band ``error`` object.

    The streaming endpoint can report a failure mid-stream in the body rather
    than through an HTTP status, so an error line must not be mistaken for an
    ordinary chunk.
    """

    try:
        decoded = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Inworld stream returned a malformed JSON line.") from exc

    if not isinstance(decoded, dict):
        raise RuntimeError("Inworld stream returned an unexpected JSON line shape.")

    error = decoded.get("error")
    if error:
        message = error.get("message") if isinstance(error, dict) else None
        raise RuntimeError(f"Inworld stream error: {message or error}")

    return decoded


def _retry_delay(attempt: int, retry_after: str | None) -> float:
    if retry_after:
        try:
            return min(float(retry_after), _MAX_RETRY_SLEEP_SECONDS)
        except ValueError:
            pass
    return min(0.5 * (2**attempt), 4.0)


@dataclass(frozen=True)
class InworldClient:
    """Small dependency-free HTTP client for the Inworld speech APIs.

    ``timeout_seconds`` bounds a single attempt.  Retries are additionally
    bounded by an overall wall-clock deadline of
    ``timeout_seconds * max_attempts``, so a request cannot stall a caller for
    an unbounded time when Inworld is slow *and* rate limiting.

    Note that retry backoff sleeps on the calling thread.  Hermes invokes
    provider methods synchronously; if you embed this client in async code, run
    it in a worker thread.
    """

    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_attempts: int = DEFAULT_MAX_ATTEMPTS

    def _build_request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
    ) -> Request:
        key = get_api_key()
        if not key:
            raise RuntimeError(
                "INWORLD_API_KEY is not configured. Set it in the Hermes profile "
                ".env file or pass it to the Hermes process/container environment."
            )

        body = None if payload is None else json.dumps(payload).encode("utf-8")
        return Request(
            api_root() + path,
            data=body,
            headers={
                "Authorization": f"Basic {key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": f"hermes-inworld-speech/{__version__}",
            },
            method=method.upper(),
        )

    def request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request = self._build_request(method, path, payload)

        attempts = max(1, self.max_attempts)
        deadline = time.monotonic() + (self.timeout_seconds * attempts)
        last_error: Exception | None = None

        for attempt in range(attempts):
            remaining = deadline - time.monotonic()
            if attempt and remaining <= 0:
                logger.warning(
                    "Inworld %s %s: retry budget exhausted after %d attempt(s).",
                    method.upper(),
                    path,
                    attempt,
                )
                break

            timeout = self.timeout_seconds if not attempt else min(self.timeout_seconds, remaining)
            try:
                with urlopen(request, timeout=timeout) as response:
                    raw = response.read()
                    if not raw:
                        return {}
                    try:
                        decoded = json.loads(raw.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise RuntimeError("Inworld returned a non-JSON response.") from exc
                    if not isinstance(decoded, dict):
                        raise RuntimeError("Inworld returned an unexpected JSON response shape.")
                    return decoded
            except HTTPError as exc:
                detail = _safe_error_body(exc)
                last_error = RuntimeError(f"Inworld HTTP {exc.code}: {detail or exc.reason}")
                if exc.code not in _RETRYABLE_STATUS or attempt + 1 >= attempts:
                    raise last_error from exc
                delay = _retry_delay(attempt, exc.headers.get("Retry-After"))
                if delay >= deadline - time.monotonic():
                    raise last_error from exc
                logger.warning(
                    "Inworld %s %s returned HTTP %s; retrying in %.1fs (attempt %d/%d).",
                    method.upper(),
                    path,
                    exc.code,
                    delay,
                    attempt + 1,
                    attempts,
                )
                time.sleep(delay)
            except URLError as exc:
                last_error = RuntimeError(f"Inworld connection error: {exc.reason}")
                if attempt + 1 >= attempts:
                    raise last_error from exc
                delay = _retry_delay(attempt, None)
                if delay >= deadline - time.monotonic():
                    raise last_error from exc
                logger.warning(
                    "Inworld %s %s failed (%s); retrying in %.1fs (attempt %d/%d).",
                    method.upper(),
                    path,
                    exc.reason,
                    delay,
                    attempt + 1,
                    attempts,
                )
                time.sleep(delay)

        raise last_error or RuntimeError("Inworld request failed.")

    def open_stream(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        """Open a streaming response, retrying only until the response opens.

        Retries stop here deliberately.  Once ``request_ndjson`` has handed a
        chunk to its caller the audio is already in flight, and replaying the
        request would duplicate it.
        """

        request = self._build_request(method, path, payload)
        attempts = max(1, self.max_attempts)
        deadline = time.monotonic() + (self.timeout_seconds * attempts)
        last_error: Exception | None = None

        for attempt in range(attempts):
            remaining = deadline - time.monotonic()
            if attempt and remaining <= 0:
                break

            timeout = self.timeout_seconds if not attempt else min(self.timeout_seconds, remaining)
            try:
                return urlopen(request, timeout=timeout)
            except HTTPError as exc:
                detail = _safe_error_body(exc)
                last_error = RuntimeError(f"Inworld HTTP {exc.code}: {detail or exc.reason}")
                if exc.code not in _RETRYABLE_STATUS or attempt + 1 >= attempts:
                    raise last_error from exc
                delay = _retry_delay(attempt, exc.headers.get("Retry-After"))
                if delay >= deadline - time.monotonic():
                    raise last_error from exc
                logger.warning(
                    "Inworld stream %s returned HTTP %s; retrying in %.1fs (attempt %d/%d).",
                    path,
                    exc.code,
                    delay,
                    attempt + 1,
                    attempts,
                )
                time.sleep(delay)
            except URLError as exc:
                last_error = RuntimeError(f"Inworld connection error: {exc.reason}")
                if attempt + 1 >= attempts:
                    raise last_error from exc
                delay = _retry_delay(attempt, None)
                if delay >= deadline - time.monotonic():
                    raise last_error from exc
                logger.warning(
                    "Inworld stream %s failed (%s); retrying in %.1fs (attempt %d/%d).",
                    path,
                    exc.reason,
                    delay,
                    attempt + 1,
                    attempts,
                )
                time.sleep(delay)

        raise last_error or RuntimeError("Inworld stream could not be opened.")

    def request_ndjson(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield newline-delimited JSON objects as Inworld produces them.

        A single audio line routinely spans several socket reads, so partial
        lines are buffered until a newline arrives.  The stream is bounded by
        the same wall-clock budget as a unary request.
        """

        response = self.open_stream(method, path, payload)
        deadline = time.monotonic() + (self.timeout_seconds * max(1, self.max_attempts))

        with response:
            buffer = b""
            while True:
                block = response.read(STREAM_READ_BLOCK)
                if not block:
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError(
                        f"Inworld stream {path} exceeded its "
                        f"{self.timeout_seconds * max(1, self.max_attempts):.0f}s budget."
                    )
                buffer += block
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if line.strip():
                        yield _decode_stream_line(line)

            # A well-behaved stream ends with a newline, but the final line may
            # arrive without one.
            if buffer.strip():
                yield _decode_stream_line(buffer)
