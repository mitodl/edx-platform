"""
Content index and search API.

The search engine itself lives behind ``backends``; nothing here talks to one
directly. Which engine is used is set by ``CONTENT_SEARCH_BACKEND``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Callable, Generator, cast  # noqa: UP035

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.paginator import Paginator
from opaque_keys import OpaqueKey
from opaque_keys.edx.keys import CourseKey, UsageKey
from opaque_keys.edx.locator import LibraryCollectionLocator, LibraryContainerLocator, LibraryLocatorV2
from openedx_content import api as content_api
from openedx_content import models_api as content_models
from rest_framework.request import Request

from common.djangoapps.student.role_helpers import get_course_roles
from common.djangoapps.student.roles import GlobalStaff
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.content.search.models import IncrementalIndexCompleted, get_access_ids_for_request
from openedx.core.djangoapps.content_libraries import api as lib_api
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.exceptions import ItemNotFoundError

from .backends import (
    Equals,
    clear_search_backend,
    Filter,
    # Re-exported: IndexDrift was defined here before the backends split, and
    # callers (including reconcile_index's tests) still import it from api.
    IndexDrift,
    SearchBackendError,
    get_search_backend,
)
from .documents import (
    Fields,
    meili_id_from_opaque_key,
    searchable_doc_collections,
    searchable_doc_containers,
    searchable_doc_for_collection,
    searchable_doc_for_container,
    searchable_doc_for_course_block,
    searchable_doc_for_key,
    searchable_doc_for_library_block,
    searchable_doc_tags,
)

log = logging.getLogger(__name__)

User = get_user_model()

STUDIO_INDEX_SUFFIX = "studio_content"

if hasattr(settings, "MEILISEARCH_INDEX_PREFIX"):
    STUDIO_INDEX_NAME = settings.MEILISEARCH_INDEX_PREFIX + STUDIO_INDEX_SUFFIX
else:
    STUDIO_INDEX_NAME = STUDIO_INDEX_SUFFIX


LOCK_EXPIRE = 24 * 60 * 60  # Lock expires in 24 hours

MAX_ACCESS_IDS_IN_FILTER = 1_000
MAX_ORGS_IN_FILTER = 1_000

EXCLUDED_XBLOCK_TYPES = ["course", "course_info"]


@contextmanager
def _index_rebuild_lock() -> Generator[str, None, None]:
    """
    Lock to prevent that more than one rebuild is running at the same time
    """
    lock_id = f"lock-meilisearch-index-{STUDIO_INDEX_NAME}"
    new_index_name = STUDIO_INDEX_NAME + "_new"

    status = cache.add(lock_id, new_index_name, LOCK_EXPIRE)

    if not status:
        # Lock already acquired
        raise RuntimeError("Rebuild already in progress")

    # Lock acquired
    try:
        yield new_index_name
    finally:
        # Release the lock
        cache.delete(lock_id)


def _get_running_rebuild_index_name() -> str | None:
    lock_id = f"lock-meilisearch-index-{STUDIO_INDEX_NAME}"

    return cache.get(lock_id)


def _backend():
    """The configured search backend."""
    if not is_search_enabled():
        raise RuntimeError("MEILISEARCH_ENABLED is not set - search functionality disabled.")
    return get_search_backend()


def clear_search_client() -> None:
    """
    Drop the cached backend, and with it the engine client it holds.

    Mostly for tests, and for picking up a settings change without a restart.
    """
    clear_search_backend()


def _index_exists(index_name: str) -> bool:
    """
    Check if an index exists
    """
    return _backend().index_exists(index_name)


@contextmanager
def _using_temp_index(status_cb: Callable[[str], None] | None = None) -> Generator[str, None, None]:
    """
    Create a new temporary Meilisearch index, populate it, then swap it to
    become the active index.

    Args:
        status_cb (Callable): A callback function to report status messages
    """
    if status_cb is None:
        status_cb = log.info

    backend = _backend()
    status_cb("Checking index...")
    with _index_rebuild_lock() as temp_index_name:
        if backend.index_exists(temp_index_name):
            status_cb("Temporary index already exists. Deleting it...")
            backend.delete_index(temp_index_name)

        status_cb("Creating new index...")
        backend.create_index(temp_index_name)

        yield temp_index_name

        status_cb("Swapping index...")
        backend.swap_indexes(STUDIO_INDEX_NAME, temp_index_name)


def _index_is_empty(index_name: str) -> bool:
    """
    Check if an index is empty

    Args:
        index_name (str): The name of the index to check
    """
    return _backend().index_is_empty(index_name)


def _apply_index_settings(
    index_name: str,
    wait: bool,
    status_cb: Callable[[str], None] | None = None,
) -> None:
    """
    Apply this app's configuration to an index.

    When wait=False, settings are sent fire-and-forget. This is appropriate for
    an empty temporary index that will immediately be populated on the same
    task queue.

    When wait=True, each settings change is confirmed before returning, which is
    what reconciling a live index needs. Backends whose writes are synchronous
    ignore the distinction.
    """
    _backend().apply_index_settings(index_name, wait=wait, status_cb=status_cb)


def _recurse_children(block, fn, status_cb: Callable[[str], None] | None = None) -> None:
    """
    Recurse the children of an XBlock and call the given function for each

    The main purpose of this is just to wrap the loading of each child in
    try...except. Otherwise block.get_children() would do what we need.
    """
    if block.has_children:
        for child_id in block.children:
            try:
                child = block.get_child(child_id)
                if child is None:
                    # XBlocks with XModuleMixin will return None from get_child() instead of raising an exception :/
                    raise ItemNotFoundError(f"block.get_child() from {block.usage_key} failed to load child {child_id}")
            except Exception as err:  # pylint: disable=broad-except
                log.exception(err)
                if status_cb is not None:
                    status_cb(f"Unable to load block {child_id}")
            else:
                fn(child)


def _update_index_docs(docs) -> None:
    """
    Helper function that updates the documents in the search index

    If there is a rebuild in progress, the document will also be added to the new index.
    """
    if not docs:
        return

    backend = _backend()
    current_rebuild_index_name = _get_running_rebuild_index_name()

    if current_rebuild_index_name:
        # If there is a rebuild in progress, the document goes into the new index too.
        backend.upsert_documents(current_rebuild_index_name, docs, wait=False)
    backend.upsert_documents(STUDIO_INDEX_NAME, docs)


def only_if_search_enabled(f):
    """
    Only call `f` if Studio content search is enabled
    """

    @wraps(f)
    def wrapper(*args, **kwargs):
        """Wraps the decorated function."""
        if is_search_enabled():
            return f(*args, **kwargs)

    return wrapper


def is_search_enabled() -> bool:
    """
    Returns whether Meilisearch is enabled
    """
    if hasattr(settings, "MEILISEARCH_ENABLED"):
        return settings.MEILISEARCH_ENABLED

    return False


def reset_index(status_cb: Callable[[str], None] | None = None) -> None:
    """
    Reset the Meilisearch index, deleting all documents and reconfiguring it
    """
    if status_cb is None:
        status_cb = log.info

    status_cb("Creating new empty index...")
    with _using_temp_index(status_cb) as temp_index_name:
        _apply_index_settings(temp_index_name, wait=False)
        status_cb("Index recreated!")
    status_cb("Index reset complete.")


def _detect_index_drift(index_name: str) -> IndexDrift:
    """
    Inspect the current state of an index and return a structured drift report.

    Which settings a backend can report on varies by engine; see IndexDrift.
    """
    return _backend().detect_index_drift(index_name)


def reconcile_index(
    status_cb: Callable[[str], None] | None = None, warn_cb: Callable[[str], None] | None = None
) -> None:  # noqa: E501
    """
    Reconcile the Meilisearch index state.

    Inspects the current Studio Meilisearch index and takes appropriate action based on its state:
    - Creates the index if missing.
    - Reconfigures if empty and drifted.
    - Applies updated settings if populated and drifted.
    - Recreates the index if primary key is mismatched (even if populated — data loss is unavoidable).
    - No-ops if everything is correctly configured.

    This is the primary reconciliation entry point, called from post_migrate and init_index().
    """
    if status_cb is None:
        status_cb = log.info
    if warn_cb is None:
        warn_cb = log.warning

    drift = _detect_index_drift(STUDIO_INDEX_NAME)

    # CASE: Index missing
    if not drift.exists:
        status_cb("Studio search index not found. Creating and configuring...")
        reset_index(status_cb)
        status_cb("Index created. Run './manage.py cms reindex_studio' to populate.")
        return

    # CASE: Primary key mismatch (must recreate regardless of population state)
    if not drift.primary_key_correct:
        if drift.is_empty:
            warn_cb("Primary key mismatch on empty index. Recreating...")
        else:
            warn_cb(
                f"PRIMARY KEY MISMATCH on populated index '{STUDIO_INDEX_NAME}'. "
                "Index must be recreated (data loss is unavoidable for primary key changes)."
            )
            warn_cb("Dropping and recreating index. Repopulate with: './manage.py cms reindex_studio'")
        reset_index(status_cb)
        warn_cb("Index recreated empty. Run './manage.py cms reindex_studio' to repopulate.")
        return

    # CASE: Index empty
    if drift.is_empty:
        if drift.is_settings_drifted:
            status_cb("Empty index has drifted settings. Reconfiguring...")
            _apply_index_settings(STUDIO_INDEX_NAME, wait=True, status_cb=status_cb)
            status_cb("Reconfigured. Run './manage.py cms reindex_studio' to populate.")
        else:
            status_cb(
                "Index exists and is correctly configured but empty. Run './manage.py cms reindex_studio' to populate."
            )
        return

    # CASE: Index populated, attribute drifted i.e settings mismatched
    if drift.is_settings_drifted:
        warn_cb(f"Settings drift detected on populated index '{STUDIO_INDEX_NAME}'. Applying updated settings...")
        # Log per-setting mismatch details
        for field_name, match in (
            ("distinctAttribute", drift.distinct_attribute_match),
            ("filterableAttributes", drift.filterable_attributes_match),
            ("searchableAttributes", drift.searchable_attributes_match),
            ("sortableAttributes", drift.sortable_attributes_match),
            ("rankingRules", drift.ranking_rules_match),
        ):
            if match is False:
                warn_cb(f"  - {field_name}: DRIFTED")

        _apply_index_settings(STUDIO_INDEX_NAME, wait=True, status_cb=status_cb)
        warn_cb(
            "Settings applied. Meilisearch will re-index documents in the background. "
            "Consider running './manage.py cms reindex_studio' for a full rebuild "
            "if search quality is affected."
        )
    else:
        status_cb("Index is populated and correctly configured. No action needed.")


def init_index(status_cb: Callable[[str], None] | None = None, warn_cb: Callable[[str], None] | None = None) -> None:
    """
    This method is depricated as of Verawood and would be removed in the future release.

    Initialize the Meilisearch index, creating it and configuring it if it doesn't exist.

    This is a compatibility wrapper around reconcile_index().
    """
    log.warning("init_index is deprecated as of Verawood and will be removed in the future release.")
    reconcile_index(status_cb=status_cb, warn_cb=warn_cb)


def index_course(
    course_key: CourseKey,
    index_name: str | None = None,
    status_cb: Callable[[str], None] | None = None,
) -> list[dict]:
    """
    Rebuilds the index for a given course.
    """
    store = modulestore()
    backend = _backend()
    docs = []
    if index_name is None:
        index_name = STUDIO_INDEX_NAME
    if status_cb is None:
        status_cb = log.info

    # Pre-fetch the course with all of its children:
    course = store.get_course(course_key, depth=None)

    if course is None:
        status_cb(f"Error: course {course_key} does not seem to exist! It may have been incompletely deleted.")
        return []

    def add_with_children(block):
        """Recursively index the given XBlock/component"""
        doc = searchable_doc_for_course_block(block)
        doc.update(searchable_doc_tags(block.usage_key))
        docs.append(doc)  # pylint: disable=cell-var-from-loop
        _recurse_children(block, add_with_children)  # pylint: disable=cell-var-from-loop

    # Index course children
    _recurse_children(course, add_with_children)

    if docs:
        # Add all the docs in this course at once (usually faster than adding one at a time):
        backend.add_documents(index_name, docs)
    return docs


def rebuild_index(  # pylint: disable=too-many-statements
    status_cb: Callable[[str], None] | None = None, incremental=False
) -> None:
    """
    Rebuild the Meilisearch index from scratch
    """
    if status_cb is None:
        status_cb = log.info

    backend = _backend()

    # Get the lists of libraries
    status_cb("Counting libraries...")
    keys_indexed = []
    if incremental:
        keys_indexed = list(IncrementalIndexCompleted.objects.values_list("context_key", flat=True))
        if keys_indexed:
            status_cb(f"Resuming incremental index - {len(keys_indexed)} courses/libraries already indexed.")
    lib_keys = [
        lib.library_key
        for lib in lib_api.ContentLibrary.objects.select_related("org").only("org", "slug").order_by("-id")
        if lib.library_key not in keys_indexed
    ]
    num_libraries = len(lib_keys)

    # Get the list of courses
    status_cb("Counting courses...")
    num_courses = CourseOverview.objects.count()

    # Some counters so we can track our progress as indexing progresses:
    num_libs_skipped = len(keys_indexed)
    num_contexts = num_courses + num_libraries + num_libs_skipped
    num_contexts_done = 0 + num_libs_skipped  # How many courses/libraries we've indexed
    num_blocks_done = 0  # How many individual components/XBlocks we've indexed

    status_cb(f"Found {num_courses} courses, {num_libraries} libraries.")
    with _using_temp_index(status_cb) if not incremental else nullcontext(STUDIO_INDEX_NAME) as index_name:
        ############## Configure the index ##############

        # The index settings are best changed on an empty index.
        # Changing them on a populated index will "re-index all documents in the index", which can take some time
        # and use more RAM. Instead, we configure an empty index then populate it one course/library at a time.
        if not incremental:
            _apply_index_settings(index_name, wait=False)

        ############## Libraries ##############
        status_cb("Indexing libraries...")

        def index_library(lib_key: LibraryLocatorV2) -> list:
            docs = []
            for component in lib_api.get_library_components(lib_key):
                try:
                    metadata = lib_api.LibraryXBlockMetadata.from_component(lib_key, component)
                    doc = {}
                    doc.update(searchable_doc_for_library_block(metadata))
                    doc.update(searchable_doc_tags(metadata.usage_key))
                    doc.update(searchable_doc_collections(metadata.usage_key))
                    doc.update(searchable_doc_containers(metadata.usage_key, "units"))
                    docs.append(doc)
                except Exception as err:  # pylint: disable=broad-except
                    status_cb(f"Error indexing library component {component}: {err}")
            if docs:
                try:
                    # Add all the docs in this library at once (usually faster than adding one at a time):
                    backend.add_documents(index_name, docs)
                except (TypeError, KeyError, SearchBackendError) as err:
                    status_cb(f"Error indexing library {lib_key}: {err}")
            return docs

        ############## Collections ##############
        def index_collection_batch(batch, num_done, library_key) -> int:
            docs = []
            for collection in batch:
                try:
                    collection_key = lib_api.library_collection_locator(library_key, collection.collection_code)
                    doc = searchable_doc_for_collection(collection_key, collection=collection)
                    doc.update(searchable_doc_tags(collection_key))
                    docs.append(doc)
                except Exception as err:  # pylint: disable=broad-except
                    status_cb(f"Error indexing collection {collection}: {err}")
                num_done += 1

            if docs:
                try:
                    # Add docs in batch of 100 at once (usually faster than adding one at a time):
                    backend.add_documents(index_name, docs)
                except (TypeError, KeyError, SearchBackendError) as err:
                    status_cb(f"Error indexing collection batch {p}: {err}")
            return num_done

        ############## Containers ##############
        def index_container_batch(batch, num_done, library_key) -> int:
            docs = []
            for container in batch:
                try:
                    container_key = lib_api.library_container_locator(
                        library_key,
                        container,
                    )
                    doc = searchable_doc_for_container(container_key)
                    doc.update(searchable_doc_tags(container_key))
                    doc.update(searchable_doc_collections(container_key))
                    container_type_code = container_key.container_type
                    match container_type_code:
                        case content_models.Unit.type_code:
                            doc.update(searchable_doc_containers(container_key, "subsections"))
                        case content_models.Subsection.type_code:
                            doc.update(searchable_doc_containers(container_key, "sections"))
                    docs.append(doc)
                except Exception as err:  # pylint: disable=broad-except
                    status_cb(f"Error indexing container {container.entity_ref}: {err}")
                num_done += 1

            if docs:
                try:
                    # Add docs in batch of 100 at once (usually faster than adding one at a time):
                    backend.add_documents(index_name, docs)
                except (TypeError, KeyError, SearchBackendError) as err:
                    status_cb(f"Error indexing container batch {p}: {err}")
            return num_done

        for lib_key in lib_keys:
            status_cb(f"{num_contexts_done + 1}/{num_contexts}. Now indexing blocks in library {lib_key}")
            lib_docs = index_library(lib_key)
            num_blocks_done += len(lib_docs)

            # To reduce memory usage on large instances, split up the Collections into pages of 100 collections:
            library = lib_api.get_library(lib_key)
            collections = content_api.get_collections(library.learning_package_id, enabled=True)
            num_collections = collections.count()
            num_collections_done = 0
            if num_collections:
                status_cb(f"Now indexing {num_collections} collections in library {lib_key}")
            paginator = Paginator(collections, 100)
            for p in paginator.page_range:
                num_collections_done = index_collection_batch(
                    paginator.page(p).object_list,
                    num_collections_done,
                    lib_key,
                )
            status_cb(f"Indexed {num_collections_done}/{num_collections} collections in library {lib_key}")

            # Similarly, batch process Containers (units, sections, etc) in pages of 100
            containers = content_api.get_containers(library.learning_package_id)
            num_containers = containers.count()
            num_containers_done = 0
            if num_containers:
                status_cb(f"Now indexing {num_containers} containers in library {lib_key}")
            paginator = Paginator(containers, 100)
            for p in paginator.page_range:
                num_containers_done = index_container_batch(
                    paginator.page(p).object_list,
                    num_containers_done,
                    lib_key,
                )
                status_cb(f"Indexed {num_containers_done}/{num_containers} containers in library {lib_key}")

            # Mark this library as indexed:
            if incremental:
                IncrementalIndexCompleted.objects.get_or_create(context_key=lib_key)

            num_contexts_done += 1

        ############## Courses ##############
        status_cb("Indexing courses...")
        # To reduce memory usage on large instances, split up the CourseOverviews into pages of 1,000 courses:

        paginator = Paginator(CourseOverview.objects.only("id", "display_name").order_by("-created", "id"), 1000)
        for p in paginator.page_range:
            for course in paginator.page(p).object_list:
                status_cb(
                    f"{num_contexts_done + 1}/{num_contexts}. Now indexing course {course.display_name} ({course.id})"
                )
                if course.id in keys_indexed:
                    num_contexts_done += 1
                    continue
                course_docs = index_course(course.id, index_name, status_cb)
                if incremental:
                    IncrementalIndexCompleted.objects.get_or_create(context_key=course.id)
                num_contexts_done += 1
                num_blocks_done += len(course_docs)

    IncrementalIndexCompleted.objects.all().delete()
    status_cb(f"Done! {num_blocks_done} blocks indexed across {num_contexts_done} courses, collections and libraries.")


def upsert_xblock_index_doc(usage_key: UsageKey, recursive: bool = True) -> None:
    """
    Creates or updates the document for the given XBlock in the search index


    Args:
        usage_key (UsageKey): The usage key of the XBlock to index
        recursive (bool): If True, also index all children of the XBlock
    """
    xblock = modulestore().get_item(usage_key)
    xblock_type = xblock.scope_ids.block_type

    if xblock_type in EXCLUDED_XBLOCK_TYPES:
        return

    docs = []

    def add_with_children(block):
        """Recursively index the given XBlock/component"""
        doc = searchable_doc_for_course_block(block)
        docs.append(doc)
        if recursive:
            _recurse_children(block, add_with_children)

    add_with_children(xblock)

    _update_index_docs(docs)


def delete_index_doc(key: OpaqueKey, *, delete_children: bool = False) -> None:
    """
    Deletes the document for the given XBlock from the search index

    Args:
        key (OpaqueKey): The opaque key of the XBlock/Container to be removed from the index
    """
    doc = searchable_doc_for_key(key)
    _delete_index_doc(doc[Fields.id])
    if delete_children:
        _delete_documents(Equals(f"{Fields.breadcrumbs}.{Fields.usage_key}", str(key)))


def delete_docs_with_context_key(key: OpaqueKey) -> None:
    """
    Delete all docs for given context key
    """
    _delete_documents(Equals(Fields.context_key, str(key)))


def _delete_documents(filter_query: Filter | None) -> None:
    """
    Deletes all documents from the search index that match the given filter

    Args:
        filter_query: A structured filter (see ``backends.base``)
    """
    if not filter_query:
        return

    backend = _backend()
    current_rebuild_index_name = _get_running_rebuild_index_name()

    if current_rebuild_index_name:
        # If there is a rebuild in progress, the document is removed from the new index too.
        backend.delete_documents_by_filter(current_rebuild_index_name, filter_query)
    backend.delete_documents_by_filter(STUDIO_INDEX_NAME, filter_query)


def _delete_index_doc(doc_id) -> None:
    """
    Helper function that deletes the document with the given ID from the search index

    If there is a rebuild in progress, the document will also be removed from the new index.
    """
    if not doc_id:
        return

    backend = _backend()
    current_rebuild_index_name = _get_running_rebuild_index_name()

    if current_rebuild_index_name:
        # If there is a rebuild in progress, the document is removed from the new index too.
        backend.delete_document(current_rebuild_index_name, doc_id)

    backend.delete_document(STUDIO_INDEX_NAME, doc_id)


def upsert_library_block_index_doc(usage_key: UsageKey) -> None:
    """
    Creates or updates the document for the given Library Block in the search index
    """

    library_block = lib_api.get_component_from_usage_key(usage_key)
    library_block_metadata = lib_api.LibraryXBlockMetadata.from_component(usage_key.context_key, library_block)

    docs = [searchable_doc_for_library_block(library_block_metadata)]

    _update_index_docs(docs)


def _get_document_from_index(document_id: str) -> dict:
    """
    Returns the Document identified by the given ID, from the given index.

    Returns None if the document or index do not exist.
    """
    document = None
    index_name = STUDIO_INDEX_NAME
    try:
        document = _backend().get_document(index_name, document_id)
    except SearchBackendError as err:
        # The index or document doesn't exist
        log.warning(f"Unable to fetch document {document_id} from {index_name}: {err}")

    return document


def upsert_library_collection_index_doc(collection_key: LibraryCollectionLocator) -> None:
    """
    Creates, updates, or deletes the document for the given Library Collection in the search index.

    If the Collection is not found or disabled (i.e. soft-deleted), then delete it from the search index.
    """
    doc = searchable_doc_for_collection(collection_key)
    # Soft-deleted/disabled/hard-deleted collections are removed from the index:
    # (If the collection is soft-deleted, searchable_doc_for_collection() sets `_disabled: True`)
    # (If the collection is hard-deleted, searchable_doc_for_collection() leaves all fields other than ID empty)
    if doc.get("_disabled") or not doc.get(Fields.type):
        _delete_index_doc(doc[Fields.id])
        return

    # Normal case - update the collection doc.
    _update_index_docs([doc])

    # We do NOT update the individual entities (components/containers) in the collection here.
    # This event can be called if a single entity is added or removed from the collection (to update the "# of items in
    # collection" field (Fields.num_children), and we don't want to re-index all entities in that case).
    #
    # If the collection is renamed, the COLLECTION_CHANGED signal will be emitted, and content_libraries will handle it
    # and emit CONTENT_OBJECT_ASSOCIATIONS_CHANGED for every entity in the collection, which will update their
    # "collections" field in the search index.
    #
    # If the collection is enabled/disabled/deleted, the COLLECTION_CHANGED signal will include all entities in the
    # collection as added or removed, which the same libraries signal handler will convert to
    # CONTENT_OBJECT_ASSOCIATIONS_CHANGED events, which will update them.


def update_library_components_collections(
    collection_key: LibraryCollectionLocator,
    batch_size: int = 1000,
) -> None:
    """
    Updates the "collections" field for all components associated with a given Library Collection.

    Because there may be a lot of components, we send these updates to Meilisearch in batches.
    """
    library_key = collection_key.lib_key
    library = lib_api.get_library(library_key)
    components = content_api.get_collection_components(
        library.learning_package_id,
        collection_key.collection_id,
    )

    paginator = Paginator(components, batch_size)
    for page in paginator.page_range:
        docs = []

        for component in paginator.page(page).object_list:
            usage_key = lib_api.library_component_usage_key(
                library_key,
                component,
            )
            doc = searchable_doc_for_key(usage_key)
            doc.update(searchable_doc_collections(usage_key))
            docs.append(doc)

        log.info(
            f"Updating document.collections for library {library_key} components page {page} / {paginator.num_pages}"
        )
        _update_index_docs(docs)


def update_library_containers_collections(
    collection_key: LibraryCollectionLocator,
    batch_size: int = 1000,
) -> None:
    """
    Updates the "collections" field for all containers associated with a given Library Collection.

    Because there may be a lot of containers, we send these updates to Meilisearch in batches.
    """
    library_key = collection_key.lib_key
    library = lib_api.get_library(library_key)
    container_entities = (
        content_api.get_collection_entities(
            library.learning_package_id,
            collection_key.collection_id,
        )
        .exclude(container=None)
        .select_related("container")
    )

    paginator = Paginator(container_entities, batch_size)
    for page in paginator.page_range:
        docs = []

        for container_entity in paginator.page(page).object_list:
            container_key = lib_api.library_container_locator(
                library_key,
                container_entity.container,
            )
            doc = searchable_doc_for_key(container_key)
            doc.update(searchable_doc_collections(container_key))
            docs.append(doc)

        log.info(
            f"Updating document.collections for library {library_key} containers page {page} / {paginator.num_pages}"
        )
        _update_index_docs(docs)


def upsert_library_container_index_doc(container_key: LibraryContainerLocator) -> None:
    """
    Creates, updates, or deletes the document for the given Library Container in the search index.

    TODO: add support for indexing a container's components, like upsert_library_collection_index_doc does.
    """
    doc = searchable_doc_for_container(container_key)

    # Soft-deleted/disabled containers are removed from the index
    # and their components updated.
    if doc.get("_disabled"):
        _delete_index_doc(doc[Fields.id])

    # Hard-deleted containers are also deleted from the index
    elif not doc.get(Fields.type):
        _delete_index_doc(doc[Fields.id])

    # Otherwise, upsert the container.
    else:
        _update_index_docs([doc])


def upsert_content_library_index_docs(library_key: LibraryLocatorV2, full_index: bool = False) -> None:
    """
    Creates or updates the documents for the given Content Library in the search index
    """
    docs = []
    for component in lib_api.get_library_components(library_key):
        metadata = lib_api.LibraryXBlockMetadata.from_component(library_key, component)
        doc = searchable_doc_for_library_block(metadata)
        docs.append(doc)

    if full_index:
        # For a full re-index, we also need to update collections, and containers data:
        for container in lib_api.get_library_containers(library_key):
            container_key = lib_api.library_container_locator(
                library_key,
                container,
            )
            doc = searchable_doc_for_container(container_key)
            docs.append(doc)

        for collection in lib_api.get_library_collections(library_key):
            collection_key = lib_api.library_collection_locator(library_key, collection.collection_code)
            doc = searchable_doc_for_collection(collection_key, collection=collection)
            docs.append(doc)

    _update_index_docs(docs)


def upsert_content_object_tags_index_doc(key: OpaqueKey):
    """
    Updates the tags data in document for the given Course/Library item
    """
    doc = {Fields.id: meili_id_from_opaque_key(key)}
    doc.update(searchable_doc_tags(key))
    _update_index_docs([doc])


def upsert_item_collections_index_docs(opaque_key: OpaqueKey):
    """
    Updates the collections data in documents for the given Course/Library block, or Container
    """
    doc = {Fields.id: meili_id_from_opaque_key(opaque_key)}
    doc.update(searchable_doc_collections(opaque_key))
    _update_index_docs([doc])


def upsert_item_containers_index_docs(opaque_key: OpaqueKey, container_type: str):
    """
    Updates the containers (units/subsections/sections) data in documents for the given Course/Library block
    """
    doc = {Fields.id: meili_id_from_opaque_key(opaque_key)}
    doc.update(searchable_doc_containers(opaque_key, container_type))
    _update_index_docs([doc])


def _get_user_orgs(request: Request) -> list[str]:
    """
    Get the org.short_names for the organizations that the requesting user has OrgStaffRole or OrgInstructorRole.

    Note: org-level roles have course_id=None to distinguish them from course-level roles.
    """
    course_roles = get_course_roles(request.user)
    return list(
        set(role.org for role in course_roles if role.course_id is None and role.role in ["staff", "instructor"])
    )


def _get_access_filter(request: Request) -> str:
    """
    Return the search filter that limits results to what the user may see.

    Rendered to an engine-specific string because it is embedded inside the
    restricted API key handed to the browser, rather than passed as an ordinary
    filter argument.
    """
    # Global staff can see anything, so no filter is required.
    if GlobalStaff().has_user(request.user):
        return ""

    # Everyone else is limited to their org staff roles...
    user_orgs = _get_user_orgs(request)[:MAX_ORGS_IN_FILTER]

    # ...or the N most recent courses and libraries they can access.
    access_ids = get_access_ids_for_request(request, omit_orgs=user_orgs)[:MAX_ACCESS_IDS_IN_FILTER]
    return _backend().access_filter(user_orgs, access_ids)


def generate_user_token_for_studio_search(request):
    """
    Returns a Meilisearch API key that only allows the user to search content that they have permission to view
    """
    expires_at = datetime.now(tz=timezone.utc) + timedelta(days=7)  # noqa: UP017

    # Note: this only mints a credential locally; it makes no API call.
    restricted_api_key = _backend().generate_user_token(
        STUDIO_INDEX_NAME,
        search_filter=_get_access_filter(request),
        expires_at=expires_at,
    )

    return {
        "url": settings.MEILISEARCH_PUBLIC_URL,
        "index_name": STUDIO_INDEX_NAME,
        "api_key": restricted_api_key,
    }


def force_array(extra_filter: Filter | None = None) -> list[str]:
    """
    Convert a filter value into a list of strings.

    Strings are wrapped in a list, lists are returned as-is (cast to `list[str]`),
    and None results in an empty list.
    """
    if isinstance(extra_filter, str):
        return [extra_filter]
    if isinstance(extra_filter, list):
        return cast(list[str], extra_filter)
    return []


def fetch_block_types(extra_filter: Filter | None = None):
    """
    Fetch the block types facet distribution for the search results.

    This data may not always be 100% accurate / up to date because it's based
    on the search index, so this should only be used for analysis/estimation
    purposes.

    Params:
    - extra_filter: Filters the query. Example: ['context_key = "course-v1:SampleTaxonomyOrg1+CC22+CC22"']

    Return example:
    {
        ...
        'estimatedTotalHits': 5,
        'facetDistribution': {
            'block_type': {
                'html': 2,
                'problem': 1,
                'video': 2,
            }
        },
    }
    """
    results = _backend().search(
        STUDIO_INDEX_NAME,
        search_filter=extra_filter,
        facets=[Fields.block_type],
        limit=0,
    )

    return {
        "facetDistribution": results.facet_distribution,
        "estimatedTotalHits": results.total_hits,
    }


def get_all_blocks_from_context(
    context_key: str,
    extra_attributes_to_retrieve: list[str] | None = None,
) -> Iterator[dict]:
    """
    Lazily yields all blocks for a given context key using Meilisearch pagination.
    Meilisearch works with limits of 1000 maximum; ensuring we obtain all blocks
    requires making several queries.

    This data may not always be 100% accurate / up to date because it's based
    on the search index, so this should only be used for analysis/estimation
    purposes.
    """
    yield from _backend().iter_documents(
        STUDIO_INDEX_NAME,
        search_filter=Equals(Fields.context_key, str(context_key)),
        attributes_to_retrieve=[Fields.usage_key] + (extra_attributes_to_retrieve or []),
    )
