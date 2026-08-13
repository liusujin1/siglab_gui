"""Minimal uncompressed MATLAB v5 reader/writer for SIDMAT file schemas.

Only the Level-5 types required by ``.sidimat19x`` and ``.idefigure`` are
implemented: numeric matrices, UTF-16 character arrays, and scalar nested
structures.  Unsupported or compressed payloads fail explicitly.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import io
from pathlib import Path
import struct
from typing import Any, BinaryIO

import numpy as np


MI_INT8 = 1
MI_UINT8 = 2
MI_INT16 = 3
MI_UINT16 = 4
MI_INT32 = 5
MI_UINT32 = 6
MI_SINGLE = 7
MI_DOUBLE = 9
MI_INT64 = 12
MI_UINT64 = 13
MI_MATRIX = 14
MI_COMPRESSED = 15
MI_UTF8 = 16
MI_UTF16 = 17
MI_UTF32 = 18

MX_STRUCT = 2
MX_CHAR = 4
MX_DOUBLE = 6
MX_SINGLE = 7
MX_INT8 = 8
MX_UINT8 = 9
MX_INT16 = 10
MX_UINT16 = 11
MX_INT32 = 12
MX_UINT32 = 13
MX_INT64 = 14
MX_UINT64 = 15

_NUMERIC_WRITERS = {
    np.dtype("float64"): (MX_DOUBLE, MI_DOUBLE, "f8"),
    np.dtype("float32"): (MX_SINGLE, MI_SINGLE, "f4"),
    np.dtype("int8"): (MX_INT8, MI_INT8, "i1"),
    np.dtype("uint8"): (MX_UINT8, MI_UINT8, "u1"),
    np.dtype("int16"): (MX_INT16, MI_INT16, "i2"),
    np.dtype("uint16"): (MX_UINT16, MI_UINT16, "u2"),
    np.dtype("int32"): (MX_INT32, MI_INT32, "i4"),
    np.dtype("uint32"): (MX_UINT32, MI_UINT32, "u4"),
    np.dtype("int64"): (MX_INT64, MI_INT64, "i8"),
    np.dtype("uint64"): (MX_UINT64, MI_UINT64, "u8"),
}

_NUMERIC_READERS = {
    MI_INT8: "i1",
    MI_UINT8: "u1",
    MI_INT16: "i2",
    MI_UINT16: "u2",
    MI_INT32: "i4",
    MI_UINT32: "u4",
    MI_SINGLE: "f4",
    MI_DOUBLE: "f8",
    MI_INT64: "i8",
    MI_UINT64: "u8",
}


class MatV5Error(ValueError):
    """Raised when a MAT v5 file uses a malformed or unsupported feature."""


def _pad8(length: int) -> int:
    return (-length) % 8


def _element(data_type: int, payload: bytes, endian: str = "<") -> bytes:
    return (
        struct.pack(f"{endian}II", int(data_type), len(payload))
        + payload
        + b"\0" * _pad8(len(payload))
    )


def _dimensions(array: np.ndarray) -> tuple[int, ...]:
    if array.ndim == 0:
        return (1, 1)
    if array.ndim == 1:
        return (1, int(array.shape[0]))
    return tuple(int(value) for value in array.shape)


def _matrix(name: str, value: Any, endian: str = "<") -> bytes:
    encoded_name = str(name).encode("ascii")
    if isinstance(value, Mapping):
        matrix_class = MX_STRUCT
        dimensions = (1, 1)
        fields = [(str(field), item) for field, item in value.items()]
        if any(not field.isascii() for field, _ in fields):
            raise MatV5Error("MAT struct field names must be ASCII")
        field_width = max([1, *(len(field.encode("ascii")) + 1 for field, _ in fields)])
        if field_width > 64:
            raise MatV5Error("MAT struct field name exceeds 63 bytes")
        field_names = b"".join(
            field.encode("ascii").ljust(field_width, b"\0") for field, _ in fields
        )
        body = _element(MI_INT32, struct.pack(f"{endian}i", field_width), endian)
        body += _element(MI_INT8, field_names, endian)
        body += b"".join(_matrix("", item, endian) for _, item in fields)
    elif isinstance(value, str):
        matrix_class = MX_CHAR
        codec = "utf-16-le" if endian == "<" else "utf-16-be"
        encoded = value.encode(codec)
        dimensions = (1, len(encoded) // 2) if encoded else (0, 0)
        body = _element(MI_UTF16, encoded, endian)
    else:
        array = np.asarray(value)
        if array.dtype.kind in "US":
            text = "".join(str(item) for item in array.reshape(-1))
            return _matrix(name, text, endian)
        if array.dtype.kind == "b":
            array = array.astype(np.uint8)
        if array.dtype.kind not in "fiu":
            raise MatV5Error(f"unsupported MAT numeric dtype: {array.dtype}")
        native = np.dtype(array.dtype.name)
        writer = _NUMERIC_WRITERS.get(native)
        if writer is None:
            raise MatV5Error(f"unsupported MAT numeric dtype: {array.dtype}")
        matrix_class, data_type, dtype_code = writer
        dimensions = _dimensions(array)
        encoded_dtype = np.dtype(endian + dtype_code)
        numeric = np.asarray(array, dtype=encoded_dtype).tobytes(order="F")
        body = _element(data_type, numeric, endian)

    flags = struct.pack(f"{endian}II", matrix_class, 0)
    dims = np.asarray(dimensions, dtype=np.dtype(endian + "i4")).tobytes()
    payload = _element(MI_UINT32, flags, endian)
    payload += _element(MI_INT32, dims, endian)
    payload += _element(MI_INT8, encoded_name, endian)
    payload += body
    return _element(MI_MATRIX, payload, endian)


def write_mat_v5(path: str | Path, variables: Mapping[str, Any]) -> Path:
    """Write variables as an uncompressed, little-endian MATLAB v5 file."""

    output = Path(path)
    description = (
        "MATLAB 5.0 MAT-file, Platform: python, Created on: "
        + datetime.now().strftime("%a %b %d %H:%M:%S %Y")
    ).encode("ascii")[:116]
    header = description.ljust(116, b" ") + b"\0" * 8 + struct.pack("<H", 0x0100) + b"IM"
    with output.open("wb") as stream:
        stream.write(header)
        for name, value in variables.items():
            stream.write(_matrix(str(name), value, "<"))
    return output


def _read_exact(stream: BinaryIO, size: int, context: str) -> bytes:
    data = stream.read(size)
    if len(data) != size:
        raise MatV5Error(f"truncated MAT v5 {context}")
    return data


def _read_element(stream: BinaryIO, endian: str) -> tuple[int, bytes] | None:
    first = stream.read(4)
    if not first:
        return None
    if len(first) != 4:
        raise MatV5Error("truncated MAT v5 element tag")
    small_type, small_size = struct.unpack(f"{endian}HH", first)
    small_types = set(_NUMERIC_READERS) | {MI_UTF8, MI_UTF16, MI_UTF32}
    if small_type in small_types and 0 < small_size <= 4:
        payload = _read_exact(stream, 4, "small element")[:small_size]
        return small_type, payload
    data_type = struct.unpack(f"{endian}I", first)[0]
    size = struct.unpack(f"{endian}I", _read_exact(stream, 4, "element size"))[0]
    payload = _read_exact(stream, size, "element payload")
    padding = _pad8(size)
    if padding:
        _read_exact(stream, padding, "element padding")
    return data_type, payload


def _required_element(stream: BinaryIO, endian: str, context: str) -> tuple[int, bytes]:
    element = _read_element(stream, endian)
    if element is None:
        raise MatV5Error(f"missing MAT v5 {context}")
    return element


def _decode_name(data_type: int, payload: bytes) -> str:
    if data_type not in {MI_INT8, MI_UINT8, MI_UTF8}:
        raise MatV5Error("MAT matrix name is not an 8-bit string")
    return payload.decode("utf-8", errors="strict")


def _parse_numeric(data_type: int, payload: bytes, dimensions: tuple[int, ...], endian: str) -> np.ndarray:
    code = _NUMERIC_READERS.get(data_type)
    if code is None:
        raise MatV5Error(f"unsupported MAT numeric element type: {data_type}")
    dtype = np.dtype(endian + code)
    if len(payload) % dtype.itemsize:
        raise MatV5Error("MAT numeric payload is not aligned to its dtype")
    values = np.frombuffer(payload, dtype=dtype).copy()
    expected = int(np.prod(dimensions, dtype=np.int64)) if dimensions else 0
    if values.size != expected:
        raise MatV5Error(
            f"MAT numeric shape {dimensions} requires {expected} values, found {values.size}"
        )
    return values.reshape(dimensions, order="F")


def _parse_matrix(payload: bytes, endian: str) -> tuple[str, Any]:
    stream = io.BytesIO(payload)
    flags_type, flags = _required_element(stream, endian, "array flags")
    if flags_type != MI_UINT32 or len(flags) < 8:
        raise MatV5Error("invalid MAT array flags")
    flags_word = struct.unpack(f"{endian}I", flags[:4])[0]
    matrix_class = flags_word & 0xFF
    if flags_word & 0x0800:
        raise MatV5Error("complex MAT matrices are not supported")
    if flags_word & 0x0200:
        raise MatV5Error("logical MAT matrices are not supported")
    dims_type, dims_payload = _required_element(stream, endian, "dimensions")
    if dims_type != MI_INT32 or len(dims_payload) % 4:
        raise MatV5Error("invalid MAT dimensions")
    dimensions = tuple(
        int(value)
        for value in np.frombuffer(dims_payload, dtype=np.dtype(endian + "i4"))
    )
    if any(value < 0 for value in dimensions):
        raise MatV5Error("MAT dimensions cannot be negative")
    name_type, name_payload = _required_element(stream, endian, "matrix name")
    name = _decode_name(name_type, name_payload)

    if matrix_class == MX_STRUCT:
        if int(np.prod(dimensions, dtype=np.int64)) not in {0, 1}:
            raise MatV5Error("non-scalar MAT struct arrays are not supported")
        width_type, width_payload = _required_element(stream, endian, "field width")
        if width_type != MI_INT32 or len(width_payload) < 4:
            raise MatV5Error("invalid MAT struct field width")
        width = struct.unpack(f"{endian}i", width_payload[:4])[0]
        if width <= 0:
            raise MatV5Error("MAT struct field width must be positive")
        names_type, names_payload = _required_element(stream, endian, "field names")
        if names_type not in {MI_INT8, MI_UINT8} or len(names_payload) % width:
            raise MatV5Error("invalid MAT struct field names")
        field_names = [
            names_payload[offset : offset + width].split(b"\0", 1)[0].decode("ascii")
            for offset in range(0, len(names_payload), width)
        ]
        result: dict[str, Any] = {}
        for field_name in field_names:
            field_type, field_payload = _required_element(stream, endian, f"field {field_name}")
            if field_type == MI_COMPRESSED:
                raise MatV5Error("compressed MAT v5 fields are not supported")
            if field_type != MI_MATRIX:
                raise MatV5Error(f"MAT struct field {field_name} is not a matrix")
            _, field_value = _parse_matrix(field_payload, endian)
            result[field_name] = field_value
        return name, result

    data_type, data_payload = _required_element(stream, endian, "matrix data")
    if data_type == MI_COMPRESSED:
        raise MatV5Error("compressed MAT v5 matrices are not supported")
    if matrix_class == MX_CHAR:
        if data_type in {MI_UTF16, MI_UINT16}:
            codec = "utf-16-le" if endian == "<" else "utf-16-be"
        elif data_type in {MI_UTF8, MI_UINT8, MI_INT8}:
            codec = "utf-8"
        elif data_type == MI_UTF32:
            codec = "utf-32-le" if endian == "<" else "utf-32-be"
        else:
            raise MatV5Error(f"unsupported MAT character encoding type: {data_type}")
        try:
            return name, data_payload.decode(codec).rstrip("\0")
        except UnicodeDecodeError as exc:
            raise MatV5Error(f"invalid MAT character data: {exc}") from exc
    if matrix_class not in {
        MX_DOUBLE, MX_SINGLE, MX_INT8, MX_UINT8, MX_INT16, MX_UINT16,
        MX_INT32, MX_UINT32, MX_INT64, MX_UINT64,
    }:
        raise MatV5Error(f"unsupported MAT matrix class: {matrix_class}")
    return name, _parse_numeric(data_type, data_payload, dimensions, endian)


def read_mat_v5(path: str | Path) -> dict[str, Any]:
    """Read an uncompressed MATLAB v5 file into arrays, strings and dicts."""

    source = Path(path)
    with source.open("rb") as stream:
        header = _read_exact(stream, 128, "header")
        marker = header[126:128]
        if marker == b"IM":
            endian = "<"
        elif marker == b"MI":
            endian = ">"
        else:
            raise MatV5Error("invalid MAT v5 endian marker")
        version = struct.unpack(f"{endian}H", header[124:126])[0]
        if version != 0x0100:
            raise MatV5Error(f"unsupported MAT v5 header version: 0x{version:04X}")
        variables: dict[str, Any] = {}
        while True:
            element = _read_element(stream, endian)
            if element is None:
                break
            data_type, payload = element
            if data_type == MI_COMPRESSED:
                raise MatV5Error("compressed MAT v5 files are not supported")
            if data_type != MI_MATRIX:
                raise MatV5Error(f"unsupported top-level MAT element type: {data_type}")
            name, value = _parse_matrix(payload, endian)
            variables[name] = value
        return variables


__all__ = ["MatV5Error", "read_mat_v5", "write_mat_v5"]
