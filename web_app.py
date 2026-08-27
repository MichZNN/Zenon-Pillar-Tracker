"""Compatibility CLI entrypoint for the dashboard server."""

from controllers.web_controller import (
    ApiRateLimiter,
    DashboardHandler,
    DashboardServer,
    main,
)

__all__ = [
    "ApiRateLimiter",
    "DashboardHandler",
    "DashboardServer",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
