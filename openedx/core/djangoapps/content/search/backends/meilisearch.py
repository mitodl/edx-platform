"""
Meilisearch implementation of the content/search backend interface.

This is the behaviour ``api.py`` has always had, moved behind the interface. It
stays the default, so an existing deployment is unaffected by the interface
existing.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Callable

from django.conf import settings
from meilisearch import Client as MeilisearchClient
from meilisearch.errors import MeilisearchApiError, MeilisearchError
from meilisearch.models.task import TaskInfo

from ..index_config import (
    INDEX_DISTINCT_ATTRIBUTE,
    INDEX_FILTERABLE_ATTRIBUTES,
    INDEX_PRIMARY_KEY,
    INDEX_RANKING_RULES,
    INDEX_SEARCHABLE_ATTRIBUTES,
    INDEX_SORTABLE_ATTRIBUTES,
)
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

log = logging.getLogger(__name__)

# Meilisearch will not return more than 1000 hits in one response.
MAX_HITS_PER_REQUEST = 1000


def quote(value: Any) -> str:
    """Render a filter value in Meilisearch syntax."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    # Meilisearch takes double-quoted strings and has no escape for an embedded
    # double quote, so drop them rather than produce an unparseable filter.
    return '"' + str(value).replace('"', "") + '"'


def render_filter(term: FilterTerm | None) -> str:
    """Render a structured filter into a Meilisearch filter expression."""
    if term is None:
        return ""

    if isinstance(term, Equals):
        return f"{term.field} = {quote(term.value)}"

    if isinstance(term, In):
        if not term.values:
            # An empty IN must match nothing, not everything.
            return f"{term.field} IN []"
        values = ", ".join(quote(value) for value in term.values)
        return f"{term.field} IN [{values}]"

    if isinstance(term, Exists):
        return f"{term.field} IS NOT NULL"

    if isinstance(term, Not):
        inner = render_filter(term.term)
        return f"NOT ({inner})" if inner else ""

    if isinstance(term, (Or, And)):
        joiner = " OR " if isinstance(term, Or) else " AND "
        rendered = [render_filter(sub) for sub in term.terms]
        rendered = [expression for expression in rendered if expression]
        if not rendered:
            return ""
        if len(rendered) == 1:
            return rendered[0]
        return joiner.join(f"({expression})" for expression in rendered)

    raise SearchBackendError(f"Unknown filter term: {term!r}")


