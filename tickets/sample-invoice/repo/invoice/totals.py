"""Invoice totals for the payments service."""


def line_total(quantity, unit_price):
    return quantity * unit_price


def invoice_total(lines, tax_rate=0.0):
    """Sum of line totals plus tax, rounded to cents."""
    subtotal = sum(line_total(q, p) for q, p in lines)
    return round(subtotal * (1 + tax_rate), 2)
