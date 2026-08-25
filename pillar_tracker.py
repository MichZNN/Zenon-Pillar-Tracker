"""CLI entrypoint for the collector.

Run ``python pillar_tracker.py`` for one poll or
``python pillar_tracker.py --loop`` for a self-contained polling loop.
"""

from collector import main


if __name__ == "__main__":
    raise SystemExit(main())
