"""Pyntara package."""

import os

from pyntara._version import __version__ as __version__

# typer renders help, docstring markup and tracebacks through rich, and rich
# frames a help panel and rules a traceback with box-drawing characters, which
# the project forbids. TYPER_USE_RICH is the switch of typer itself and is read
# when typer is imported, so the package turns it off here, before anything in
# the program imports typer.
os.environ["TYPER_USE_RICH"] = "0"
