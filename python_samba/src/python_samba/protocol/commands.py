"""High-level RCI command helpers (mnemonics + param encoding)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import math

from python_samba.protocol.codes import FilterType, status_name
from python_samba.protocol.frame import (
    ProtocolError,
    RciCommand,
    RciResponse,
    build_frame,
    next_crl,
    parse_frame,
)


class RciCommandError(RuntimeError):
    def __init__(self, response: RciResponse, message: str | None = None) -> None:
        self.response = response
        if message is None:
            if not response.protocol_ok:
                message = f"protocol reject: {response.reject_reason}"
            else:
                message = (
                    f"{response.mnemonic} failed: "
                    f"0x{response.status_code:02X} ({status_name(response.status_code)}) "
                    f"data={response.data_text!r}"
                )
        super().__init__(message)


def _fmt_float(value: float) -> str:
    """Firmware expects C printf %e style floats."""
    return f"{float(value):.6e}"


def _integral_param(mnemonic: str, index: int, value: object) -> int:
    """Return one protocol integer without silently truncating a decimal."""

    text = str(value).strip()
    try:
        if text.lower().startswith(("0x", "+0x", "-0x")):
            return int(text, 0)
        number = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ProtocolError(
            f"{mnemonic} parameter {index + 1} expects an integer, got {value!r}"
        ) from exc
    if not number.is_finite() or number != number.to_integral_value():
        raise ProtocolError(
            f"{mnemonic} parameter {index + 1} expects an integer, got {value!r}"
        )
    return int(number)


def _floating_param(mnemonic: str, index: int, value: object) -> float:
    """Return one finite protocol float with a command-specific error."""

    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ProtocolError(
            f"{mnemonic} parameter {index + 1} expects a float, got {value!r}"
        ) from exc
    if not math.isfinite(number):
        raise ProtocolError(
            f"{mnemonic} parameter {index + 1} expects a finite float, got {value!r}"
        )
    return number


def _typed_params(
    mnemonic: str,
    params: tuple[object, ...],
    schema: str,
) -> tuple[int | float, ...]:
    """Validate a fixed RCI parameter list (``I`` integer, ``F`` float)."""

    if len(params) != len(schema):
        raise ProtocolError(
            f"{mnemonic} expects {len(schema)} parameters, got {len(params)}"
        )
    values: list[int | float] = []
    for index, (value, kind) in enumerate(zip(params, schema)):
        if kind == "I":
            values.append(_integral_param(mnemonic, index, value))
        elif kind == "F":
            values.append(_floating_param(mnemonic, index, value))
        else:  # pragma: no cover - schemas are constants next to each command
            raise RuntimeError(f"unsupported protocol schema kind: {kind}")
    return tuple(values)


def _parse_floats(tokens: tuple[str, ...]) -> list[float]:
    out: list[float] = []
    for t in tokens:
        try:
            out.append(float(t))
        except ValueError as exc:
            raise ProtocolError(f"expected float, got {t!r}") from exc
    return out


def _parse_ints(tokens: tuple[str, ...]) -> list[int]:
    out: list[int] = []
    for t in tokens:
        try:
            # ints may be decimal; some status words are hex without prefix in docs
            out.append(int(t, 0) if t.lower().startswith("0x") else int(t))
        except ValueError:
            try:
                out.append(int(t, 16))
            except ValueError as exc:
                raise ProtocolError(f"expected int, got {t!r}") from exc
    return out


@dataclass(frozen=True, slots=True)
class FirmwareVersion:
    major: int
    minor: int
    patch: int
    lib: int = 0
    main_board: int | None = None
    raw_info: str = ""

    @property
    def full_text(self) -> str:
        """Return the complete version information reported by the controller.

        Recent firmware appends labelled compiler and build-date fields to the
        numeric version header.  Keep that vendor text intact for the Connect
        page and configuration backups while retaining a compact ``str()`` for
        status bars and logs.
        """
        if self.raw_info:
            return self.raw_info
        values = [str(self.major), str(self.minor), str(self.patch)]
        if self.lib or self.main_board is not None:
            values.append(str(self.lib))
        if self.main_board is not None:
            values.append(str(self.main_board))
        return " ".join(values)

    def __str__(self) -> str:
        base = f"V{self.major}.{self.minor}.{self.patch}"
        if self.lib:
            return f"{base} (lib {self.lib})"
        return base


@dataclass(frozen=True, slots=True)
class LoopStatus:
    individual: int
    system: int

    def __str__(self) -> str:
        return f"individual=0x{self.individual:X} system=0x{self.system:X}"


@dataclass(frozen=True, slots=True)
class FilterStage:
    axis: int
    stage: int
    filter_type: int
    params: tuple[float, float, float, float, float]

    @property
    def type_name(self) -> str:
        try:
            return FilterType(self.filter_type).name
        except ValueError:
            return f"TYPE_{self.filter_type}"


class CommandEncoder:
    """Builds framed commands and decodes typed responses. CRL auto-increments."""

    def __init__(self, *, msg_id: str = "?", bypass_crc: bool = False, bypass_length: bool = False) -> None:
        self.msg_id = msg_id
        self.bypass_crc = bypass_crc
        self.bypass_length = bypass_length
        self._crl = 0

    def _command(self, mnemonic: str, *params: str | int | float) -> bytes:
        crl = next_crl(self._crl)
        self._crl = (self._crl + 1) & 0xFF
        str_params = tuple(
            _fmt_float(p) if isinstance(p, float) else str(p) for p in params
        )
        cmd = RciCommand(crl=crl, mnemonic=mnemonic, params=str_params, msg_id=self.msg_id)
        return build_frame(cmd, bypass_length=self.bypass_length, bypass_crc=self.bypass_crc)

    def ensure_ok(self, response: RciResponse, expected_mnemonic: str | None = None) -> RciResponse:
        if expected_mnemonic and response.protocol_ok and response.mnemonic != expected_mnemonic:
            raise RciCommandError(
                response,
                f"mnemonic mismatch: expected {expected_mnemonic}, got {response.mnemonic}",
            )
        if not response.ok:
            raise RciCommandError(response)
        return response

    # --- Basic group ---

    def bgvis(self) -> bytes:
        """Get firmware version."""
        return self._command("BGVIS")

    def decode_bgvis(self, response: RciResponse) -> FirmwareVersion:
        self.ensure_ok(response, "BGVIS")
        tokens = response.data_tokens
        if len(tokens) < 3:
            raise ProtocolError(f"BGVIS expected >=3 ints, got {response.data_tokens}")

        # Only the leading fields are numeric.  Newer controller firmware
        # returns a longer string such as:
        #   3 3 122 103 9 FWCompiler: ... FWBldDate: ...
        # Parsing every token as an integer therefore rejects a valid BGVIS
        # response as soon as it reaches ``FWCompiler:``.
        vals = _parse_ints(tokens[:3])

        def optional_int(index: int, default: int | None) -> int | None:
            if index >= len(tokens):
                return default
            try:
                return int(tokens[index], 0)
            except ValueError as exc:
                raise ProtocolError(
                    f"BGVIS field {index + 1} expected int, got {tokens[index]!r}"
                ) from exc

        lib = optional_int(3, 0)
        main_board = optional_int(4, None)
        return FirmwareVersion(
            vals[0],
            vals[1],
            vals[2],
            int(lib or 0),
            main_board,
            response.data_text,
        )

    def bgsts(self) -> bytes:
        """Get system loop status."""
        return self._command("BGSTS")

    def decode_bgsts(self, response: RciResponse) -> LoopStatus:
        self.ensure_ok(response, "BGSTS")
        # Doc: Individual Loop Status (hex), System Status Word (hex)
        tokens = response.data_tokens
        if len(tokens) < 2:
            raise ProtocolError(f"BGSTS expected 2 hex words, got {tokens}")

        def parse_word(token: str) -> int:
            try:
                return int(token, 16)
            except ValueError:
                return int(token)

        return LoopStatus(individual=parse_word(tokens[0]), system=parse_word(tokens[1]))

    def bssts(self, individual: int, system: int) -> bytes:
        return self._command("BSSTS", f"{individual:X}", f"{system:X}")

    def bgsst(self) -> bytes:
        """Get position/pneumatic individual loops and digital I/O words."""
        return self._command("BGSST")

    def decode_bgsst(self, response: RciResponse) -> tuple[int, int, int, int]:
        self.ensure_ok(response, "BGSST")
        if len(response.data_tokens) < 4:
            raise ProtocolError(
                f"BGSST expected 4 hex words, got {response.data_tokens}"
            )
        return tuple(int(token, 16) for token in response.data_tokens[:4])  # type: ignore[return-value]

    def bssst(self, position: int, pneumatic: int) -> bytes:
        """Set position and pneumatic individual-loop bit fields."""
        return self._command("BSSST", f"{position:X}", f"{pneumatic:X}")

    def ngsfr(self) -> bytes:
        """Get sample frequency."""
        return self._command("NGSFR")

    def decode_ngsfr(self, response: RciResponse) -> float:
        self.ensure_ok(response, "NGSFR")
        vals = _parse_floats(response.data_tokens)
        if not vals:
            # sometimes int Hz
            ints = _parse_ints(response.data_tokens)
            if not ints:
                raise ProtocolError("NGSFR empty")
            return float(ints[0])
        return vals[0]

    # --- Velocity group ---

    def vgvfs(self, axis: int, stage: int) -> bytes:
        return self._command("VGVFS", int(axis), int(stage))

    def decode_vgvfs(self, response: RciResponse, axis: int, stage: int) -> FilterStage:
        self.ensure_ok(response, "VGVFS")
        tokens = response.data_tokens
        if len(tokens) < 6:
            raise ProtocolError(f"VGVFS expected type+5 params, got {tokens}")
        ftype = int(tokens[0])
        params = _parse_floats(tuple(tokens[1:6]))
        while len(params) < 5:
            params.append(0.0)
        return FilterStage(
            axis=axis,
            stage=stage,
            filter_type=ftype,
            params=(params[0], params[1], params[2], params[3], params[4]),
        )

    def vsvfs(self, stage: FilterStage) -> bytes:
        return self._command(
            "VSVFS",
            stage.axis,
            stage.stage,
            stage.filter_type,
            *stage.params,
        )

    def vgsmv(self, axis: int) -> bytes:
        return self._command("VGSMV", int(axis))

    def decode_vgsmv(self, response: RciResponse) -> list[float]:
        self.ensure_ok(response, "VGSMV")
        return _parse_floats(response.data_tokens)

    def vssmv(self, axis: int, values: list[float] | tuple[float, ...]) -> bytes:
        if len(values) != 7:
            raise ProtocolError(f"VSSMV expects 7 sensor mults, got {len(values)}")
        return self._command("VSSMV", int(axis), *values)

    def vgmmv(self, axis: int) -> bytes:
        return self._command("VGMMV", int(axis))

    def decode_vgmmv(self, response: RciResponse) -> list[float]:
        self.ensure_ok(response, "VGMMV")
        return _parse_floats(response.data_tokens)

    def vsmmv(self, axis: int, values: list[float] | tuple[float, ...]) -> bytes:
        if len(values) != 12:
            raise ProtocolError(f"VSMMV expects 12 motor mults, got {len(values)}")
        return self._command("VSMMV", int(axis), *values)

    def vggiv(self) -> bytes:
        return self._command("VGGIV")

    def decode_vggiv(self, response: RciResponse) -> list[int]:
        self.ensure_ok(response, "VGGIV")
        return _parse_ints(response.data_tokens)

    # --- Position / proximity ---

    def cgsmv(self, axis: int) -> bytes:
        return self._command("CGSMV", int(axis))

    def decode_cgsmv(self, response: RciResponse) -> list[float]:
        self.ensure_ok(response, "CGSMV")
        return _parse_floats(response.data_tokens)

    def cssmv(self, axis: int, values: list[float] | tuple[float, ...]) -> bytes:
        if len(values) != 6:
            raise ProtocolError(f"CSSMV expects 6 sensor mults, got {len(values)}")
        return self._command("CSSMV", int(axis), *values)

    def cgmmv(self, axis: int) -> bytes:
        return self._command("CGMMV", int(axis))

    def decode_cgmmv(self, response: RciResponse) -> list[float]:
        self.ensure_ok(response, "CGMMV")
        return _parse_floats(response.data_tokens)

    def csmmv(self, axis: int, values: list[float] | tuple[float, ...]) -> bytes:
        if len(values) != 8:
            raise ProtocolError(f"CSMMV expects 8 motor mults, got {len(values)}")
        return self._command("CSMMV", int(axis), *values)

    def cgpov(self) -> bytes:
        return self._command("CGPOV")

    def decode_cgpov(self, response: RciResponse) -> list[float]:
        self.ensure_ok(response, "CGPOV")
        return _parse_floats(response.data_tokens)

    def cgpox(self) -> bytes:
        """Read the eight-proximity offset array used by newer firmware."""
        return self._command("CGPOX")

    def decode_cgpox(self, response: RciResponse) -> list[float]:
        self.ensure_ok(response, "CGPOX")
        values = _parse_floats(response.data_tokens)
        if len(values) != 8:
            raise ProtocolError(f"CGPOX expected 8 proximity offsets, got {values}")
        return values

    def cspov(self, values: list[float] | tuple[float, ...]) -> bytes:
        if len(values) != 6:
            raise ProtocolError(f"CSPOV expects 6 proximity offsets, got {len(values)}")
        return self._command("CSPOV", *values)

    def cspox(self, values: list[float] | tuple[float, ...]) -> bytes:
        """Write the eight-proximity offset array used by newer firmware."""
        if len(values) != 8:
            raise ProtocolError(f"CSPOX expects 8 proximity offsets, got {len(values)}")
        return self._command("CSPOX", *values)

    def cauco(self) -> bytes:
        """Use current proximity values as offsets."""
        return self._command("CAUCO")

    def caucx(self) -> bytes:
        """Use all eight current proximity values as offsets."""
        return self._command("CAUCX")

    def pggix(self) -> bytes:
        """Read all eight live proximity input values."""
        return self._command("PGGIX")

    def decode_pggix(self, response: RciResponse) -> list[float]:
        self.ensure_ok(response, "PGGIX")
        values = _parse_floats(response.data_tokens)
        if len(values) != 8:
            raise ProtocolError(f"PGGIX expected 8 proximity inputs, got {values}")
        return values

    def cgpfs(self, axis: int, stage: int) -> bytes:
        return self._command("CGPFS", int(axis), int(stage))

    def decode_cgpfs(self, response: RciResponse, axis: int, stage: int) -> FilterStage:
        self.ensure_ok(response, "CGPFS")
        tokens = response.data_tokens
        if len(tokens) < 6:
            raise ProtocolError(f"CGPFS expected type+5 params, got {tokens}")
        ftype = int(tokens[0])
        params = _parse_floats(tuple(tokens[1:6]))
        while len(params) < 5:
            params.append(0.0)
        return FilterStage(
            axis=axis,
            stage=stage,
            filter_type=ftype,
            params=(params[0], params[1], params[2], params[3], params[4]),
        )

    def cspfs(self, stage: FilterStage) -> bytes:
        return self._command(
            "CSPFS",
            stage.axis,
            stage.stage,
            stage.filter_type,
            *stage.params,
        )

    # --- Feedforward (stage FF filter subset) ---

    def fgffs(self) -> bytes:
        """Get feedforward subsystem status word(s)."""
        return self._command("FGFFS")

    def decode_fgffs(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "FGFFS")
        return list(response.data_tokens)

    def fsffs(self, *params: str | int | float) -> bytes:
        return self._command("FSFFS", *params)

    def fgpfs(self, axis: int, source: int, stage: int) -> bytes:
        """Get one FF filter stage using the documented wire address."""
        values = _typed_params("FGPFS", (axis, source, stage), "III")
        if not 0 <= values[0] <= 5:
            raise ProtocolError(f"FGPFS axis out of range: {values[0]}")
        if not 0 <= values[1] <= 6:
            raise ProtocolError(f"FGPFS source out of range: {values[1]}")
        if not 0 <= values[2] <= 7:
            raise ProtocolError(f"FGPFS stage out of range: {values[2]}")
        return self._command("FGPFS", *values)

    def decode_fgpfs(self, response: RciResponse, source: int, stage: int) -> FilterStage:
        self.ensure_ok(response, "FGPFS")
        tokens = response.data_tokens
        if len(tokens) < 6:
            raise ProtocolError(f"FGPFS expected type+5 params, got {tokens}")
        ftype = int(tokens[0])
        params = _parse_floats(tuple(tokens[1:6]))
        while len(params) < 5:
            params.append(0.0)
        return FilterStage(
            axis=source,
            stage=stage,
            filter_type=ftype,
            params=(params[0], params[1], params[2], params[3], params[4]),
        )

    def fspfs(self, axis: int, source: int, stage: FilterStage) -> bytes:
        address = _typed_params(
            "FSPFS",
            (axis, source, stage.stage, stage.filter_type),
            "IIII",
        )
        if not 0 <= address[0] <= 5:
            raise ProtocolError(f"FSPFS axis out of range: {address[0]}")
        if not 0 <= address[1] <= 6:
            raise ProtocolError(f"FSPFS source out of range: {address[1]}")
        if not 0 <= address[2] <= 7:
            raise ProtocolError(f"FSPFS stage out of range: {address[2]}")
        return self._command(
            "FSPFS",
            *address,
            *(
                _floating_param("FSPFS", index + 4, value)
                for index, value in enumerate(stage.params)
            ),
        )

    def fgffi(self) -> bytes:
        return self._command("FGFFI")

    def decode_fgffi(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "FGFFI")
        return list(response.data_tokens)

    # --- Diagnostics / noise ---

    def dgnty(self) -> bytes:
        return self._command("DGNTY")

    def decode_dgnty(self, response: RciResponse) -> int:
        self.ensure_ok(response, "DGNTY")
        vals = _parse_ints(response.data_tokens)
        if not vals:
            raise ProtocolError("DGNTY empty")
        return vals[0]

    def dsnty(self, noise_type: int) -> bytes:
        return self._command("DSNTY", int(noise_type))

    def dgnsg(self) -> bytes:
        return self._command("DGNSG")

    def decode_dgnsg(self, response: RciResponse) -> float:
        self.ensure_ok(response, "DGNSG")
        vals = _parse_floats(response.data_tokens)
        if vals:
            return vals[0]
        ints = _parse_ints(response.data_tokens)
        if not ints:
            raise ProtocolError("DGNSG empty")
        return float(ints[0])

    def dsnsg(self, gain: float) -> bytes:
        return self._command("DSNSG", float(gain))

    def dgnip(self) -> bytes:
        return self._command("DGNIP")

    def decode_dgnip(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "DGNIP")
        return list(response.data_tokens)

    def dsnip(self, *params: str | int | float) -> bytes:
        return self._command("DSNIP", *params)

    def dgcss(self) -> bytes:
        """Get current switch status."""
        return self._command("DGCSS")

    def decode_dgcss(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "DGCSS")
        return list(response.data_tokens)

    # --- NVRAM ---

    def nasup(self) -> bytes:
        """Save user parameters to NVRAM."""
        return self._command("NASUP")

    def narup(self) -> bytes:
        """Restore user parameters from NVRAM."""
        return self._command("NARUP")

    def naclr(self) -> bytes:
        """Clear NVRAM (dangerous)."""
        return self._command("NACLR")

    def bcncs(self) -> bytes:
        """Check NVRAM checksums (status plus saved/actual values)."""
        return self._command("BCNCS")

    def decode_bcncs(self, response: RciResponse) -> list[int]:
        self.ensure_ok(response, "BCNCS")
        values = _parse_ints(response.data_tokens)
        if len(values) != 7:
            raise ProtocolError(f"BCNCS expected 7 values, got {response.data_tokens}")
        return values

    def bbncs(self) -> bytes:
        """Build the monitor, firmware and configuration checksums."""
        return self._command("BBNCS")

    def decode_bbncs(self, response: RciResponse) -> list[int]:
        self.ensure_ok(response, "BBNCS")
        values = _parse_ints(response.data_tokens)
        if len(values) != 3:
            raise ProtocolError(f"BBNCS expected 3 values, got {response.data_tokens}")
        return values

    # --- Basic extras used by system pages ---

    def bgopl(self) -> bytes:
        return self._command("BGOPL")

    def decode_bgopl(self, response: RciResponse) -> int:
        self.ensure_ok(response, "BGOPL")
        vals = _parse_ints(response.data_tokens)
        if not vals:
            raise ProtocolError("BGOPL empty")
        return vals[0]

    def bsopl(self, percent: int) -> bytes:
        return self._command("BSOPL", int(percent))


    # --- Switch criterion ---

    def bgsws(self) -> bytes:
        return self._command("BGSWS")

    def decode_bgsws(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "BGSWS")
        return list(response.data_tokens)

    def bssws(self, *params: str | int | float) -> bytes:
        return self._command("BSSWS", *params)

    def bgocd(self) -> bytes:
        return self._command("BGOCD")

    def decode_bgocd(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "BGOCD")
        return list(response.data_tokens)

    def bsocd(self, *params: str | int | float) -> bytes:
        values = _typed_params("BSOCD", params, "IFFI")
        trigger_level = int(values[0])
        if not 0 <= trigger_level <= 100:
            raise ProtocolError(
                f"BSOCD trigger level must be between 0 and 100, got {trigger_level}"
            )
        return self._command("BSOCD", *values)

    # --- Motor protection ---

    def bgocv(self) -> bytes:
        return self._command("BGOCV")

    def decode_bgocv(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "BGOCV")
        return list(response.data_tokens)

    def bsocv(self, *params: str | int | float) -> bytes:
        return self._command("BSOCV", *params)

    def bgmpv(self) -> bytes:
        return self._command("BGMPV")

    def decode_bgmpv(self, response: RciResponse) -> list[float]:
        self.ensure_ok(response, "BGMPV")
        return _parse_floats(response.data_tokens)

    def bgmps(self) -> bytes:
        return self._command("BGMPS")

    def decode_bgmps(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "BGMPS")
        return list(response.data_tokens)

    def bgmcc(self) -> bytes:
        return self._command("BGMCC")

    def decode_bgmcc(self, response: RciResponse) -> float:
        self.ensure_ok(response, "BGMCC")
        values = _parse_floats(response.data_tokens)
        if len(values) != 1:
            raise ProtocolError(f"BGMCC expected 1 value, got {response.data_tokens}")
        return values[0]

    def bsmcc(self, value: float) -> bytes:
        return self._command("BSMCC", float(value))

    def dgade(self) -> bytes:
        return self._command("DGADE")

    def decode_dgade(self, response: RciResponse) -> list[int]:
        self.ensure_ok(response, "DGADE")
        # The vendor format is H#10.  Digit-only words (for example "0100")
        # are still hexadecimal and must not be parsed as decimal.
        try:
            values = [int(token, 16) for token in response.data_tokens]
        except ValueError as exc:
            raise ProtocolError(
                f"DGADE expected hexadecimal words, got {response.data_tokens}"
            ) from exc
        if len(values) != 10:
            raise ProtocolError(f"DGADE expected 10 values, got {response.data_tokens}")
        return values

    # --- Performance monitor ---

    def dgpmv(self) -> bytes:
        return self._command("DGPMV")

    def decode_dgpmv(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "DGPMV")
        return list(response.data_tokens)

    def dspmv(self, *params: str | int | float) -> bytes:
        return self._command("DSPMV", *params)

    def dgpms(self) -> bytes:
        return self._command("DGPMS")

    def decode_dgpms(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "DGPMS")
        return list(response.data_tokens)

    def dgslo(self) -> bytes:
        return self._command("DGSLO")

    def decode_dgslo(self, response: RciResponse) -> float:
        self.ensure_ok(response, "DGSLO")
        vals = _parse_floats(response.data_tokens)
        if not vals:
            raise ProtocolError("DGSLO empty")
        return vals[0]

    # --- Startup ramp ---

    def bgsut(self) -> bytes:
        return self._command("BGSUT")

    def decode_bgsut(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "BGSUT")
        return list(response.data_tokens)

    def bssut(self, *params: str | int | float) -> bytes:
        return self._command("BSSUT", *params)

    # --- DAC/ADC sequences ---

    def bgads(self) -> bytes:
        return self._command("BGADS")

    def decode_bgads(self, response: RciResponse) -> list[int]:
        self.ensure_ok(response, "BGADS")
        return _parse_ints(response.data_tokens)

    def bsads(self, values: list[int] | tuple[int, ...]) -> bytes:
        return self._command("BSADS", *[int(v) for v in values])

    def bgdas(self) -> bytes:
        return self._command("BGDAS")

    def decode_bgdas(self, response: RciResponse) -> list[int]:
        self.ensure_ok(response, "BGDAS")
        return _parse_ints(response.data_tokens)

    def bsdas(self, values: list[int] | tuple[int, ...]) -> bytes:
        return self._command("BSDAS", *[int(v) for v in values])

    # --- Pneumatic ---

    def pgpaf(self, axis: int, stage: int) -> bytes:
        return self._command("PGPAF", int(axis), int(stage))

    def decode_pgpaf(self, response: RciResponse, axis: int, stage: int) -> FilterStage:
        self.ensure_ok(response, "PGPAF")
        tokens = response.data_tokens
        if len(tokens) < 6:
            raise ProtocolError(f"PGPAF expected type+5 params, got {tokens}")
        ftype = int(tokens[0])
        params = _parse_floats(tuple(tokens[1:6]))
        while len(params) < 5:
            params.append(0.0)
        return FilterStage(
            axis=axis,
            stage=stage,
            filter_type=ftype,
            params=(params[0], params[1], params[2], params[3], params[4]),
        )

    def pspaf(self, stage: FilterStage) -> bytes:
        return self._command(
            "PSPAF",
            stage.axis,
            stage.stage,
            stage.filter_type,
            *stage.params,
        )

    def pgpsm(self, axis: int) -> bytes:
        return self._command("PGPSM", int(axis))

    def decode_pgpsm(self, response: RciResponse) -> list[float]:
        self.ensure_ok(response, "PGPSM")
        return _parse_floats(response.data_tokens)

    def pspsm(self, axis: int, values: list[float] | tuple[float, ...]) -> bytes:
        return self._command("PSPSM", int(axis), *[float(v) for v in values])

    def pgpcp(self) -> bytes:
        return self._command("PGPCP")

    def decode_pgpcp(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "PGPCP")
        tokens = response.data_tokens
        _typed_params("PGPCP", tokens, "III")
        return list(tokens)

    def pspcp(self, *params: str | int | float) -> bytes:
        # The legacy COM contract exposes all three values as Int32.  Keep
        # their wire representation integral as well: some controller builds
        # accept ordinary decimal tokens here but do not reliably apply the
        # generic scientific-notation float representation.
        values = _typed_params("PSPCP", params, "III")
        return self._command("PSPCP", *values)

    def pgpvo(self) -> bytes:
        return self._command("PGPVO")

    def decode_pgpvo(self, response: RciResponse) -> list[float]:
        self.ensure_ok(response, "PGPVO")
        return _parse_floats(response.data_tokens)

    def pspvo(self, values: list[float] | tuple[float, ...]) -> bytes:
        return self._command("PSPVO", *[float(v) for v in values])

    def pgpas(self) -> bytes:
        return self._command("PGPAS")

    def decode_pgpas(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "PGPAS")
        return list(response.data_tokens)

    def pgphv(self) -> bytes:
        return self._command("PGPHV")

    def decode_pgphv(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "PGPHV")
        return list(response.data_tokens)

    def pgpst(self) -> bytes:
        return self._command("PGPST")

    def decode_pgpst(self, response: RciResponse) -> tuple[float, float]:
        """Decode pneumatic position-status timers as ``(OK, NOK)`` seconds."""
        self.ensure_ok(response, "PGPST")
        values = _parse_floats(response.data_tokens)
        if len(values) != 2:
            raise ProtocolError(f"PGPST expected 2 timers, got {response.data_tokens}")
        return values[0], values[1]

    def pggiv(self) -> bytes:
        return self._command("PGGIV")

    def decode_pggiv(self, response: RciResponse) -> list[float]:
        self.ensure_ok(response, "PGGIV")
        return _parse_floats(response.data_tokens)

    def pgdit(self) -> bytes:
        return self._command("PGDIT", 0)

    def decode_pgdit(self, response: RciResponse) -> float:
        self.ensure_ok(response, "PGDIT")
        vals = _parse_floats(response.data_tokens)
        if not vals:
            raise ProtocolError("PGDIT empty")
        return vals[0]

    def psdit(self, value: float) -> bytes:
        return self._command("PSDIT", 0, float(value))

    def pgdfr(self) -> bytes:
        return self._command("PGDFR", 0)

    def decode_pgdfr(self, response: RciResponse) -> float:
        self.ensure_ok(response, "PGDFR")
        vals = _parse_floats(response.data_tokens)
        if not vals:
            ints = _parse_ints(response.data_tokens)
            if not ints:
                raise ProtocolError("PGDFR empty")
            return float(ints[0])
        return vals[0]

    def psdfr(self, freq: object) -> bytes:
        # PSDFR is one of the few pneumatic setters whose value is explicitly
        # an integer in the RCI.  Sending a float makes the generic encoder use
        # scientific notation (for example ``3.500000e+01``), which firmware
        # 3.3.122 rejects with OUT_OF_RANGE.
        return self._command("PSDFR", 0, _integral_param("PSDFR", 1, freq))

    def pgdca(self) -> bytes:
        return self._command("PGDCA")

    def decode_pgdca(self, response: RciResponse) -> float:
        self.ensure_ok(response, "PGDCA")
        vals = _parse_floats(response.data_tokens)
        if not vals:
            raise ProtocolError("PGDCA empty")
        return vals[0]

    def psdca(self, alpha: float) -> bytes:
        return self._command("PSDCA", float(alpha))

    def pgpss(self) -> bytes:
        return self._command("PGPSS")

    def decode_pgpss(self, response: RciResponse) -> int:
        self.ensure_ok(response, "PGPSS")
        vals = _parse_ints(response.data_tokens)
        if not vals:
            raise ProtocolError("PGPSS empty")
        return vals[0]

    def pspss(self, use_all: int) -> bytes:
        return self._command("PSPSS", int(use_all))

    def pauco(self, condition: int = 1) -> bytes:
        """Adopt current valve outputs as up (0/1) or down (2) offsets."""
        condition = int(condition)
        if condition not in (0, 1, 2):
            raise ProtocolError(f"PAUCO condition must be 0, 1 or 2, got {condition}")
        return self._command("PAUCO", condition)

    def pamov(self, action: int) -> bytes:
        return self._command("PAMOV", int(action))

    # --- Event logging (subset) ---

    def dgetp(self) -> bytes:
        return self._command("DGETP")

    def decode_dgetp(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "DGETP")
        return list(response.data_tokens)

    def dsetp(self, *params: str | int | float) -> bytes:
        return self._command("DSETP", *_typed_params("DSETP", params, "IIIIII"))

    def dgeti(self) -> bytes:
        return self._command("DGETI")

    def decode_dgeti(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "DGETI")
        return list(response.data_tokens)

    def dsset(self, logging_status: int) -> bytes:
        return self._command("DSSET", int(logging_status))

    def dgets(self) -> bytes:
        return self._command("DGETS")

    def decode_dgets(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "DGETS")
        return list(response.data_tokens)

    def dsets(self, *params: str | int | float) -> bytes:
        return self._command("DSETS", *_typed_params("DSETS", params, "IIIFI"))

    # --- PFF (pneumatic feedforward) subset ---

    def fgcpf(self) -> bytes:
        return self._command("FGCPF")

    def decode_fgcpf(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "FGCPF")
        return list(response.data_tokens)

    def fscpf(self, *params: str | int | float) -> bytes:
        return self._command("FSCPF", *params)

    def fggpf(self, *params: str | int | float) -> bytes:
        return self._command("FGGPF", *params)

    def decode_fggpf(self, response: RciResponse) -> list[float]:
        self.ensure_ok(response, "FGGPF")
        return _parse_floats(response.data_tokens)

    def fsgpf(self, *params: str | int | float) -> bytes:
        return self._command("FSGPF", *params)


    # --- Event logging deep ---

    def dgmos(self, sig_num: int) -> bytes:
        return self._command("DGMOS", int(sig_num))

    def decode_dgmos(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "DGMOS")
        return list(response.data_tokens)

    def dsmos(self, sig_num: int, *monsig: str | int | float) -> bytes:
        params = (sig_num, *monsig)
        return self._command("DSMOS", *_typed_params("DSMOS", params, "IIII"))

    def dgldv(self, trace_num: int, sample_num: int) -> bytes:
        return self._command("DGLDV", int(trace_num), int(sample_num))

    def decode_dgldv(self, response: RciResponse) -> list[float]:
        self.ensure_ok(response, "DGLDV")
        return _parse_floats(response.data_tokens)

    def dglda(self, trace_num: int) -> bytes:
        return self._command("DGLDA", int(trace_num))

    def decode_dglda(self, response: RciResponse) -> list[float]:
        self.ensure_ok(response, "DGLDA")
        return _parse_floats(response.data_tokens)

    def dgmsv(self, index1: int, index2: int) -> bytes:
        return self._command("DGMSV", int(index1), int(index2))

    def decode_dgmsv(self, response: RciResponse) -> list[float]:
        self.ensure_ok(response, "DGMSV")
        return _parse_floats(response.data_tokens)

    def dgevt(self, trace_num: int) -> bytes:
        return self._command("DGEVT", int(trace_num))

    def decode_dgevt(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "DGEVT")
        return list(response.data_tokens)

    # --- PFF deep ---

    def fgfsp(self, axis: int, source: int, stage: int) -> bytes:
        return self._command("FGFSP", int(axis), int(source), int(stage))

    def decode_fgfsp(
        self, response: RciResponse, axis: int, source: int, stage: int
    ) -> FilterStage:
        self.ensure_ok(response, "FGFSP")
        tokens = response.data_tokens
        if len(tokens) < 6:
            raise ProtocolError(f"FGFSP expected type+5 params, got {tokens}")
        ftype = int(tokens[0])
        params = _parse_floats(tuple(tokens[1:6]))
        while len(params) < 5:
            params.append(0.0)
        # encode source in axis high nibble-ish via stage tuple; keep axis as axis
        return FilterStage(
            axis=axis,
            stage=stage,
            filter_type=ftype,
            params=(params[0], params[1], params[2], params[3], params[4]),
        )

    def fsfsp(
        self,
        axis: int,
        source: int,
        stage: int,
        filter_type: int,
        params: tuple[float, float, float, float, float] | list[float],
    ) -> bytes:
        p = list(params)
        while len(p) < 5:
            p.append(0.0)
        return self._command(
            "FSFSP",
            int(axis),
            int(source),
            int(stage),
            int(filter_type),
            *p[:5],
        )

    def fgppf(self, source: int) -> bytes:
        return self._command("FGPPF", int(source))

    def decode_fgppf(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "FGPPF")
        return list(response.data_tokens)

    def fsppf(self, source: int, outputs: str | int, adaption_rate: float) -> bytes:
        return self._command("FSPPF", int(source), outputs, float(adaption_rate))

    def farpf(self, axis: int, source: int) -> bytes:
        return self._command("FARPF", int(axis), int(source))

    def fggpf_axis_source(self, axis: int, source: int) -> bytes:
        return self._command("FGGPF", int(axis), int(source))

    def fsgpf_axis_source(self, axis: int, source: int, gains: list[float] | tuple[float, ...]) -> bytes:
        if isinstance(gains, (int, float)):
            gains = [float(gains)]
        return self._command("FSGPF", int(axis), int(source), *[float(g) for g in gains])

    def fgipf(self) -> bytes:
        return self._command("FGIPF")

    def decode_fgipf(self, response: RciResponse) -> list[int]:
        self.ensure_ok(response, "FGIPF")
        return _parse_ints(response.data_tokens)

    def fsipf(self, inputs: list[int] | tuple[int, ...]) -> bytes:
        return self._command("FSIPF", *[int(x) for x in inputs])


    # --- System limits / constants ---

    def bgffl(self) -> bytes:
        return self._command("BGFFL")

    def decode_bgffl(self, response: RciResponse) -> int:
        self.ensure_ok(response, "BGFFL")
        vals = _parse_ints(response.data_tokens)
        if not vals:
            raise ProtocolError("BGFFL empty")
        return vals[0]

    def bsffl(self, percent: int) -> bytes:
        return self._command("BSFFL", int(percent))

    def bgfbl(self) -> bytes:
        return self._command("BGFBL")

    def decode_bgfbl(self, response: RciResponse) -> list[float]:
        self.ensure_ok(response, "BGFBL")
        return _parse_floats(response.data_tokens)

    def bsfbl(self, values: list[float] | tuple[float, ...]) -> bytes:
        return self._command("BSFBL", *[float(v) for v in values])

    def bgcot(self) -> bytes:
        return self._command("BGCOT")

    def decode_bgcot(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "BGCOT")
        return list(response.data_tokens)

    def bscot(self, *params: str | int | float) -> bytes:
        return self._command("BSCOT", *params)

    # --- Stage FF (non-pneumatic) deep ---

    def fgffg(self, *params: str | int | float) -> bytes:
        return self._command("FGFFG", *params)

    def decode_fgffg(self, response: RciResponse) -> list[float]:
        self.ensure_ok(response, "FGFFG")
        return _parse_floats(response.data_tokens)

    def fsffg(self, *params: str | int | float) -> bytes:
        return self._command("FSFFG", *params)

    def farff(self, *params: str | int | float) -> bytes:
        return self._command("FARFF", *params)

    def fgffc(self) -> bytes:
        return self._command("FGFFC")

    def decode_fgffc(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "FGFFC")
        return list(response.data_tokens)

    def fsffc(self, *params: str | int | float) -> bytes:
        if len(params) != 2:
            raise ProtocolError(f"FSFFC expects 2 parameters, got {len(params)}")
        # The second token is the firmware's OnOff character representation;
        # preserve it because supported builds return both numeric and letter
        # forms.  Only NoOfGains has an unambiguous integer contract.
        return self._command(
            "FSFFC", _integral_param("FSFFC", 0, params[0]), params[1]
        )

    def fgffp(self, *params: str | int | float) -> bytes:
        return self._command("FGFFP", *params)

    def decode_fgffp(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "FGFFP")
        return list(response.data_tokens)

    def fsffp(self, *params: str | int | float) -> bytes:
        return self._command("FSFFP", *params)

    def fgsfm(self) -> bytes:
        return self._command("FGSFM")

    def decode_fgsfm(self, response: RciResponse) -> list[float]:
        self.ensure_ok(response, "FGSFM")
        return _parse_floats(response.data_tokens)

    def fssfm(self, values: list[float] | tuple[float, ...]) -> bytes:
        return self._command("FSSFM", *[float(v) for v in values])

    def fgfat(self) -> bytes:
        return self._command("FGFAT")

    def decode_fgfat(self, response: RciResponse) -> int:
        self.ensure_ok(response, "FGFAT")
        vals = _parse_ints(response.data_tokens)
        if not vals:
            raise ProtocolError("FGFAT empty")
        return vals[0]

    def fsfat(self, algo: int) -> bytes:
        return self._command("FSFAT", int(algo))

    def fgzrp(self) -> bytes:
        return self._command("FGZRP")

    def decode_fgzrp(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "FGZRP")
        return list(response.data_tokens)

    def fszrp(self, *params: str | int | float) -> bytes:
        return self._command("FSZRP", *params)

    # already have fgffi/fsffs; ensure fsffi exists
    def fsffi(self, *params: str | int | float) -> bytes:
        if not params:
            raise ProtocolError("FSFFI expects at least 1 parameter")
        values = tuple(
            _integral_param("FSFFI", index, value)
            for index, value in enumerate(params)
        )
        return self._command("FSFFI", *values)

    # --- Diagnostics deep ---

    def dgesp(self) -> bytes:
        return self._command("DGESP")

    def decode_dgesp(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "DGESP")
        return list(response.data_tokens)

    def dsesp(self, *params: str | int | float) -> bytes:
        return self._command("DSESP", *_typed_params("DSESP", params, "IFFFF"))

    def dgnsf(self) -> bytes:
        return self._command("DGNSF")

    def decode_dgnsf(self, response: RciResponse) -> float:
        self.ensure_ok(response, "DGNSF")
        vals = _parse_floats(response.data_tokens)
        if not vals:
            raise ProtocolError("DGNSF empty")
        return vals[0]

    def dsnsf(self, freq: float) -> bytes:
        return self._command("DSNSF", float(freq))

    def dgnfu(self) -> bytes:
        return self._command("DGNFU")

    def decode_dgnfu(self, response: RciResponse) -> str:
        self.ensure_ok(response, "DGNFU")
        if not response.data_tokens:
            raise ProtocolError("DGNFU empty")
        return response.data_tokens[0]

    def dsnfu(self, on_off: str) -> bytes:
        return self._command("DSNFU", str(on_off))

    def dgnfs(self, stage: int) -> bytes:
        return self._command("DGNFS", int(stage))

    def decode_dgnfs(self, response: RciResponse, stage: int = 0) -> FilterStage:
        self.ensure_ok(response, "DGNFS")
        tokens = response.data_tokens
        if len(tokens) < 6:
            raise ProtocolError(f"DGNFS expected type+5 params, got {tokens}")
        ftype = int(tokens[0])
        params = _parse_floats(tuple(tokens[1:6]))
        while len(params) < 5:
            params.append(0.0)
        return FilterStage(0, stage, ftype, (params[0], params[1], params[2], params[3], params[4]))

    def dsnfs(self, stage: FilterStage) -> bytes:
        return self._command(
            "DSNFS",
            stage.stage,
            stage.filter_type,
            *stage.params,
        )

    def dgdos(self) -> bytes:
        return self._command("DGDOS")

    def decode_dgdos(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "DGDOS")
        return list(response.data_tokens)

    def dsdos(self, *params: str | int | float) -> bytes:
        return self._command("DSDOS", *params)

    def dgtmo(self) -> bytes:
        return self._command("DGTMO")

    def decode_dgtmo(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "DGTMO")
        return list(response.data_tokens)

    def dstmo(self, *params: str | int | float) -> bytes:
        return self._command("DSTMO", *params)

    # Classic digital trace
    def dgtiv(self) -> bytes:
        return self._command("DGTIV")

    def decode_dgtiv(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "DGTIV")
        return list(response.data_tokens)

    def dstiv(self, *params: str | int | float) -> bytes:
        return self._command("DSTIV", *params)

    def dasta(self) -> bytes:
        return self._command("DASTA")

    def decode_dasta(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "DASTA")
        return list(response.data_tokens)

    def dgtas(self) -> bytes:
        return self._command("DGTAS")

    def decode_dgtas(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "DGTAS")
        return list(response.data_tokens)

    def dgtbv(self, read_offset: int = -1) -> bytes:
        """Read up to 16 trace samples from the documented buffer offset."""
        read_offset = int(read_offset)
        if not -1 <= read_offset <= 8191:
            raise ProtocolError(
                f"DGTBV read offset must be -1..8191, got {read_offset}"
            )
        return self._command("DGTBV", read_offset)

    def decode_dgtbv(self, response: RciResponse) -> list[float]:
        self.ensure_ok(response, "DGTBV")
        values = _parse_floats(response.data_tokens)
        if not values:
            raise ProtocolError("DGTBV empty")
        sample_count = int(values[0])
        expected = 1 + 2 * sample_count
        if sample_count < 0 or sample_count > 16 or len(values) < expected:
            raise ProtocolError(
                "DGTBV invalid payload: "
                f"sample_count={sample_count}, values={len(values)}"
            )
        # Keep the session/UI API as the flattened Ch1 + Ch2 samples; the
        # leading count is protocol metadata, not a displayed sample.
        return values[1:expected]

    # --- Position devices / motor offsets ---

    def cgpsd(self, axis: int | None = None) -> bytes:
        return self._command("CGPSD", *(() if axis is None else (int(axis),)))

    def decode_cgpsd(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "CGPSD")
        return list(response.data_tokens)

    def cspsd(self, *params: str | int | float) -> bytes:
        return self._command("CSPSD", *params)

    def cgpmd(self, axis: int | None = None) -> bytes:
        return self._command("CGPMD", *(() if axis is None else (int(axis),)))

    def decode_cgpmd(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "CGPMD")
        return list(response.data_tokens)

    def cspmd(self, *params: str | int | float) -> bytes:
        return self._command("CSPMD", *params)

    def cgmov(self) -> bytes:
        return self._command("CGMOV")

    def decode_cgmov(self, response: RciResponse) -> list[float]:
        self.ensure_ok(response, "CGMOV")
        return _parse_floats(response.data_tokens)

    def csmov(self, values: list[float] | tuple[float, ...]) -> bytes:
        if len(values) != 11:
            raise ValueError(f"CSMOV requires 11 legacy offsets, got {len(values)}")
        return self._command("CSMOV", *[float(v) for v in values])

    # --- NVRAM extras ---

    def nssfr(self, hz: float, mode: int = 0) -> bytes:
        # The mode parameter is mandatory even though RCI 1.1 documents it as
        # having no effect.  Older mocks accepted the truncated one-parameter
        # frame, while the real controller times it out.
        return self._command("NSSFR", float(hz), int(mode))

    def ngexl(self) -> bytes:
        return self._command("NGEXL")

    def decode_ngexl(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "NGEXL")
        return list(response.data_tokens)

    def nsexl(self, *params: str | int | float) -> bytes:
        return self._command("NSEXL", *params)

    def ngasn(self) -> bytes:
        return self._command("NGASN")

    def decode_ngasn(self, response: RciResponse) -> int:
        self.ensure_ok(response, "NGASN")
        vals = _parse_ints(response.data_tokens)
        if not vals:
            raise ProtocolError("NGASN empty")
        return vals[0]

    def nsasn(self, n: int) -> bytes:
        return self._command("NSASN", int(n))

    # --- Analysis filter logging (L*) ---

    def lganp(self) -> bytes:
        return self._command("LGANP")

    def decode_lganp(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "LGANP")
        return list(response.data_tokens)

    def lsanp(self, *params: str | int | float) -> bytes:
        return self._command("LSANP", *_typed_params("LSANP", params, "III"))

    def lgais(self) -> bytes:
        return self._command("LGAIS")

    def decode_lgais(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "LGAIS")
        return list(response.data_tokens)

    def lsais(self, *params: str | int | float) -> bytes:
        return self._command("LSAIS", *_typed_params("LSAIS", params, "IIII"))

    def lgafc(self, *params: str | int | float) -> bytes:
        return self._command("LGAFC", *params)

    def decode_lgafc(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "LGAFC")
        return list(response.data_tokens)

    def lsafc(self, *params: str | int | float) -> bytes:
        return self._command("LSAFC", *_typed_params("LSAFC", params, "IIIFFFFF"))

    def lgafo(self) -> bytes:
        return self._command("LGAFO")

    def decode_lgafo(self, response: RciResponse) -> list[float]:
        self.ensure_ok(response, "LGAFO")
        return _parse_floats(response.data_tokens)

    def lgaen(self) -> bytes:
        return self._command("LGAEN")

    def decode_lgaen(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "LGAEN")
        return list(response.data_tokens)

    def lgaev(self) -> bytes:
        return self._command("LGAEV")

    def decode_lgaev(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "LGAEV")
        return list(response.data_tokens)

    def lgafs(self) -> bytes:
        return self._command("LGAFS")

    def decode_lgafs(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "LGAFS")
        return list(response.data_tokens)

    def lsafs(self, *params: str | int | float) -> bytes:
        if not params:
            raise ProtocolError("LSAFS expects at least 1 parameter")
        values: tuple[int | float, ...] = (
            _integral_param("LSAFS", 0, params[0]),
            *(
                _floating_param("LSAFS", index, value)
                for index, value in enumerate(params[1:], 1)
            ),
        )
        return self._command("LSAFS", *values)

    # raw escape hatch
    def raw(self, mnemonic: str, *params: str | int | float) -> bytes:
        return self._command(mnemonic, *params)

    def parse(self, raw: str | bytes) -> RciResponse:
        return parse_frame(raw)

    # === Newly added commands ===

    # VelAxes output limiter (BGFBL/BSFBL already exist)

    # FF Zrot parameters (FGZRP/FSZRP already exist)

    # Excitation parameters (DGESP/DSESP already exist)

    # Event signal (DGETS/DSETS already exist)

    # Monitor signal (DGMOS/DSMOS already exist)

    # Actual time
    def dgati(self) -> bytes:
        return self._command("DGATI")

    def decode_dgati(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "DGATI")
        return list(response.data_tokens)

    def dsati(self, *params: str | int | float) -> bytes:
        return self._command("DSATI", *_typed_params("DSATI", params, "IIII"))

    # Floor FF adaptive algorithm
    def fgfat(self) -> bytes:
        return self._command("FGFAT")

    def decode_fgfat(self, response: RciResponse) -> int:
        self.ensure_ok(response, "FGFAT")
        vals = _parse_ints(response.data_tokens)
        if not vals:
            raise ProtocolError("FGFAT empty")
        return vals[0]

    def fsfat(self, algo: int) -> bytes:
        return self._command("FSFAT", int(algo))

    # Pneumatic ramp parameters
    def pgprp(self) -> bytes:
        return self._command("PGPRP")

    def decode_pgprp(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "PGPRP")
        tokens = response.data_tokens
        _typed_params("PGPRP", tokens, "IFFFF")
        return list(tokens)

    def psprp(self, *params: str | int | float) -> bytes:
        return self._command("PSPRP", *_typed_params("PSPRP", params, "IFFFF"))

    # Use pneumatic axis setpoint for all axes (PGPSS/PSPSS already exist)

    # Cascaded position filter
    def cgpcf(self, stage: int) -> bytes:
        return self._command("CGCPF", int(stage))

    def decode_cgpcf(self, response: RciResponse, stage: int = 0) -> FilterStage:
        self.ensure_ok(response, "CGCPF")
        tokens = response.data_tokens
        if len(tokens) < 6:
            raise ProtocolError(f"CGCPF expected type+5 params, got {tokens}")
        ftype = int(tokens[0])
        params = _parse_floats(tuple(tokens[1:6]))
        while len(params) < 5:
            params.append(0.0)
        return FilterStage(0, stage, ftype, (params[0], params[1], params[2], params[3], params[4]))

    def cspcf(self, stage: int, filter_type: int, *params: float) -> bytes:
        return self._command("CSCPF", int(stage), int(filter_type), *params)

    # Cascaded position parameter
    def cgpcm(self) -> bytes:
        return self._command("CGCPP")

    def decode_cgpcm(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "CGCPP")
        return list(response.data_tokens)

    def cspcm(self, *params: str | int | float) -> bytes:
        return self._command("CSCPP", *params)

    # Non-linear position parameter
    def cgpnp(self) -> bytes:
        return self._command("CGSFP")

    def decode_cgpnp(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "CGSFP")
        return list(response.data_tokens)

    def cspnp(self, *params: str | int | float) -> bytes:
        return self._command("CSSFP", *_typed_params("CSSFP", params, "IIFF"))

    # Firmware config info
    def bggsc(self) -> bytes:
        return self._command("BGGSC")

    def decode_bggsc(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "BGGSC")
        return list(response.data_tokens)

    # Power-supply current limitation (newer firmware extension)
    def lgpsl(self) -> bytes:
        return self._command("LGPSL")

    def decode_lgpsl(self, response: RciResponse) -> list[str]:
        self.ensure_ok(response, "LGPSL")
        return list(response.data_tokens)

    def lspsl(self, *params: str | int | float) -> bytes:
        return self._command("LSPSL", *params)

    # ZMS stability
    def bgsvt(self) -> bytes:
        return self._command("BGSVT")

    def decode_bgsvt(self, response: RciResponse) -> list[float]:
        self.ensure_ok(response, "BGSVT")
        return _parse_floats(response.data_tokens)

    def bssvt(self, *params: str | int | float) -> bytes:
        return self._command("BSSVT", *params)

    def bglse(self) -> bytes:
        return self._command("BGLSE")

    def decode_bglse(self, response: RciResponse) -> tuple[int, float]:
        self.ensure_ok(response, "BGLSE")
        if len(response.data_tokens) != 2:
            raise ProtocolError(f"BGLSE expected 2 values, got {response.data_tokens}")
        return _parse_ints(response.data_tokens[:1])[0], _parse_floats(response.data_tokens[1:])[0]

    def bgsrv(self) -> bytes:
        return self._command("BGSRV")

    def decode_bgsrv(self, response: RciResponse) -> list[float]:
        self.ensure_ok(response, "BGSRV")
        return _parse_floats(response.data_tokens)
