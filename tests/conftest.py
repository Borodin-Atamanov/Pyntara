"""Shared pytest configuration.

Journal forwarding must never reach the real system journal during unit
tests, so PYNTARA_JOURNAL_IDENTIFIER is set to an empty value here, before
any test module imports the application. logger reads the variable lazily,
so an empty value disables systemd-cat for the whole test run. The journal
integration tests in test_logger.py override the variable locally with
their own identifiers.
"""

import os

os.environ["PYNTARA_JOURNAL_IDENTIFIER"] = ""
