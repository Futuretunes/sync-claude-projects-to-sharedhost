from __future__ import annotations

import sys

from sync_to_web.ui import run_app


def main() -> int:
    return run_app()


if __name__ == "__main__":
    sys.exit(main())
