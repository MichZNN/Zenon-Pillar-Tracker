"""Compatibility CLI entrypoint for the collector."""

from controllers.collector_controller import Collector, main

__all__ = ["Collector", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
