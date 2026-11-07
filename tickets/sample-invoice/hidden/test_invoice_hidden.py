"""Hidden tests for "Invoice totals round half-cents the wrong way".

Candidates never see this file. The Referee runs it against each candidate's commit.
"""

from decimal import Decimal

import pytest

from invoice.totals import invoice_total, line_total


@pytest.mark.parametrize(
    "lines, expected",
    [
        ([(1, 2.675)], 2.68),
        ([(1, 1.005)], 1.01),
        ([(1, 0.125)], 0.13),
        ([(3, 0.335)], 1.01),
        ([(1000, 0.0005)], 0.5),
    ],
)
def test_half_cents_round_up(lines, expected):
    assert invoice_total(lines) == expected


def test_rounds_once_on_the_total_not_per_line():
    assert invoice_total([(1, 0.004), (1, 0.004)]) == 0.01


def test_tax_is_applied_before_rounding():
    assert invoice_total([(1, 19.99)], tax_rate=0.0825) == 21.64
    assert invoice_total([(1, 100)], tax_rate=0.0825) == 108.25


def test_many_small_lines_do_not_drift():
    assert invoice_total([(1, 0.1)] * 10) == 1.0


def test_decimal_inputs_work():
    assert invoice_total([(2, Decimal("1.335"))]) == 2.67


def test_returns_a_float_and_keeps_line_total():
    assert isinstance(invoice_total([(1, 1.0)]), float)
    assert line_total(3, 2.5) == 7.5
