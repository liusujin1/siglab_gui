"""Offline transfer-function compatibility tests."""

from __future__ import annotations

import numpy as np
import pytest

from python_sidmat.measurement.filter_tf import (
    apply_filter_chain,
    filter_response,
    generate_closed_loop,
)


class _Stage:
    def __init__(self, filter_type, params):
        self.filter_type = filter_type
        self.params = tuple(params)


def test_no_filter_is_exact_pass_through():
    freq = np.array([0.0, 1.0, 10.0])
    amplitude = np.array([1 + 2j, 2 - 1j, -1 + 0.5j])
    got = apply_filter_chain(freq, amplitude, [_Stage(0, [0, 0, 0, 0, 0])])
    np.testing.assert_allclose(got, amplitude)


def test_low_pass_response_and_closed_loop_are_finite():
    freq = np.linspace(0.0, 100.0, 32)
    response = filter_response(freq, 1, [10.0, 0.0, 2.0, 0.0, 0.0])
    assert np.all(np.isfinite(response))
    closed = generate_closed_loop(response)
    assert np.all(np.isfinite(closed))


def test_unknown_filter_is_reported():
    with pytest.raises(ValueError, match="unknown filter"):
        filter_response(np.array([1.0]), 999, [0, 0, 0, 0, 0])


def test_limited_pid_and_sample_hold_are_supported():
    freq = np.linspace(1.0, 100.0, 16)
    for filter_type in (23, 24):
        response = filter_response(freq, filter_type, [1.0, 0.1, 0.01, 0.0, 10.0])
        assert np.all(np.isfinite(response))
