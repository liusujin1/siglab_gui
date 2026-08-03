"""RCI serial framing for IDE OPTICON / TC-MFD firmware.

Based on official document:
  Remote command interface description.DOC / 1.3 (17-Dec-2012)

Command telegram:
  : <len_hex2> <msg_id> <data> <crc_hex2> \\r
  data = "<crl> <CMD5> [params...]"

Response telegram:
  : <len_hex2> <msg_id> <proto_status 0|1> <data> <crc_hex2> \\r
  data (accept) = "<crl> <status_code_hex> <CMD5> [response-data...]"
"""

from __future__ import annotations

from dataclasses import dataclass


class ProtocolError(ValueError):
    """Malformed RCI frame or CRC/length mismatch."""


def xor_checksum(payload: str) -> int:
    """XOR of all bytes between prefix and CRC (i.e. of ``payload``)."""
    value = 0
    for ch in payload.encode("ascii"):
        value ^= ch
    return value & 0xFF


def format_crc(value: int) -> str:
    return f"{value & 0xFF:02X}"


@dataclass(frozen=True, slots=True)
class RciCommand:
    """Host → controller command fields (logical, pre-framing)."""

    crl: str
    mnemonic: str
    params: tuple[str, ...] = ()
    msg_id: str = "?"

    def __post_init__(self) -> None:
        if len(self.msg_id) != 1:
            raise ProtocolError("msg_id must be a single printable ASCII character")
        if not (0x20 <= ord(self.msg_id) <= 0x7E):
            raise ProtocolError("msg_id out of printable range")
        if len(self.mnemonic) != 5:
            raise ProtocolError(f"mnemonic must be 5 chars, got {self.mnemonic!r}")
        if not self.crl:
            raise ProtocolError("crl must be non-empty hex link id")

    @property
    def data_field(self) -> str:
        parts = [self.crl, self.mnemonic, *self.params]
        return " ".join(parts)


@dataclass(frozen=True, slots=True)
class RciResponse:
    """Controller → host response (logical)."""

    msg_id: str
    protocol_ok: bool
    crl: str
    status_code: int
    mnemonic: str
    data_tokens: tuple[str, ...]
    raw: str
    reject_reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.protocol_ok and self.status_code == 0

    @property
    def data_text(self) -> str:
        return " ".join(self.data_tokens)


def build_frame(
    command: RciCommand,
    *,
    bypass_length: bool = False,
    bypass_crc: bool = False,
) -> bytes:
    """Build a complete command telegram including ``:`` and ``\\r``."""
    data = command.data_field
    # Wire layout after prefix, before CRC: length(2) + msg_id(1) + data
    # length counts characters between prefix and CRC (excluding both).
    body_without_len = command.msg_id + data
    length = 2 + len(body_without_len)  # include the two length digits themselves
    if length >= 0xF8 and not bypass_length:
        raise ProtocolError(f"command length {length:#x} exceeds 0xF7; use bypass or split")

    len_field = "##" if bypass_length else f"{length:02X}"
    mid = f"{len_field}{command.msg_id}{data}"
    crc = "##" if bypass_crc else format_crc(xor_checksum(mid))
    frame = f":{mid}{crc}\r"
    return frame.encode("ascii")


