"""Shared fixtures for the content/search tests."""

import pytest

from ..backends import clear_search_backend


@pytest.fixture(autouse=True)
def reset_search_backend():
    """
    Drop the cached backend around every test.

    The backend is built once per process and holds an engine client, so
    without this a test that patches the client would either miss the patch or
    leak a mock into the next test.
    """
    clear_search_backend()
    yield
    clear_search_backend()
