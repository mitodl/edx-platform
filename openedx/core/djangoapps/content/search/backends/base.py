"""
The search engine interface used by ``content/search``.

This is deliberately not a general-purpose search abstraction. It covers only
the operations this app performs, is private to this app, and is not offered to
any other. See ``docs/decisions/0002-pluggable-search-backend.rst`` for why that
boundary is drawn where it is: ``edx-search``'s django-haystack layer is the
cautionary example, and it failed by trying to span engines generically.

Implementations live alongside this module and are selected by
``CONTENT_SEARCH_BACKEND``.
"""

from __future__ import annotations

import abc
from typing import Any, Callable, Iterator

from attrs import define


# --- filters ----------------------------------------------------------------
#
# Filters are structured rather than engine-syntax strings. They were strings
# before, which leaked Meilisearch's syntax across app boundaries -
# cms/djangoapps/modulestore_migrator, for one, builds
# `breadcrumbs.usage_key IN [...]` by hand. Each backend renders these into its
# own syntax, so callers do not have to know which engine is running.
#
# This vocabulary covers everything the platform actually builds. It is
# deliberately not a general query language; add a term only when something
# needs it.


@define
class Equals:
    """``field`` exactly equals ``value``."""

    field: str
    value: Any


@define
class In:
    """``field`` equals any of ``values``. An empty list matches nothing."""

    field: str
    values: list[Any]


@define
class Exists:
    """
    ``field`` has a value.

    Engines differ sharply here - Meilisearch has an IS NOT NULL operator and
    Typesense has none - so this is expressed as intent and left to the backend.
    """

    field: str


@define
class Not:
    """Negates ``term``."""

    term: "FilterTerm"


@define
class Or:
    """Any of ``terms`` matches."""

    terms: list["FilterTerm"]


@define
class And:
    """All of ``terms`` match."""

    terms: list["FilterTerm"]


FilterTerm = Equals | In | Exists | Not | Or | And

# A bare list is an implicit And, which is how callers already think about it.
Filter = FilterTerm | list[FilterTerm]


def as_term(search_filter: Filter | None) -> FilterTerm | None:
    """Normalise a filter argument to a single term, or None for 'no filter'."""
    if search_filter is None:
        return None
    if isinstance(search_filter, list):
        if not search_filter:
            return None
        if len(search_filter) == 1:
            return search_filter[0]
        return And(search_filter)
    return search_filter


@define
class SearchHit:
    """One document from a search response, plus whatever the engine annotated it with."""

    document: dict[str, Any]
    highlights: dict[str, Any]


@define
class SearchResults:
    """
    Engine-independent search response.

    ``facet_distribution`` maps a field name to ``{value: count}``. ``total_hits``
    is the number of documents matching the filter, which for some engines is an
    estimate rather than an exact count.
    """

    hits: list[SearchHit]
    total_hits: int
    facet_distribution: dict[str, dict[str, int]]


@define
class IndexDrift:
    """
    The state of a live index compared with what this app expects.

    ``None`` on a setting means the backend does not track it: engines disagree
    about what is even a stored index setting. Meilisearch stores all five;
    Typesense fixes fields at creation and treats ranking and distinctness as
    per-request parameters, so those cannot drift there.
    """

    exists: bool
    is_empty: bool | None = None  # None if the index does not exist
    primary_key_correct: bool | None = None  # None if the index does not exist
    distinct_attribute_match: bool | None = None
    filterable_attributes_match: bool | None = None
    searchable_attributes_match: bool | None = None
    sortable_attributes_match: bool | None = None
    ranking_rules_match: bool | None = None

    @property
    def is_settings_drifted(self) -> bool:
        """True if a tracked setting is explicitly wrong. Untracked (None) settings are ignored."""
        return any(
            match is False
            for match in (
                self.distinct_attribute_match,
                self.filterable_attributes_match,
                self.searchable_attributes_match,
                self.sortable_attributes_match,
                self.ranking_rules_match,
            )
        )


class SearchBackendError(Exception):
    """
    Raised for any engine-level failure.

    ``tasks.py`` retries on this rather than on an engine's own exception type,
    which is what previously tied the Celery layer to Meilisearch.
    """


