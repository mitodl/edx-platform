"""
Tests for the Typesense content-search backend.

Split deliberately in two:

* ``TestTypesenseFilters`` and ``TestTypesenseDocuments`` are pure and always
  run. They cover the translation layer, where the bugs are cheap to make.
* ``TestTypesenseBackendLive`` runs against a real Typesense and is skipped
  unless ``TYPESENSE_TEST_URL`` is set. These are not optional nice-to-haves:
  every defect found while writing this backend was a server-side rejection
  that a mocked client would have accepted. The most serious - backtick-quoting
  a numeric value, which silently broke the ``access_id`` filter that keeps one
  organisation's content out of another's results - passed against a mock.
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone

from django.test import SimpleTestCase, override_settings

from ..backends.base import And, Equals, Exists, In, Not, Or, SearchBackendError, as_term
from ..backends.typesense import TypesenseBackend, prepare_document, quote, render_filter
from ..backends.typesense_config import IS_NULL_SUFFIX, MAX_PER_PAGE, collection_schema

TYPESENSE_TEST_URL = os.environ.get("TYPESENSE_TEST_URL")
TYPESENSE_TEST_API_KEY = os.environ.get("TYPESENSE_TEST_API_KEY", "typesense_test_key")


class TestTypesenseFilters(SimpleTestCase):
    """The structured filter vocabulary renders to Typesense ``filter_by`` syntax."""

    def test_equals(self):
        assert render_filter(Equals("context_key", "lib:Org1:LibA")) == "context_key:=`lib:Org1:LibA`"

    def test_in(self):
        assert render_filter(In("block_type", ["html", "problem"])) == "block_type:=[`html`,`problem`]"

    def test_not_equals(self):
        assert render_filter(Not(Equals("block_type", "unit"))) == "block_type:!=`unit`"

    def test_not_in(self):
        assert render_filter(Not(In("block_type", ["unit", "section"]))) == "block_type:!=[`unit`,`section`]"

    def test_or(self):
        assert render_filter(Or([Equals("org", "A"), Equals("org", "B")])) == "(org:=`A`) || (org:=`B`)"

    def test_and(self):
        assert render_filter(And([Equals("org", "A"), Equals("type", "t")])) == "(org:=`A`) && (type:=`t`)"

    def test_exists_uses_companion_flag_for_nullable_fields(self):
        """
        Typesense has no IS NULL. ``last_published`` is written as an explicit
        None by documents.py, so absence is recorded in a companion boolean.
        """
        assert render_filter(Exists("last_published")) == f"last_published{IS_NULL_SUFFIX}:!=true"

    def test_numbers_are_not_quoted(self):
        """
        Backticking a value on a numeric field is rejected by Typesense with
        "Numerical field has an invalid comparator". This matters most for
        access_id, which the scoped search key filters on.
        """
        assert render_filter(Equals("access_id", 42)) == "access_id:=42"
        assert render_filter(In("access_id", [1, 2])) == "access_id:=[1,2]"
        assert render_filter(Equals("created", 1.5)) == "created:=1.5"

    def test_booleans_are_not_quoted(self):
        assert render_filter(Equals("flag", True)) == "flag:=true"
        assert render_filter(Equals("flag", False)) == "flag:=false"

    def test_backticks_are_stripped_from_values(self):
        """A backtick would otherwise close the quoting and let a value inject filter syntax."""
        assert quote("a`|| true") == "`a|| true`"

    def test_empty_in_matches_nothing(self):
        """An empty IN must not degrade into matching everything."""
        rendered = render_filter(In("org", []))
        assert ":!=[]" in rendered

    def test_no_filter_renders_empty(self):
        assert render_filter(None) == ""
        assert render_filter(as_term([])) == ""

    def test_bare_list_is_implicit_and(self):
        assert isinstance(as_term([Equals("a", 1), Equals("b", 2)]), And)
        assert as_term([Equals("a", 1)]) == Equals("a", 1)

    def test_unnegatable_term_is_rejected_loudly(self):
        with self.assertRaises(SearchBackendError):
            render_filter(Not(Exists("last_published")))


class TestTypesenseDocuments(SimpleTestCase):
    """Documents from documents.py are adapted to what Typesense accepts."""

    def test_null_is_replaced_by_a_companion_flag(self):
        """Typesense has no null: the field must be absent, and the absence recorded."""
        prepared = prepare_document({"id": "x", "last_published": None})
        assert "last_published" not in prepared
        assert prepared[f"last_published{IS_NULL_SUFFIX}"] is True

    def test_present_value_is_kept_and_flagged(self):
        prepared = prepare_document({"id": "x", "last_published": 123.5})
        assert prepared["last_published"] == 123.5
        assert prepared[f"last_published{IS_NULL_SUFFIX}"] is False

    def test_the_source_document_is_not_mutated(self):
        original = {"id": "x", "last_published": None}
        prepare_document(original)
        assert original["last_published"] is None

    def test_content_array_subfields_are_declared_before_the_wildcard(self):
        """
        The ``content\\..*`` wildcard declares string. Typesense rejects an array
        sub-field matched by it, which is every container document. The explicit
        overrides only work if they come first.
        """
        names = [field["name"] for field in collection_schema("x")["fields"]]
        wildcard = names.index(r"content\..*")
        for subfield in ("content.problem_types", "content.child_usage_keys", "content.child_display_names"):
            assert names.index(subfield) < wildcard, f"{subfield} must precede the content wildcard"

    def test_timestamps_are_float_not_int(self):
        """documents.py uses datetime.timestamp(), which is a float."""
        by_name = {field["name"]: field for field in collection_schema("x")["fields"]}
        for name in ("created", "modified", "last_published"):
            assert by_name[name]["type"] == "float"


@unittest.skipUnless(TYPESENSE_TEST_URL, "Set TYPESENSE_TEST_URL to run the live Typesense tests")
class TestTypesenseBackendLive(SimpleTestCase):
    """
    End-to-end against a real Typesense.

    A mocked client cannot observe a rejected request, and every defect found
    while writing this backend was exactly that.
    """

    COLLECTION = "test_studio_content_v1"
    ALIAS = "test_studio_content"

    def setUp(self):
        super().setUp()
        self.settings_patcher = override_settings(
            TYPESENSE_URLS=[TYPESENSE_TEST_URL],
            TYPESENSE_API_KEY=TYPESENSE_TEST_API_KEY,
        )
        self.settings_patcher.enable()
        self.addCleanup(self.settings_patcher.disable)

        self.backend = TypesenseBackend()
        for name in (self.COLLECTION, f"{self.COLLECTION}_new"):
            self.backend.delete_index(name)
        self.backend.create_index(self.COLLECTION)
        self.addCleanup(self.backend.delete_index, self.COLLECTION)

    def library_block(self, index, published=True, problem=False):
        """A document shaped like searchable_doc_for_library_block()."""
        doc = {
            "id": f"blk{index}",
            "type": "library_block",
            "usage_key": f"lb:Org1:LibA:html:blk{index}",
            "block_id": f"blk{index}",
            "block_type": "problem" if problem else "html",
            "display_name": f"Photosynthesis {index}",
            "description": "light into chemical energy",
            "context_key": "lib:Org1:LibA",
            "org": "Org1",
            "access_id": 42,
            "created": 1724889600.123456,
            "modified": 1735689600.65,
            "publish_status": "published" if published else "never",
            "breadcrumbs": [{"display_name": "Library A"}],
            "collections": {"display_name": ["Bio"], "key": ["COL_BIO"]},
            "tags": {
                "taxonomy": ["Location"],
                "level0": ["Location > NA"],
                "level1": ["Location > NA > Canada"],
                "level2": [],
                "level3": [],
            },
            "last_published": 1735689600.5 if published else None,
        }
        doc["content"] = (
            {"capa_content": "running water", "problem_types": ["multiplechoiceresponse"]}
            if problem
            else {"html_content": "running water"}
        )
        if published:
            doc["published"] = {"display_name": doc["display_name"], "description": "published"}
        return doc

    def container(self):
        """A document shaped like searchable_doc_for_container()."""
        return {
            "id": "unit1",
            "type": "library_container",
            "usage_key": "lct:Org1:LibA:unit:u1",
            "block_id": "u1",
            "block_type": "unit",
            "display_name": "Intro Unit",
            "context_key": "lib:Org1:LibA",
            "org": "Org1",
            "access_id": 42,
            "created": 1724889600.0,
            "modified": 1735689600.0,
            "num_children": 1,
            "publish_status": "never",
            "last_published": None,
            "content": {
                "child_usage_keys": ["lb:Org1:LibA:html:blk0"],
                "child_display_names": ["Photosynthesis 0"],
            },
            "breadcrumbs": [{"display_name": "Library A"}],
        }

    def collection_document(self):
        """A document shaped like searchable_doc_for_collection()."""
        return {
            "id": "col1",
            "type": "collection",
            "usage_key": "lib-collection:Org1:LibA:COL_BIO",
            "block_id": "COL_BIO",
            "display_name": "Bio",
            "description": "cell biology",
            "context_key": "lib:Org1:LibA",
            "org": "Org1",
            "access_id": 42,
            "created": 1724889600.0,
            "modified": 1735689600.0,
            "num_children": 12,
            "published": {"num_children": 10},
            "last_published": None,
            "breadcrumbs": [{"display_name": "Library A"}],
        }

    def load_fixtures(self):
        documents = [self.library_block(i, published=(i % 4 != 0), problem=(i % 3 == 0)) for i in range(12)]
        documents += [self.container(), self.collection_document()]
        self.backend.upsert_documents(self.COLLECTION, documents)
        return documents

    def test_all_three_document_shapes_index(self):
        """
        Containers carry array sub-fields under ``content``; collections carry
        none of the block fields. Both are rejected by a naive schema.
        """
        self.load_fixtures()
        assert self.backend.search(self.COLLECTION).total_hits == 14

    def test_index_lifecycle(self):
        assert self.backend.index_exists(self.COLLECTION) is True
        assert self.backend.index_is_empty(self.COLLECTION) is True
        self.load_fixtures()
        assert self.backend.index_is_empty(self.COLLECTION) is False
        assert self.backend.index_exists("no_such_collection") is False

    def test_keyword_search_and_stemming(self):
        self.load_fixtures()
        assert self.backend.search(self.COLLECTION, "photosynthesis").total_hits > 0
        # "run" matches "running" only if the content wildcard enabled stemming
        assert self.backend.search(self.COLLECTION, "run").total_hits > 0

    def test_filters(self):
        self.load_fixtures()
        search = self.backend.search
        assert search(self.COLLECTION, search_filter=Equals("context_key", "lib:Org1:LibA")).total_hits == 14
        assert search(self.COLLECTION, search_filter=[Equals("org", "Org1"), Equals("type", "library_block")]).total_hits == 12
        assert search(self.COLLECTION, search_filter=In("collections.key", ["COL_BIO"])).total_hits == 12
        assert search(self.COLLECTION, search_filter=Equals("tags.level1", "Location > NA > Canada")).total_hits == 12
        assert search(self.COLLECTION, search_filter=Not(In("block_type", ["unit"]))).total_hits == 13

    def test_show_only_published_filter(self):
        """The MFE's `last_published IS NOT NULL`, via the companion flag."""
        self.load_fixtures()
        assert self.backend.search(self.COLLECTION, search_filter=Exists("last_published")).total_hits == 9

    def test_facets(self):
        self.load_fixtures()
        results = self.backend.search(
            self.COLLECTION,
            facets=["block_type", "content.problem_types", "publish_status"],
            limit=1,
        )
        assert set(results.facet_distribution) == {"block_type", "content.problem_types", "publish_status"}

    def test_facets_are_not_silently_truncated(self):
        """
        Typesense returns 10 facet values by default *and* caps the reported
        total, so a truncated tag tree is indistinguishable from a complete one.
        """
        documents = [
            {
                "id": f"tag{index}",
                "type": "library_block",
                "display_name": f"tagged {index}",
                "context_key": "lib:Org1:LibA",
                "org": "Org1",
                "access_id": 42,
                "tags": {"taxonomy": ["Subject"], "level0": [f"Subject > Topic{index:02d}"]},
            }
            for index in range(25)
        ]
        self.backend.upsert_documents(self.COLLECTION, documents)
        results = self.backend.search(self.COLLECTION, facets=["tags.level0"], limit=1)
        assert len(results.facet_distribution["tags.level0"]) == 25

    def test_limit_above_the_page_cap_is_rejected(self):
        with self.assertRaises(SearchBackendError):
            self.backend.search(self.COLLECTION, limit=MAX_PER_PAGE + 1)

    def test_iter_documents_pages_through_everything(self):
        self.load_fixtures()
        found = list(self.backend.iter_documents(self.COLLECTION, attributes_to_retrieve=["id"], batch_size=5))
        assert len(found) == 14

    def test_get_and_delete_document(self):
        self.load_fixtures()
        assert self.backend.get_document(self.COLLECTION, "blk1")["block_id"] == "blk1"
        self.backend.delete_document(self.COLLECTION, "blk1")
        with self.assertRaises(SearchBackendError):
            self.backend.get_document(self.COLLECTION, "blk1")
        # deleting an absent document is not an error
        self.backend.delete_document(self.COLLECTION, "blk1")

    def test_delete_by_filter_never_deletes_everything_on_an_empty_filter(self):
        self.load_fixtures()
        self.backend.delete_documents_by_filter(self.COLLECTION, None)
        assert self.backend.search(self.COLLECTION).total_hits == 14
        self.backend.delete_documents_by_filter(self.COLLECTION, Equals("type", "collection"))
        assert self.backend.search(self.COLLECTION).total_hits == 13

    def test_alias_swap_publishes_and_reclaims(self):
        """
        The alias replaces Meilisearch's swap_indexes. Deleting the collection
        that was behind it returns the disk at once, which Meilisearch's LMDB
        store does not do.
        """
        rebuild = f"{self.COLLECTION}_new"
        self.backend.create_index(rebuild)
        self.addCleanup(self.backend.delete_index, rebuild)
        self.backend.upsert_documents(rebuild, [self.library_block(99)])

        self.backend.swap_indexes(self.ALIAS, rebuild)
        self.addCleanup(self.backend.delete_index, self.ALIAS)
        assert self.backend.search(self.ALIAS).total_hits == 1

    def test_scoped_search_key_is_enforced_by_the_server(self):
        """
        The tenant-token analogue. It is what lets the browser query Typesense
        directly, so the filter has to be enforced by the engine, not the caller.
        """
        import typesense  # pylint: disable=import-outside-toplevel

        self.load_fixtures()
        admin = typesense.Client({"api_key": TYPESENSE_TEST_API_KEY, "nodes": [TYPESENSE_TEST_URL]})
        parent = admin.keys.create(
            {"description": "test search", "actions": ["documents:search"], "collections": ["*"]}
        )["value"]
        expires_at = datetime.now(tz=timezone.utc) + timedelta(days=7)

        with override_settings(TYPESENSE_SEARCH_API_KEY=parent):
            permitted = self.backend.generate_user_token(
                self.COLLECTION,
                search_filter=self.backend.access_filter(["Org1"], [42]),
                expires_at=expires_at,
            )
            denied = self.backend.generate_user_token(
                self.COLLECTION,
                search_filter=self.backend.access_filter(["OtherOrg"], [999]),
                expires_at=expires_at,
            )

        def search_as(token):
            client = typesense.Client({"api_key": token, "nodes": [TYPESENSE_TEST_URL]})
            return client.collections[self.COLLECTION].documents.search(
                {"q": "*", "query_by": "display_name", "per_page": 1}
            )["found"]

        assert search_as(permitted) == 14
        # The filter is embedded in the key, so a caller cannot search around it.
        assert search_as(denied) == 0