class MeilisearchBackend(SearchBackend):
    """Studio content search on Meilisearch."""

    max_hits_per_request = MAX_HITS_PER_REQUEST

    def __init__(self) -> None:
        self._client: MeilisearchClient | None = None
        self._api_key_uid: str | None = None

    @property
    def client(self) -> MeilisearchClient:
        """The Meilisearch client, created and health-checked on first use."""
        if self._client is not None:
            return self._client

        client = MeilisearchClient(settings.MEILISEARCH_URL, settings.MEILISEARCH_API_KEY)
        try:
            client.health()
        except MeilisearchError as err:
            raise SearchBackendError("Unable to connect to Meilisearch") from err
        self._client = client
        return self._client

    def check_connection(self) -> None:
        try:
            self.client.health()
        except MeilisearchError as err:
            raise SearchBackendError("Unable to connect to Meilisearch") from err

    def wait_for_task(self, info: TaskInfo) -> None:
        """
        Block until a Meilisearch task finishes.

        Meilisearch processes tasks in submission order, so waiting on the last
        of a batch is enough. Only safe from Celery tasks and management
        commands, never from a request.

        An initial 20ms wait is deliberate: a task is almost never done in under
        10ms, and this avoids an extra round trip in the common case.
        """
        sleep_delay = 0.020
        time.sleep(sleep_delay)
        current_status = self.client.get_task(info.task_uid)
        while current_status.status in ("enqueued", "processing"):
            time.sleep(sleep_delay)
            sleep_delay = min(sleep_delay * 1.5, 2.0)
            current_status = self.client.get_task(info.task_uid)
        if current_status.status != "succeeded":
            try:
                err_reason = current_status.error["message"]
            except (TypeError, KeyError):
                err_reason = "Unknown error"
            raise SearchBackendError(err_reason)

    # --- index lifecycle ----------------------------------------------------

    def index_exists(self, index_name: str) -> bool:
        try:
            self.client.get_index(index_name)
        except MeilisearchApiError as err:
            # Only the API error class carries a `code`.
            if err.code == "index_not_found":
                return False
            raise SearchBackendError(f"Could not check for index '{index_name}'") from err
        except MeilisearchError as err:
            raise SearchBackendError(f"Could not check for index '{index_name}'") from err
        return True

    def create_index(self, index_name: str) -> None:
        try:
            self.wait_for_task(self.client.create_index(index_name, {"primaryKey": INDEX_PRIMARY_KEY}))
        except MeilisearchError as err:
            raise SearchBackendError(f"Could not create index '{index_name}'") from err

    def delete_index(self, index_name: str) -> None:
        try:
            self.wait_for_task(self.client.delete_index(index_name))
        except MeilisearchError as err:
            raise SearchBackendError(f"Could not delete index '{index_name}'") from err

    def swap_indexes(self, index_name: str, other_index_name: str) -> None:
        """
        Swap the two indexes' contents, then drop the temporary one.

        The completion poll is not decoration: an API key restricted to an index
        prefix cannot read the swap task's status, so the change is detected by
        watching ``created_at`` instead.
        https://github.com/meilisearch/meilisearch/issues/4103
        """
        try:
            if not self.index_exists(index_name):
                # The target has to exist before anything can be swapped into it.
                self.wait_for_task(self.client.create_index(index_name))

            previous_created_at = self.client.get_index(other_index_name).created_at
            self.client.swap_indexes([{"indexes": [other_index_name, index_name]}])
            while self.client.get_index(index_name).created_at != previous_created_at:
                time.sleep(1)

            self.wait_for_task(self.client.delete_index(other_index_name))
        except MeilisearchError as err:
            raise SearchBackendError(f"Could not swap '{other_index_name}' into '{index_name}'") from err

    def index_is_empty(self, index_name: str) -> bool:
        try:
            return self.client.get_index(index_name).get_stats().number_of_documents == 0
        except MeilisearchError as err:
            raise SearchBackendError(f"Could not read stats for '{index_name}'") from err

    def apply_index_settings(
        self,
        index_name: str,
        *,
        wait: bool = True,
        status_cb: Callable[[str], None] | None = None,
    ) -> None:
        """
        Apply this app's settings to the index.

        ``wait=False`` sends them fire-and-forget, which is fine for an empty
        temporary index about to be populated on the same task queue. ``True``
        confirms each one, which is what reconciling a live index needs.
        """
        if status_cb is None:
            status_cb = log.info

        index = self.client.index(index_name)
        settings_updates = (
            ("distinct attribute", index.update_distinct_attribute, INDEX_DISTINCT_ATTRIBUTE),
            ("filterable attributes", index.update_filterable_attributes, INDEX_FILTERABLE_ATTRIBUTES),
            ("searchable attributes", index.update_searchable_attributes, INDEX_SEARCHABLE_ATTRIBUTES),
            ("sortable attributes", index.update_sortable_attributes, INDEX_SORTABLE_ATTRIBUTES),
            ("ranking rules", index.update_ranking_rules, INDEX_RANKING_RULES),
        )

        for label, update_method, value in settings_updates:
            status_cb(f"Applying {label} to '{index_name}'...")
            if wait:
                self.wait_for_task(update_method(value))
            else:
                update_method(value)

        status_cb(f"All settings applied to '{index_name}'.")

    def detect_index_drift(self, index_name: str) -> IndexDrift:
        if not self.index_exists(index_name):
            return IndexDrift(exists=False)

        index = self.client.get_index(index_name)
        index_settings = index.get_settings()

        def compare(key, expected):
            actual = index_settings.get(key, [] if isinstance(expected, list) else None)
            if isinstance(expected, list):
                # Order is meaningful for ranking rules and for nothing else.
                if key == "rankingRules":
                    return list(actual) == list(expected)
                return set(actual) == set(expected)
            return actual == expected

        return IndexDrift(
            exists=True,
            is_empty=index.get_stats().number_of_documents == 0,
            primary_key_correct=index.primary_key == INDEX_PRIMARY_KEY,
            distinct_attribute_match=compare("distinctAttribute", INDEX_DISTINCT_ATTRIBUTE),
            filterable_attributes_match=compare("filterableAttributes", INDEX_FILTERABLE_ATTRIBUTES),
            searchable_attributes_match=compare("searchableAttributes", INDEX_SEARCHABLE_ATTRIBUTES),
            sortable_attributes_match=compare("sortableAttributes", INDEX_SORTABLE_ATTRIBUTES),
            ranking_rules_match=compare("rankingRules", INDEX_RANKING_RULES),
        )

    # --- documents ----------------------------------------------------------

    def upsert_documents(self, index_name: str, documents: list[dict], *, wait: bool = True) -> None:
        """Meilisearch's update_documents merges, leaving absent fields alone."""
        if not documents:
            return
        try:
            task = self.client.index(index_name).update_documents(documents)
            if wait:
                self.wait_for_task(task)
        except MeilisearchError as err:
            raise SearchBackendError(f"Could not index documents into '{index_name}'") from err

    def add_documents(self, index_name: str, documents: list[dict], *, wait: bool = True) -> None:
        """Meilisearch's add_documents replaces the whole document."""
        if not documents:
            return
        try:
            task = self.client.index(index_name).add_documents(documents)
            if wait:
                self.wait_for_task(task)
        except MeilisearchError as err:
            raise SearchBackendError(f"Could not index documents into '{index_name}'") from err

    def get_document(self, index_name: str, document_id: str) -> dict:
        try:
            return self.client.index(index_name).get_document(document_id)
        except MeilisearchApiError as err:
            raise SearchBackendError(f"Document '{document_id}' not found in '{index_name}'") from err
        except MeilisearchError as err:
            raise SearchBackendError(f"Could not fetch document '{document_id}'") from err

    def delete_document(self, index_name: str, document_id: str) -> None:
        try:
            self.wait_for_task(self.client.index(index_name).delete_document(document_id))
        except MeilisearchApiError as err:
            # Deleting an absent document is not an error.
            if err.code == "document_not_found":
                return
            raise SearchBackendError(f"Could not delete document '{document_id}'") from err
        except MeilisearchError as err:
            raise SearchBackendError(f"Could not delete document '{document_id}'") from err

    def delete_documents_by_filter(self, index_name: str, filter_query: Filter) -> None:
        rendered = render_filter(as_term(filter_query))
        if not rendered:
            # Never let an empty filter turn into "delete everything".
            return
        try:
            self.wait_for_task(self.client.index(index_name).delete_documents(filter=rendered))
        except MeilisearchError as err:
            raise SearchBackendError(f"Could not delete documents from '{index_name}'") from err

    # --- search -------------------------------------------------------------

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
        if limit > MAX_HITS_PER_REQUEST:
            raise SearchBackendError(
                f"Meilisearch returns at most {MAX_HITS_PER_REQUEST} hits per request; "
                f"use iter_documents() to page through more than that."
            )

        parameters: dict[str, Any] = {"limit": limit, "offset": offset}

        rendered = render_filter(as_term(search_filter))
        if rendered:
            parameters["filter"] = rendered
        if facets:
            parameters["facets"] = facets
        if attributes_to_retrieve:
            parameters["attributesToRetrieve"] = attributes_to_retrieve

        try:
            response = self.client.index(index_name).search(query, parameters)
        except MeilisearchError as err:
            raise SearchBackendError(f"Search against '{index_name}' failed") from err

        return SearchResults(
            hits=[
                SearchHit(document=hit, highlights=hit.get("_formatted", {}))
                for hit in response.get("hits", [])
            ],
            total_hits=response.get("estimatedTotalHits", 0),
            facet_distribution=response.get("facetDistribution", {}),
        )

    # --- auth ---------------------------------------------------------------

    @property
    def api_key_uid(self) -> str:
        """The UID of the API key in use, needed to mint tenant tokens."""
        if self._api_key_uid is None:
            self._api_key_uid = self.client.get_key(settings.MEILISEARCH_API_KEY).uid
        return self._api_key_uid

    def generate_user_token(self, index_name: str, *, search_filter: str, expires_at: datetime) -> str:
        """
        Mint a Meilisearch tenant token.

        Despite appearances this makes no API call - it just signs a JWT - so it
        is cheap enough to do per request.
        """
        search_rules: dict[str, Any] = {index_name: {"filter": search_filter} if search_filter else {}}
        return self.client.generate_tenant_token(
            api_key_uid=self.api_key_uid,
            search_rules=search_rules,
            expires_at=expires_at,
        )

    def access_filter(self, orgs: list[str], access_ids: list[int]) -> str:
        """
        Build the permission filter embedded in a tenant token.

        Rendered to a string because it has to travel inside the token itself
        rather than being passed as a normal filter argument.
        """
        return render_filter(Or([In("org", orgs), In("access_id", access_ids)]))
