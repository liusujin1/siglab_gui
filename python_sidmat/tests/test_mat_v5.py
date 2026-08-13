"""Boundary and compatibility tests for the internal MATLAB v5 codec."""

from __future__ import annotations

import struct

import numpy as np
import pytest

from python_sidmat.measurement import mat_v5
from python_sidmat.measurement.mat_v5 import MatV5Error, read_mat_v5, write_mat_v5


def _header(endian: str) -> bytes:
    marker = b"IM" if endian == "<" else b"MI"
    return b"MATLAB 5.0 MAT-file".ljust(116, b" ") + b"\0" * 8 + struct.pack(
        f"{endian}H", 0x0100
    ) + marker


def test_nested_struct_unicode_and_numeric_roundtrip(tmp_path):
    path = tmp_path / "roundtrip.mat"
    write_mat_v5(
        path,
        {
            "Title": "中文 test",
            "Version": np.array([2.0]),
            "Record": {
                "Count": np.array([3], dtype=np.int32),
                "Data": np.array([[1.0, 2.0], [3.0, 4.0]]),
                "Empty": {},
            },
        },
    )
    loaded = read_mat_v5(path)
    assert loaded["Title"] == "中文 test"
    assert float(loaded["Version"].flat[0]) == 2.0
    assert int(loaded["Record"]["Count"].flat[0]) == 3
    np.testing.assert_array_equal(loaded["Record"]["Data"], [[1.0, 2.0], [3.0, 4.0]])
    assert loaded["Record"]["Empty"] == {}


def test_big_endian_matrix_is_supported(tmp_path):
    path = tmp_path / "big-endian.mat"
    path.write_bytes(
        _header(">")
        + mat_v5._matrix("Answer", np.array([[42.25]], dtype=np.float64), ">")
    )
    loaded = read_mat_v5(path)
    np.testing.assert_array_equal(loaded["Answer"], [[42.25]])


def test_compressed_and_unsupported_values_fail_explicitly(tmp_path):
    compressed = tmp_path / "compressed.mat"
    compressed.write_bytes(_header("<") + struct.pack("<II", mat_v5.MI_COMPRESSED, 0))
    with pytest.raises(MatV5Error, match="compressed MAT v5 files are not supported"):
        read_mat_v5(compressed)

    with pytest.raises(MatV5Error, match="unsupported MAT numeric dtype"):
        write_mat_v5(tmp_path / "complex.mat", {"Z": np.array([1.0 + 2.0j])})


def test_complex_matrix_flag_is_not_silently_read_as_real(tmp_path):
    path = tmp_path / "complex-flag.mat"
    matrix = bytearray(mat_v5._matrix("Z", np.array([[1.0]]), "<"))
    flags_offset = 8 + 8
    flags = struct.unpack_from("<I", matrix, flags_offset)[0]
    struct.pack_into("<I", matrix, flags_offset, flags | 0x0800)
    path.write_bytes(_header("<") + matrix)
    with pytest.raises(MatV5Error, match="complex MAT matrices are not supported"):
        read_mat_v5(path)
