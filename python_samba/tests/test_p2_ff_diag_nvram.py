"""P2 feedforward / diagnostics / NVRAM tests on mock transport."""

from __future__ import annotations

import pytest

from python_samba.protocol.commands import CommandEncoder, FilterStage
from python_samba.services.session import open_mock
from python_samba.ui.page_specs import PAGE_SPECS


def test_ff_status_and_filter_roundtrip():
    with open_mock(readonly=False) as session:
        status = session.get_ff_status()
        assert status
        inputs = session.get_ff_inputs()
        assert inputs
        stage = session.get_ff_filter(0, 0)
        assert stage.filter_type == 3
        new = FilterStage(0, 0, 1, (0.25, 0.0, 1.0, 0.0, 0.0))
        session.set_ff_filter(new)
        got = session.get_ff_filter(0, 0)
        assert got.filter_type == 1
        assert got.params[0] == pytest.approx(0.25)


def test_ff_filter_wire_address_has_axis_source_and_stage():
    encoder = CommandEncoder()

    assert b"FGPFS 0 2 3" in encoder.fgpfs(0, 2, 3)
    assert b"FGPFS 5 0 6" in encoder.fgpfs(5, 0, 6)
    with pytest.raises(Exception, match="FGPFS stage out of range"):
        encoder.fgpfs(0, 0, 12)


def test_diagnostics_noise_roundtrip():
    with open_mock(readonly=False) as session:
        assert session.get_noise_type() == 0
        session.set_noise_type(2)
        assert session.get_noise_type() == 2
        session.set_noise_gain(0.5)
        assert session.get_noise_gain() == pytest.approx(0.5)
        session.set_noise_inject_point(1, 3)
        assert session.get_noise_inject_point() == ["1", "3"]
        assert session.get_switch_status()
        assert session.get_output_limit() == 100
        session.set_output_limit(80)
        assert session.get_output_limit() == 80


def test_nvram_save_restore_clear():
    with open_mock(readonly=False) as session:
        session.set_noise_type(3)
        session.set_output_limit(70)
        session.set_dither_frequency(35)
        session.nvram_save()
        session.set_noise_type(0)
        session.set_output_limit(100)
        session.set_dither_frequency(34)
        session.nvram_restore()
        assert session.get_noise_type() == 3
        assert session.get_output_limit() == 70
        assert session.get_dither_frequency() == pytest.approx(35)
        session.nvram_clear()
        # clear only wipes stored blob; live values remain until next restore of empty
        session.set_noise_type(1)
        session.nvram_restore()  # empty blob -> no change
        assert session.get_noise_type() == 1


def test_page_specs_cover_samba_tree():
    ids = {p.page_id for p in PAGE_SPECS}
    for required in (
        "connect",
        "status",
        "velocity_tuning",
        "position_tuning",
        "ff_tuning",
        "diagnostics",
        "nvram",
        "pneumatic_tuning",
        "raw",
    ):
        assert required in ids
    # At least the original top-level groups
    groups = {p.group for p in PAGE_SPECS}
    for g in ("Connection", "Velocity", "Position", "Pneumatic", "Feedforward", "Setup"):
        assert g in groups


def test_cli_ff_diag_mock():
    from python_samba.cli import main

    assert main(["ff", "--backend", "mock"]) == 0
    assert main(["diag", "--backend", "mock"]) == 0
    assert main(["nvram", "save", "--backend", "mock", "--write"]) == 0