class SearchBackend(abc.ABC):
    """
    What ``content/search`` needs a search engine to do.

    Index names are passed in rather than held, because rebuilds run against a
    temporary index while the live one keeps serving.
    """

    #: The most hits an engine will return from one search. Engines differ
    #: sharply - Meilisearch allows 1000, Typesense 250 - so anything paging
    #: through results must take its page size from here rather than guess.
    max_hits_per_request: int = 250

    @abc.abstractmethod
    def check_connection(self) -> None:
        """Raise SearchBackendError if the engine is unreachable."""

    # --- index lifecycle ----------------------------------------------------

    @abc.abstractmethod
    def index_exists(self, index_name: str) -> bool:
        """Whether the index exists."""

    @abc.abstractmethod
    def create_index(self, index_name: str) -> None:
        """Create the index, configured for the documents this app stores."""

    @abc.abstractmethod
    def delete_index(self, index_name: str) -> None:
        """Delete the index and everything in it."""

    @abc.abstractmethod
    def swap_indexes(self, index_name: str, other_index_name: str) -> None:
        """
        Atomically make ``other_index_name``'s contents serve under
        ``index_name``, so a rebuild can be published without downtime.
        """

    @abc.abstractmethod
    def index_is_empty(self, index_name: str) -> bool:
        """Whether the index holds zero documents."""

    @abc.abstractmethod
    def apply_index_settings(
        self,
        index_name: str,
        *,
        wait: bool = True,
        status_cb: Callable[[str], None] | None = None,
    ) -> None:
        """
        Apply this app's index configuration.

        ``wait=False`` allows fire-and-forget for an empty temporary index about
        to be populated. Engines with synchronous writes ignore it.
        """

    @abc.abstractmethod
    def detect_index_drift(self, index_name: str) -> IndexDrift:
        """Compare the live index against what this app expects."""

    # --- documents ----------------------------------------------------------

    @abc.abstractmethod
    def upsert_documents(self, index_name: str, documents: list[dict], *, wait: bool = True) -> None:
        """
        Merge documents into the index, matched on their ``id``.

        Fields absent from a given document are LEFT ALONE. This is what lets a
        caller push a partial document - a tags-only or collections-only update -
        without having to rebuild the whole thing.
        """

    @abc.abstractmethod
    def add_documents(self, index_name: str, documents: list[dict], *, wait: bool = True) -> None:
        """
        Replace documents wholesale, matched on their ``id``.

        Fields absent from a given document are REMOVED. Use this when the
        caller has built the complete document, as a full reindex does.
        """

    @abc.abstractmethod
    def get_document(self, index_name: str, document_id: str) -> dict:
        """Fetch one document. Raises SearchBackendError if it is not there."""

    @abc.abstractmethod
    def delete_document(self, index_name: str, document_id: str) -> None:
        """Delete one document. Deleting an absent document is not an error."""

    @abc.abstractmethod
    def delete_documents_by_filter(self, index_name: str, filter_query: Filter) -> None:
        """Delete every document matching the filter."""

    # --- search -------------------------------------------------------------

    @abc.abstractmethod
    def search(
        self,
        index_name: str,
        query: str = "",
        *,
        search_filter: Filter | None = None,
        facets: list[str] | None = None,
        limit: int = 20,
        offset: int = 0,
        attributes_to_retrieve: list[str] | None = None,
    ) -> SearchResults:
        """Run a search. An empty ``query`` matches everything."""

    def iter_documents(
        self,
        index_name: str,
        *,
        search_filter: Filter | None = None,
        attributes_to_retrieve: list[str] | None = None,
        batch_size: int | None = None,
    ) -> Iterator[dict]:
        """
        Yield every document matching the filter, a page at a time.

        Implemented once here in terms of ``search``, since paging is the same
        shape for every engine; override only if an engine offers something
        better. The page size defaults to the engine's own maximum, and is
        capped by it - asking for more than an engine allows is an error, not a
        thing to discover at runtime.
        """
        batch_size = min(batch_size or self.max_hits_per_request, self.max_hits_per_request)
        offset = 0
        while True:
            results = self.search(
                index_name,
                search_filter=search_filter,
                limit=batch_size,
                offset=offset,
                attributes_to_retrieve=attributes_to_retrieve,
            )
            if not results.hits:
                return
            for hit in results.hits:
                yield hit.document
            if len(results.hits) < batch_size:
                return
            offset += batch_size

    # --- auth ---------------------------------------------------------------

    @abc.abstractmethod
    def generate_user_token(self, index_name: str, *, search_filter: str, expires_at) -> str:
        """
        Mint a restricted, time-limited credential that can only search
        ``index_name``, and only documents matching ``search_filter``.

        This is what lets the browser query the engine directly instead of
        routing every search through Django, so it must be enforced by the
        engine rather than by the caller.
        """
