"""Allow ``python -m speed_profile_builder`` as well as the console script."""

from .cli import run

if __name__ == "__main__":  # pragma: no cover - process entry point
    run()
