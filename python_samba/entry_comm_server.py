"""PyInstaller entry point for the portable Communication Server.

Double-clicking starts the full configuration window.  A packaged SAMBA or
SIDMAT client starts the same executable with ``--listen``/``--tray`` and is
dispatched to the lightweight server CLI.
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--listen" in arguments:
        from python_samba.commserver.cli import main as cli_main

        return cli_main(arguments)
    from python_samba.commserver.app import main as app_main

    if not arguments:
        # Double-clicking opens configuration without exposing a listener or
        # requesting firewall elevation. Local clients use the CLI dispatch
        # above; remote access is an explicit operator action in the window.
        arguments = ["--no-auto-start", "--no-firewall-prompt"]
    return app_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