def parse_frame(raw: str | bytes) -> RciResponse:
    """Parse one response telegram (with or without trailing CR)."""
    if isinstance(raw, bytes):
        try:
            text = raw.decode("ascii")
        except UnicodeDecodeError as exc:
            raise ProtocolError(f"non-ASCII response: {exc}") from exc
    else:
        text = raw

    text = text.strip("\r\n")
    if not text.startswith(":"):
        raise ProtocolError(f"response missing prefix: {text!r}")
    if len(text) < 8:
        raise ProtocolError(f"response too short: {text!r}")

    body = text[1:]  # drop ':'
    # CRC is last two chars; everything before is length-checked region
    mid, crc_field = body[:-2], body[-2:]
    if crc_field != "##":
        try:
            expected = int(crc_field, 16)
        except ValueError as exc:
            raise ProtocolError(f"invalid CRC field {crc_field!r}") from exc
        actual = xor_checksum(mid)
        if actual != expected:
            raise ProtocolError(
                f"CRC mismatch: frame={crc_field} computed={actual:02X} raw={text!r}"
            )

    if len(mid) < 4:
        raise ProtocolError(f"response body too short: {text!r}")

    len_field, rest = mid[:2], mid[2:]
    if len_field != "##":
        try:
            declared = int(len_field, 16)
        except ValueError as exc:
            raise ProtocolError(f"invalid length field {len_field!r}") from exc
        # Documented: length = chars between prefix and CRC.  The firmware can
        # return more than 255 bytes for these three buffer commands and then
        # places only the low byte of the real length in the two-digit field.
        # Identify the fixed-width response mnemonic before enforcing length;
        # keep strict validation for every normal response.
        early_fields = rest[2:].strip().split(maxsplit=2)
        early_mnemonic = (
            early_fields[2][:5]
            if len(early_fields) == 3 and len(early_fields[2]) >= 5
            else ""
        )
        wrapped_buffer_length = (
            len(mid) >= 0x100
            and early_mnemonic in {"DGLDV", "DGTBV", "DGMSV"}
            and declared == (len(mid) & 0xFF)
        )
        if declared != len(mid) and not wrapped_buffer_length:
            raise ProtocolError(
                f"length mismatch: declared={declared} actual={len(mid)} raw={text!r}"
            )

    msg_id = rest[0]
    proto_status_ch = rest[1]
    data = rest[2:].strip()

    # The RCI document says that the message-level status is only 0 (accept)
    # or 1 (protocol reject).  Real 3.3 firmware also copies a non-zero TC
    # command status into this character.  For example an unsupported command
    # is returned as::
    #
    #   :1E?30A 03 PGPSXUNKNOWN COMMAND69\r
    #
    # Notice that the five-character mnemonic is immediately followed by the
    # error text.  Parse the fixed-width mnemonic instead of relying on a
    # separating space so callers receive RciCommandError and can perform a
    # documented-command fallback.
    fields = data.split(maxsplit=2)
    structured = len(fields) == 3 and len(fields[2]) >= 5
    if structured:
        crl, status_s, tail = fields
        mnemonic = tail[:5]
        payload = tail[5:].strip().split()
        if not (1 <= len(crl) <= 2 and all(ch in "0123456789abcdefABCDEF" for ch in crl)):
            structured = False
        if not (1 <= len(status_s) <= 4 and all(ch in "0123456789abcdefABCDEF" for ch in status_s)):
            structured = False
        if len(mnemonic) != 5 or not mnemonic.isalnum():
            structured = False

    if not structured:
        if proto_status_ch == "1":
            # True message/protocol rejection: the data is only a textual
            # reason and has no CRL/status/mnemonic tuple.
            reason = data or "REJECT"
            return RciResponse(
                msg_id=msg_id,
                protocol_ok=False,
                crl="",
                status_code=-1,
                mnemonic="",
                data_tokens=tuple(data.split()),
                raw=text,
                reject_reason=reason,
            )
        if proto_status_ch != "0":
            raise ProtocolError(f"invalid protocol status {proto_status_ch!r} in {text!r}")
        raise ProtocolError(f"accept response missing fields: {text!r}")

    try:
        status_code = int(status_s, 16)
    except ValueError as exc:
        raise ProtocolError(f"invalid status code {status_s!r}") from exc

    return RciResponse(
        msg_id=msg_id,
        protocol_ok=True,
        crl=crl,
        status_code=status_code,
        mnemonic=mnemonic,
        data_tokens=tuple(payload),
        raw=text,
    )


def next_crl(counter: int) -> str:
    """Two-digit hex CRL in 00..FF, rolling."""
    return f"{counter & 0xFF:02X}"
