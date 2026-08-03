"""Unit tests for RCI framing (doc examples + round-trip)."""

from __future__ import annotations

import pytest

from python_samba.protocol.commands import CommandEncoder, RciCommandError
from python_samba.protocol.frame import (
    ProtocolError,
    RciCommand,
    build_frame,
    parse_frame,
    xor_checksum,
)


def test_xor_checksum_basic():
    assert xor_checksum("") == 0
    assert xor_checksum("A") == ord("A")
    assert xor_checksum("AB") == ord("A") ^ ord("B")


def test_build_frame_with_bypass_matches_doc_shape():
    # Doc example conceptually: :0B?AA BGVIS##\r  (length/crc may be ##)
    cmd = RciCommand(crl="00", mnemonic="BGVIS", msg_id="?")
    frame = build_frame(cmd, bypass_length=True, bypass_crc=True)
    assert frame == b":##?00 BGVIS##\r"


def test_build_and_parse_roundtrip_bgvis_style_response():
    # Host command with real CRC
    cmd = RciCommand(crl="AA", mnemonic="BGVIS", msg_id="?")
    frame = build_frame(cmd)
    assert frame.startswith(b":")
    assert frame.endswith(b"\r")
    # Manually craft accept response like doc example 1
    # :18?0AA 00 BGVIS 4 0 1 1279\r  — verify our parser handles similar
    raw = b":18?0AA 00 BGVIS 4 0 1 12\r"
    # Fix CRC to match actual mid
    mid = raw[1:-3].decode("ascii")  # without : crc \r — careful
    # Rebuild properly
    from python_samba.protocol.frame import format_crc

    data = "AA 00 BGVIS 4 0 1 12"
    msg_id = "?"
    proto = "0"
    body_core = f"{msg_id}{proto}{data}"
    length = 2 + len(body_core)
    len_field = f"{length:02X}"
    mid = f"{len_field}{body_core}"
    raw = f":{mid}{format_crc(xor_checksum(mid))}\r"
    resp = parse_frame(raw)
    assert resp.ok
    assert resp.crl == "AA"
    assert resp.mnemonic == "BGVIS"
    assert resp.data_tokens == ("4", "0", "1", "12")


def test_decode_bgvis_accepts_labelled_real_controller_response():
    # Captured read-only from a SAMBA controller.  The trailing two characters
    # are the frame CRC, not part of the LibBldTime value.
    raw = (
        ":9E?000 00 BGVIS 3 3 122 103 9 "
        "FWCompiler: 7004021 FWBldDate: Mar 15 2019 FWBldTime: 11:05:37 "
        "LibCompiler: 7004016 LibBldDate: Jun 24 2016 LibBldTime: 08:28:4770\r"
    )
    response = parse_frame(raw)
    version = CommandEncoder().decode_bgvis(response)

    assert (version.major, version.minor, version.patch) == (3, 3, 122)
    assert version.lib == 103
    assert version.main_board == 9
    assert str(version) == "V3.3.122 (lib 103)"
    assert version.full_text.startswith("3 3 122 103 9 FWCompiler:")
    assert version.full_text.endswith("LibBldTime: 08:28:47")


def test_parse_protocol_reject():
    from python_samba.protocol.frame import format_crc

    reason = "CRC ERROR"
    msg_id = "$"
    proto = "1"
    body_core = f"{msg_id}{proto}{reason}"
    length = 2 + len(body_core)
    mid = f"{length:02X}{body_core}"
    raw = f":{mid}{format_crc(xor_checksum(mid))}\r"
    resp = parse_frame(raw)
    assert not resp.protocol_ok
    assert "CRC ERROR" in (resp.reject_reason or "")


def test_parse_real_controller_unknown_command_response():
    # Captured from firmware 3.3.122.  Contrary to the prose in the RCI spec,
    # command error 03 is copied into the message status and the reason is
    # concatenated directly to the fixed-width mnemonic.
    raw = ":1E?30A 03 PGPSXUNKNOWN COMMAND69\r"
    resp = parse_frame(raw)

    assert resp.protocol_ok
    assert not resp.ok
    assert resp.crl == "0A"
    assert resp.status_code == 0x03
    assert resp.mnemonic == "PGPSX"
    assert resp.data_tokens == ("UNKNOWN", "COMMAND")

    with pytest.raises(RciCommandError, match="UNKNOWN_COMMAND"):
        CommandEncoder().ensure_ok(resp, "PGPSX")


def test_parse_real_controller_wrapped_dgtbv_length():
    # DGTBV/DGLDV/DGMSV are documented exceptions to the one-byte length
    # field.  This captured 434-byte region is 0x1B2 bytes, while the firmware
    # puts only 0xB2 in the header.
    raw = (
        ":B2?044 00 DGTBV 16 "
        "+7.49213E-01 +5.50793E-01 +1.62194E-01 +2.02262E-01 "
        "+2.23419E-01 +1.82835E-01 +2.21661E-01 +4.17784E-01 "
        "+7.09463E-01 +6.25140E-01 -3.50883E-02 +3.19102E-01 "
        "+6.25237E-01 +6.85460E-01 +5.60116E-01 +6.96642E-01 "
        "+7.49213E-01 +5.50793E-01 +1.62194E-01 +2.02262E-01 "
        "+2.23419E-01 +1.82835E-01 +2.21661E-01 +4.17784E-01 "
        "+7.09463E-01 +6.25140E-01 -3.50883E-02 +3.19102E-01 "
        "+6.25237E-01 +6.85460E-01 +5.60116E-01 +6.96642E-011B\r"
    )

    response = parse_frame(raw)

    assert response.ok
    assert response.mnemonic == "DGTBV"
    assert response.data_tokens[0] == "16"
    assert len(response.data_tokens) == 33


