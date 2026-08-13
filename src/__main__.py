"""`python -m src` starts the web ground station UI.

Convenience entry point: the project package is ``src`` (not ``airsim_mcp``),
so ``python -m src`` behaves like ``python -m src.ui.server`` with default
arguments (host 127.0.0.1, port 8765, backend px4_mavlink).
"""

from src.ui.server import main

if __name__ == "__main__":
    main()
