ticket: naturalsize shows "1000.0 kB" instead of "1.0 MB" just below a unit boundary
Sizes a hair under the next unit come out wrong. naturalsize(999999) gives "1000.0 kB" when it should be "1.0 MB". The same happens for GB and TB, in binary mode (1024**2 - 1 should be "1.0 MiB") and in gnu mode ("1.0M"). Rounding to one decimal should carry into the next unit. See src/humanize/filesize.py.
