"""Scaffold-level tests for package importability."""

from importlib import import_module


def test_package_importable() -> None:
    """The top-level package can be imported from the source layout."""
    module = import_module("Pyntara")
    assert module is not None
