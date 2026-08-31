"""
Search backends for ``content/search``.

Which engine this app talks to is chosen by ``CONTENT_SEARCH_BACKEND``. It
defaults to Meilisearch, so an existing deployment keeps its behaviour without
setting anything.
"""

from __future__ import annotations

from django.conf import settings
from django.utils.module_loading import import_string

from .base import (
    And,
    Equals,
    Exists,
    Filter,
    FilterTerm,
    In,
    IndexDrift,
    Not,
    Or,
    SearchBackend,
    SearchBackendError,
    SearchHit,
    SearchResults,
    as_term,
)

__all__ = [
    "And",
    "Equals",
    "Exists",
    "Filter",
    "FilterTerm",
    "In",
    "IndexDrift",
    "Not",
    "Or",
    "SearchBackend",
    "SearchBackendError",
    "SearchHit",
    "SearchResults",
    "as_term",
    "clear_search_backend",
    "get_search_backend",
]

DEFAULT_BACKEND = "openedx.core.djangoapps.content.search.backends.meilisearch.MeilisearchBackend"

_BACKEND: SearchBackend | None = None


def get_search_backend() -> SearchBackend:
    """
    The configured backend, built once per process.

    Cached because a backend holds an engine client, and building one per call
    would discard connection pooling.
    """
    global _BACKEND  # pylint: disable=global-statement

    if _BACKEND is None:
        backend_path = getattr(settings, "CONTENT_SEARCH_BACKEND", DEFAULT_BACKEND)
        _BACKEND = import_string(backend_path)()
    return _BACKEND


def clear_search_backend() -> None:
    """Drop the cached backend. For tests, and for settings changes at runtime."""
    global _BACKEND  # pylint: disable=global-statement

    _BACKEND = None
