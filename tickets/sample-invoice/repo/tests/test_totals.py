from invoice.totals import invoice_total, line_total


def test_line_total():
    assert line_total(3, 2.5) == 7.5


def test_invoice_total_without_tax():
    assert invoice_total([(1, 10.0), (2, 2.5)]) == 15.0
