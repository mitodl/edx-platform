"""
Typesense implementation of the content/search backend interface.

Typesense writes are synchronous, so there is no task queue to poll and the
``wait`` argument on write methods is ignored. That removes the whole
``_wait_for_meili_task`` layer, and with it a source of request latency.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import math
from datetime import datetime
from typing import Any, Callable

import typesense
from django.conf import settings
from typesense.exceptions import ObjectNotFound, TypesenseClientError

from ..documents import Fields
from .base import (
    And,
    Equals,
    Exists,
    Filter,
    FilterTerm,
    In,
    IndexSettingsDrift,
    Not,
    Or,
    SearchBackend,
    SearchBackendError,
    SearchHit,
    SearchResults,
    as_term,
)
from .typesense_config import (
    IS_NULL_SUFFIX,
    MAX_FACET_VALUES,
    MAX_PER_PAGE,
    NULLABLE_FILTERABLE_FIELDS,
    QUERY_BY_FIELDS,
    QUERY_BY_WEIGHTS,
    collection_schema,
)

log = logging.getLogger(__name__)


def quote(value: Any) -> str:
    """
    Render a filter value.

    Strings are backtick-quoted: Typesense uses backticks to escape values
    containing its filter operators, and offers no escape for a backtick itself,
    so any are dropped.
    https://typesense.org/docs/guide/tips-for-filtering.html

    Numbers and booleans must NOT be quoted. Backticking a value on a numeric
    field is rejected with "Numerical field has an invalid comparator", which
    matters most for ``access_id`` - the field the scoped search key filters on
    to keep one organisation's content out of another's results.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return "`" + str(value).replace("`", "") + "`"


def render_filter(term: FilterTerm | None) -> str:
    """Render a structured filter into Typesense ``filter_by`` syntax."""
    if term is None:
        return ""

    if isinstance(term, Equals):
        return f"{term.field}:={quote(term.value)}"

    if isinstance(term, In):
        if not term.values:
            # An empty IN matches nothing. Typesense rejects `field:=[]`, so
            # express it as a contradiction instead of silently matching all.
            return f"{term.field}:=[] && {term.field}:!=[]"
        values = ",".join(quote(value) for value in term.values)
        return f"{term.field}:=[{values}]"

    if isinstance(term, Exists):
        # Typesense has no IS NULL operator. For fields that documents.py writes
        # as an explicit None we maintain a companion boolean at index time; for
        # anything else, absence is the natural state of an optional field.
        if term.field in NULLABLE_FILTERABLE_FIELDS:
            return f"{term.field}{IS_NULL_SUFFIX}:!=true"
        return f"{term.field}:!=null"

    if isinstance(term, Not):
        inner = render_filter(term.term)
        if isinstance(term.term, Equals):
            return f"{term.term.field}:!={quote(term.term.value)}"
        if isinstance(term.term, In):
            values = ",".join(quote(value) for value in term.term.values)
            return f"{term.term.field}:!=[{values}]"
        raise SearchBackendError(f"Cannot negate filter term: {term.term!r}")

    if isinstance(term, (Or, And)):
        if not term.terms:
            return ""
        joiner = " || " if isinstance(term, Or) else " && "
        rendered = [render_filter(sub) for sub in term.terms]
        rendered = [expression for expression in rendered if expression]
        if not rendered:
            return ""
        if len(rendered) == 1:
            return rendered[0]
        return joiner.join(f"({expression})" for expression in rendered)

    raise SearchBackendError(f"Unknown filter term: {term!r}")


def prepare_document(document: dict) -> dict:
    """
    Adapt a document from ``documents.py`` to what Typesense will accept.

    Typesense has no null: a null-valued field must be absent. For fields the UI
    filters on "has a value", drop the null and record the absence in a
    companion boolean, following the convention edx-search's engine uses.
    """
    prepared = dict(document)
    for field in NULLABLE_FILTERABLE_FIELDS:
        if field in prepared and prepared[field] is None:
            del prepared[field]
            prepared[f"{field}{IS_NULL_SUFFIX}"] = True
        elif field in prepared:
            prepared[f"{field}{IS_NULL_SUFFIX}"] = False
    return prepared


