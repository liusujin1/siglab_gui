"""PyInstaller entry point for the portable Communication Server."""

from python_samba.commserver.app import main


if __name__ == "__main__":
    raise SystemExit(main())