def test_parse_crc_mismatch():
    with pytest.raises(ProtocolError, match="CRC"):
        parse_frame(":0B?00 BGVIS00\r")


def test_mnemonic_length_enforced():
    with pytest.raises(ProtocolError):
        RciCommand(crl="00", mnemonic="BGVI")


def test_real_controller_integer_and_required_mode_frames():
    encoder = CommandEncoder(bypass_length=True, bypass_crc=True)

    sample_frame = encoder.nssfr(5000)
    dither_frame = encoder.psdfr(35.0)
    pressure_up_frame = encoder.pauco(1)
    pressure_down_frame = encoder.pauco(2)
    trace_buffer_frame = encoder.dgtbv(0)
    floatation_frame = encoder.pspcp("93.0", 12469, 56.0)

    assert b"NSSFR 5.000000e+03 0" in sample_frame
    assert b"PSDFR 0 35" in dither_frame
    assert b"3.500000e+01" not in dither_frame
    assert b"PAUCO 1" in pressure_up_frame
    assert b"PAUCO 2" in pressure_down_frame
    assert b"DGTBV 0" in trace_buffer_frame
    assert b"PSPCP 93 12469 56" in floatation_frame
    assert b"e+" not in floatation_frame

    with pytest.raises(ProtocolError, match="PAUCO condition"):
        encoder.pauco(3)
    with pytest.raises(ProtocolError, match="DGTBV read offset"):
        encoder.dgtbv(8192)
    with pytest.raises(ProtocolError, match="PSPCP expects"):
        encoder.pspcp(93, 12469)
    with pytest.raises(ProtocolError, match="expects an integer"):
        encoder.pspcp(93, 12469.5, 56)
    with pytest.raises(ProtocolError, match="PSDFR.*expects an integer"):
        encoder.psdfr(35.5)


def test_mixed_integer_float_command_contracts_are_typed_before_framing():
    encoder = CommandEncoder(bypass_length=True, bypass_crc=True)

    frames = {
        "event params": encoder.dsetp(2.0, 512.0, 4.0, 3.0, 8.0, 1.0),
        "event signal": encoder.dsets(5.0, 2.0, 1.0, 0.75, 12.0),
        "monitor signal": encoder.dsmos(7.0, 2.0, 4.0, 3.0),
        "excitation": encoder.dsesp(2.0, 0.25, 12.5, 3.0, 4.0),
        "ff config": encoder.fsffc(6.0, "50"),
        "ff inputs": encoder.fsffi(0.0, 1.0, 2.0),
        "analysis params": encoder.lsanp(1.0, 2.0, 1.0),
        "analysis input": encoder.lsais(0.0, 1.0, 2.0, 3.0),
        "analysis filter": encoder.lsafc(
            0.0, 1.0, 4.0, 0.1, 0.2, 0.3, 0.4, 0.5
        ),
        "analysis thresholds": encoder.lsafs(0.0, 0.25, 0.5),
        "actual time": encoder.dsati(1.0, 2.0, 3.0, 4.0),
        "pneumatic ramp": encoder.psprp(1.0, 1.25, 2.5, 3.75, 4.5),
        "nonlinear position": encoder.cspnp(2.0, 1.0, 0.125, 3.5),
    }

    assert b"DSETP 2 512 4 3 8 1" in frames["event params"]
    assert b"DSETS 5 2 1 7.500000e-01 12" in frames["event signal"]
    assert b"DSMOS 7 2 4 3" in frames["monitor signal"]
    assert b"DSESP 2 2.500000e-01 1.250000e+01" in frames["excitation"]
    assert b"FSFFC 6 50" in frames["ff config"]
    assert b"FSFFI 0 1 2" in frames["ff inputs"]
    assert b"LSANP 1 2 1" in frames["analysis params"]
    assert b"LSAIS 0 1 2 3" in frames["analysis input"]
    assert b"LSAFC 0 1 4 1.000000e-01" in frames["analysis filter"]
    assert b"LSAFS 0 2.500000e-01 5.000000e-01" in frames[
        "analysis thresholds"
    ]
    assert b"DSATI 1 2 3 4" in frames["actual time"]
    assert b"PSPRP 1 1.250000e+00 2.500000e+00" in frames[
        "pneumatic ramp"
    ]
    assert b"CSSFP 2 1 1.250000e-01 3.500000e+00" in frames[
        "nonlinear position"
    ]

    with pytest.raises(ProtocolError, match="DSETP expects 6"):
        encoder.dsetp(1, 2, 3, 4, 5)
    with pytest.raises(ProtocolError, match="DSETS parameter 1 expects an integer"):
        encoder.dsets(1.5, 2, 3, 0.25, 4)
    with pytest.raises(ProtocolError, match="FSFFI expects at least"):
        encoder.fsffi()
    with pytest.raises(ProtocolError, match="PSPRP parameter 1 expects an integer"):
        encoder.psprp(1.5, 1, 1, 1, 1)
    with pytest.raises(ProtocolError, match="CSSFP expects 4"):
        encoder.cspnp(1, 0, 0.25)
