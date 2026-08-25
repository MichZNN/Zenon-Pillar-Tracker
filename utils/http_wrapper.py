from __future__ import annotations

from typing import Any, Mapping

import requests


class HttpError(RuntimeError):
    """Raised when an HTTP request cannot be completed."""


class HttpWrapper:
    DEFAULT_TIMEOUT = 15

    @staticmethod
    def get(
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> requests.Response:
        try:
            return requests.get(
                url,
                params=params,
                timeout=timeout or HttpWrapper.DEFAULT_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise HttpError(f"GET {url} failed: {exc}") from exc

    @staticmethod
    def post(
        url: str,
        data: Any,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> requests.Response:
        try:
            return requests.post(
                url,
                headers=dict(headers or {"Content-type": "application/json"}),
                json=data,
                timeout=timeout or HttpWrapper.DEFAULT_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise HttpError(f"POST {url} failed: {exc}") from exc

    @staticmethod
    def retry_after_seconds(
        response: requests.Response,
        *,
        fallback: float = 1.0,
        maximum: float = 60.0,
    ) -> float:
        """Return a safe delay from a rate-limit response.

        Telegram puts its flood-control delay in
        ``parameters.retry_after``. HTTP services may instead use the
        standard ``Retry-After`` header. The fallback is used when neither is
        available.
        """
        candidate: Any = response.headers.get("Retry-After")
        if candidate is None:
            try:
                payload = response.json()
            except (AttributeError, ValueError):
                payload = {}
            parameters = payload.get("parameters") if isinstance(payload, dict) else None
            if isinstance(parameters, dict):
                candidate = parameters.get("retry_after")

        try:
            delay = float(candidate)
        except (TypeError, ValueError):
            delay = float(fallback)

        delay = max(0.0, delay)
        if maximum > 0:
            delay = min(delay, float(maximum))
        return delay
