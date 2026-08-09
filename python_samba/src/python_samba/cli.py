"""Command-line entry for python_samba (vendor-free RCI host)."""

from __future__ import annotations

import argparse
import sys

from python_samba import __version__
from python_samba.protocol.codes import status_name
from python_samba.protocol.commands import RciCommandError
from python_samba.protocol.frame import ProtocolError
from python_samba.services.session import (
    ControllerSession,
    open_comm_server,
    open_mock,
    open_serial,
)
from python_samba.transport.serial_port import TransportError


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python-samba",
        description="Vendor-free IDE TC-MFD / OPTICON host (pure RCI serial).",
    )
    parser.add_argument("--version", action="version", version=f"python-samba {__version__}")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--backend",
        choices=["server", "serial", "mock"],
        default="server",
        help="Transport backend (default: shared communication server).",
    )
    common.add_argument("--port", default=None, help="Serial port, e.g. COM3 or /dev/ttyUSB0")
    common.add_argument(
        "--baud",
        type=int,
        default=57600,
        help="Baud rate (firmware default after reset: 57600)",
    )
    common.add_argument("--timeout", type=float, default=2.0, help="Response timeout seconds")
    common.add_argument("--server", default="127.0.0.1:47619", help="Communication Server HOST:PORT")
    common.add_argument("--token-file", default=None, help="Access-token file for remote servers")
    common.add_argument("--no-auto-start", action="store_true", help="Do not auto-start a local server")
    common.add_argument(
        "--write",
        action="store_true",
        help="Allow write commands (default is readonly)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_connect = sub.add_parser("connect", parents=[common], help="Open link and print firmware version")
    p_connect.set_defaults(func=cmd_connect)

    p_status = sub.add_parser("status", parents=[common], help="Read loop status and sample rate")
    p_status.set_defaults(func=cmd_status)

    p_filter = sub.add_parser("filter", parents=[common], help="Read one velocity filter stage")
    p_filter.add_argument("--axis", type=int, default=0, help="Velocity axis 0..5")
    p_filter.add_argument("--stage", type=int, default=0, help="Filter stage 0..6")
    p_filter.set_defaults(func=cmd_filter)

    p_matrix = sub.add_parser("matrix", parents=[common], help="Read velocity sensor/motor matrix row")
    p_matrix.add_argument("--axis", type=int, default=0)
    p_matrix.add_argument(
        "--which",
        choices=["sensor", "motor", "both"],
        default="both",
    )
    p_matrix.set_defaults(func=cmd_matrix)

    p_geo = sub.add_parser("geophone", parents=[common], help="Read geophone input digits")
    p_geo.set_defaults(func=cmd_geophone)

    p_pos = sub.add_parser("position", parents=[common], help="Read position/proximity basics")
    p_pos.add_argument("--axis", type=int, default=0, help="Position axis 0..5")
    p_pos.add_argument("--stage", type=int, default=0, help="Proximity filter stage 0..3")
    p_pos.set_defaults(func=cmd_position)

    p_gui = sub.add_parser("gui", help="Launch PySide6 tuning UI")
    p_gui.set_defaults(func=cmd_gui)

    p_ff = sub.add_parser("ff", parents=[common], help="Read feedforward status / one filter stage")
    p_ff.add_argument("--source", type=int, default=0, help="FF source index")
    p_ff.add_argument("--stage", type=int, default=0, help="FF filter stage")
    p_ff.set_defaults(func=cmd_ff)

    p_diag = sub.add_parser("diag", parents=[common], help="Read diagnostic noise settings")
    p_diag.set_defaults(func=cmd_diag)

    p_nvram = sub.add_parser("nvram", parents=[common], help="NVRAM save/restore/clear (needs --write)")
    p_nvram.add_argument("action", choices=["save", "restore", "clear"])
    p_nvram.set_defaults(func=cmd_nvram)

    p_raw = sub.add_parser("raw", parents=[common], help="Send a raw 5-letter mnemonic")
    p_raw.add_argument("mnemonic", help="5-char command, e.g. BGVIS")
    p_raw.add_argument("params", nargs="*", help="Optional parameters")
    p_raw.set_defaults(func=cmd_raw)

    return parser


def _open_session(args: argparse.Namespace) -> ControllerSession:
    readonly = not args.write
    if args.backend == "mock":
        return open_mock(readonly=readonly)
    if not args.port:
        raise SystemExit(f"{args.backend} backend requires --port (or use --backend mock)")
    if args.backend == "server":
        return open_comm_server(
            args.port,
            args.baud,
            server=args.server,
            token_file=args.token_file,
            auto_start=not args.no_auto_start,
            client_name="python-samba-cli",
            readonly=readonly,
            timeout=args.timeout,
        )
    return open_serial(args.port, args.baud, readonly=readonly, timeout=args.timeout)


def cmd_connect(args: argparse.Namespace) -> int:
    session = _open_session(args)
    try:
        version = session.open()
        print(f"backend : {session.info.backend}")
        if session.info.server_endpoint:
            print(f"server  : {session.info.server_endpoint}")
        if session.info.port:
            print(f"port    : {session.info.port} @ {session.info.baudrate}")
        print(f"firmware: {version}")
        print("connect : ok")
        return 0
    finally:
        session.close()


def cmd_status(args: argparse.Namespace) -> int:
    session = _open_session(args)
    try:
        version = session.open()
        loop = session.get_loop_status()
        try:
            fs = session.get_sample_frequency()
        except (RciCommandError, ProtocolError, TransportError) as exc:
            fs = f"n/a ({exc})"
        print(f"firmware : {version}")
        print(f"loop     : {loop}")
        print(f"sample   : {fs}")
        return 0
    finally:
        session.close()


def cmd_filter(args: argparse.Namespace) -> int:
    session = _open_session(args)
    try:
        session.open()
        stage = session.get_velocity_filter(args.axis, args.stage)
        params = ", ".join(f"{p:g}" for p in stage.params)
        print(
            f"axis={stage.axis} stage={stage.stage} "
            f"type={stage.filter_type} ({stage.type_name}) params=[{params}]"
        )
        return 0
    finally:
        session.close()


def cmd_matrix(args: argparse.Namespace) -> int:
    session = _open_session(args)
    try:
        session.open()
        if args.which in ("sensor", "both"):
            row = session.get_velocity_sensor_matrix(args.axis)
            print(f"sensor axis {args.axis}: {row}")
        if args.which in ("motor", "both"):
            row = session.get_velocity_motor_matrix(args.axis)
            print(f"motor  axis {args.axis}: {row}")
        return 0
    finally:
        session.close()


def cmd_geophone(args: argparse.Namespace) -> int:
    session = _open_session(args)
    try:
        session.open()
        vals = session.get_geophone_inputs()
        print("geophone:", vals)
        return 0
    finally:
        session.close()


def cmd_position(args: argparse.Namespace) -> int:
    session = _open_session(args)
    try:
        session.open()
        stage = session.get_proximity_filter(args.axis, args.stage)
        params = ", ".join(f"{p:g}" for p in stage.params)
        print(
            f"proximity filter axis={stage.axis} stage={stage.stage} "
            f"type={stage.filter_type} ({stage.type_name}) params=[{params}]"
        )
        print("proximity offsets:", session.get_proximity_offsets())
        print("pos sensor matrix:", session.get_position_sensor_matrix(args.axis))
        print("pos motor  matrix:", session.get_position_motor_matrix(args.axis))
        return 0
    finally:
        session.close()


def cmd_gui(args: argparse.Namespace) -> int:
    from python_samba.app import main as gui_main

    return gui_main()


def cmd_ff(args: argparse.Namespace) -> int:
    session = _open_session(args)
    try:
        session.open()
        print("ff status:", session.get_ff_status())
        print("ff inputs:", session.get_ff_inputs())
        stage = session.get_ff_filter(args.source, args.stage)
        params = ", ".join(f"{p:g}" for p in stage.params)
        print(
            f"ff filter source={stage.axis} stage={stage.stage} "
            f"type={stage.filter_type} ({stage.type_name}) params=[{params}]"
        )
        return 0
    finally:
        session.close()


def cmd_diag(args: argparse.Namespace) -> int:
    session = _open_session(args)
    try:
        session.open()
        print("noise type :", session.get_noise_type())
        print("noise gain :", session.get_noise_gain())
        print("inject pt  :", session.get_noise_inject_point())
        print("switch     :", session.get_switch_status())
        print("out limit% :", session.get_output_limit())
        return 0
    finally:
        session.close()


def cmd_nvram(args: argparse.Namespace) -> int:
    if not args.write:
        print("error: nvram requires --write", file=sys.stderr)
        return 1
    session = _open_session(args)
    try:
        session.open()
        if args.action == "save":
            session.nvram_save()
        elif args.action == "restore":
            session.nvram_restore()
        else:
            session.nvram_clear()
        print(f"nvram {args.action}: ok")
        return 0
    finally:
        session.close()


def cmd_raw(args: argparse.Namespace) -> int:
    session = _open_session(args)
    try:
        session.open()
        # Coerce numeric-looking params
        params: list[str | int | float] = []
        for p in args.params:
            try:
                if any(c in p for c in ".eE"):
                    params.append(float(p))
                else:
                    params.append(int(p, 0))
            except ValueError:
                params.append(p)
        resp = session.raw_command(args.mnemonic.upper(), *params)
        print(f"raw      : {resp.raw}")
        print(f"ok       : {resp.ok}")
        print(f"status   : 0x{resp.status_code:02X} ({status_name(resp.status_code)})")
        print(f"mnemonic : {resp.mnemonic}")
        print(f"data     : {resp.data_text}")
        return 0 if resp.ok else 2
    finally:
        session.close()


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (TransportError, ProtocolError, RciCommandError, PermissionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
