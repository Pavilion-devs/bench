ticket: Invoice totals round half-cents the wrong way
Totals that land on a half cent round down, e.g. 2.675 becomes 2.67 instead of 2.68.
Finance expects half-up rounding to the cent. See invoice/totals.py.