class TypesenseBackend(SearchBackend):
    """Studio content search on Typesense."""

    def __init__(self) -> None:
        self._client: typesense.Client | None = None

    @property
    def client(self) -> typesense.Client:
        """The Typesense client, created on first use."""
        if self._client is None:
            self._client = typesense.Client(
                {
                    "api_key": settings.TYPESENSE_API_KEY,
                    "nodes": settings.TYPESENSE_URLS,
                }
            )
        return self._client

    def check_connection(self) -> None:
        try:
            if not self.client.operations.is_healthy():
                raise SearchBackendError("Typesense reports itself unhealthy")
        except TypesenseClientError as err:
            raise SearchBackendError("Unable to connect to Typesense") from err

    # --- index lifecycle ----------------------------------------------------

    def index_exists(self, index_name: str) -> bool:
        try:
            self.client.collections[index_name].retrieve()
        except ObjectNotFound:
            return False
        except TypesenseClientError as err:
            raise SearchBackendError(f"Could not check for collection '{index_name}'") from err
        return True

    def create_index(self, index_name: str) -> None:
        try:
            self.client.collections.create(collection_schema(index_name))
        except TypesenseClientError as err:
            raise SearchBackendError(f"Could not create collection '{index_name}'") from err

    def delete_index(self, index_name: str) -> None:
        try:
            self.client.collections[index_name].delete()
        except ObjectNotFound:
            return
        except TypesenseClientError as err:
            raise SearchBackendError(f"Could not delete collection '{index_name}'") from err

    def swap_indexes(self, index_name: str, other_index_name: str) -> None:
        """
        Publish ``other_index_name`` under ``index_name`` by repointing an alias.

        Meilisearch swaps two indexes' contents; Typesense aliases mean the
        rebuilt collection keeps its own name and the alias moves to it. The
        collection previously behind the alias is deleted, which - unlike
        Meilisearch's LMDB store - returns the disk immediately.
        """
        try:
            previous = None
            try:
                previous = self.client.aliases[index_name].retrieve()["collection_name"]
            except ObjectNotFound:
                # First publish, or the name is still a real collection.
                if self.index_exists(index_name):
                    previous = index_name

            self.client.aliases.upsert(index_name, {"collection_name": other_index_name})

            if previous and previous != other_index_name:
                self.delete_index(previous)
        except TypesenseClientError as err:
            raise SearchBackendError(
                f"Could not point alias '{index_name}' at '{other_index_name}'"
            ) from err

    def index_is_empty(self, index_name: str) -> bool:
        try:
            collection = self.client.collections[index_name].retrieve()
        except TypesenseClientError as err:
            raise SearchBackendError(f"Could not read collection '{index_name}'") from err
        return collection.get("num_documents", 0) == 0

    def apply_index_settings(
        self,
        index_name: str,
        *,
        wait: bool = True,
        status_cb: Callable[[str], None] | None = None,
    ) -> None:
        """
        No-op: a Typesense collection's fields, facets, sortability and
        stemming are fixed by its schema at creation time, so ``create_index``
        has already applied everything ``_apply_index_settings`` used to send as
        five separate Meilisearch tasks.
        """
        if status_cb is None:
            status_cb = log.info
        status_cb(f"Settings for '{index_name}' were applied when the collection was created.")

    def detect_settings_drift(self, index_name: str) -> IndexSettingsDrift:
        """
        Compare the live collection's fields against the schema this app expects.

        Only field-level drift is meaningful for Typesense: the notions
        Meilisearch tracks separately (distinct attribute, ranking rules) are
        per-request parameters here, not stored index settings, so they cannot
        drift.
        """
        try:
            actual = self.client.collections[index_name].retrieve()
        except TypesenseClientError as err:
            raise SearchBackendError(f"Could not read collection '{index_name}'") from err

        expected_fields = {field["name"] for field in collection_schema(index_name)["fields"]}
        actual_fields = {field["name"] for field in actual.get("fields", [])}

        return IndexSettingsDrift(
            filterable_attributes_match=expected_fields.issubset(actual_fields),
            searchable_attributes_match=expected_fields.issubset(actual_fields),
            sortable_attributes_match=expected_fields.issubset(actual_fields),
        )

    # --- documents ----------------------------------------------------------

    def upsert_documents(self, index_name: str, documents: list[dict], *, wait: bool = True) -> None:
        if not documents:
            return
        prepared = [prepare_document(document) for document in documents]
        try:
            responses = self.client.collections[index_name].documents.import_(
                prepared, {"action": "upsert"}
            )
        except TypesenseClientError as err:
            raise SearchBackendError(f"Could not index documents into '{index_name}'") from err

        # import_ reports per-document success; a partial failure is a 200.
        failures = [response for response in responses if not response.get("success")]
        if failures:
            raise SearchBackendError(
                f"{len(failures)} of {len(prepared)} documents failed to index "
                f"into '{index_name}': {failures[0]}"
            )

    def get_document(self, index_name: str, document_id: str) -> dict:
        try:
            return self.client.collections[index_name].documents[document_id].retrieve()
        except ObjectNotFound as err:
            raise SearchBackendError(f"Document '{document_id}' not found in '{index_name}'") from err
        except TypesenseClientError as err:
            raise SearchBackendError(f"Could not fetch document '{document_id}'") from err

    def delete_document(self, index_name: str, document_id: str) -> None:
        try:
            self.client.collections[index_name].documents[document_id].delete(
                delete_parameters={"ignore_not_found": True},
            )
        except TypesenseClientError as err:
            raise SearchBackendError(f"Could not delete document '{document_id}'") from err

    def delete_documents_by_filter(self, index_name: str, filter_query: Filter) -> None:
        rendered = render_filter(as_term(filter_query))
        if not rendered:
            # Refuse to turn an empty filter into "delete everything".
            return
        try:
            self.client.collections[index_name].documents.delete({"filter_by": rendered})
        except TypesenseClientError as err:
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
        if limit > MAX_PER_PAGE:
            raise SearchBackendError(
                f"Typesense returns at most {MAX_PER_PAGE} hits per request; "
                f"use iter_documents() to page through more than that."
            )

        parameters: dict[str, Any] = {
            # Typesense uses "*" for match-everything, where Meilisearch uses "".
            "q": query or "*",
            "query_by": ",".join(QUERY_BY_FIELDS),
            "query_by_weights": ",".join(str(weight) for weight in QUERY_BY_WEIGHTS),
            "per_page": max(1, min(limit, MAX_PER_PAGE)),
            "page": (offset // limit) + 1 if limit else 1,
        }

        rendered = render_filter(as_term(search_filter))
        if rendered:
            parameters["filter_by"] = rendered

        if facets:
            parameters["facet_by"] = ",".join(facets)
            # Without this the response silently truncates to 10 values *and*
            # reports the truncated count as the total, so a partial facet list
            # is indistinguishable from a complete one.
            parameters["max_facet_values"] = MAX_FACET_VALUES

        if attributes_to_retrieve:
            parameters["include_fields"] = ",".join(attributes_to_retrieve)

        try:
            response = self.client.collections[index_name].documents.search(parameters)
        except TypesenseClientError as err:
            raise SearchBackendError(f"Search against '{index_name}' failed") from err

        hits = [
            SearchHit(document=hit["document"], highlights=hit.get("highlight", {}))
            for hit in response.get("hits", [])
        ]

        facet_distribution = {
            facet["field_name"]: {count["value"]: count["count"] for count in facet["counts"]}
            for facet in response.get("facet_counts", [])
        }

        return SearchResults(
            hits=hits,
            total_hits=response.get("found", 0),
            facet_distribution=facet_distribution,
        )

    # --- auth ---------------------------------------------------------------

    def generate_user_token(self, index_name: str, *, search_filter: str, expires_at: datetime) -> str:
        """
        Mint a Typesense scoped search key.

        The analogue of Meilisearch's tenant token, and it shares the property
        the original ADR valued: it is derived locally from a parent key with no
        API call, so the browser can query the engine directly and the embedded
        filter is still enforced server-side.
        """
        parent_key = settings.TYPESENSE_SEARCH_API_KEY
        rules = json.dumps(
            {
                "filter_by": search_filter,
                "expires_at": int(expires_at.timestamp()),
            },
            separators=(",", ":"),
        )
        digest = base64.b64encode(
            hmac.new(parent_key.encode(), rules.encode(), hashlib.sha256).digest()
        ).decode()
        return base64.b64encode(f"{digest}{parent_key[:4]}{rules}".encode()).decode()

    def access_filter(self, orgs: list[str], access_ids: list[int]) -> str:
        """
        Build the permission filter that goes inside a scoped search key.

        Returned as a rendered string because that is what has to be embedded in
        the key itself, not passed as a normal filter argument.
        """
        return render_filter(
            Or([In(Fields.org, orgs), In(Fields.access_id, access_ids)])
        )
