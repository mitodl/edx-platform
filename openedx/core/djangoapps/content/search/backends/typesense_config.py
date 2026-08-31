"""
Typesense collection configuration for the Studio content index.

Every field declaration here was validated against a running Typesense by
``tests/test_typesense_backend.py``. Three of them exist for reasons that are
not obvious and will look removable:

* The three explicit ``content.*`` overrides must precede the ``content\\..*``
  wildcard. The wildcard declares ``string``, and Typesense rejects a document
  whose sub-field is an array with "field inside an array of objects must be an
  array type as well" - which every container document has.
* ``created``/``modified``/``last_published`` are ``float``, not ``int64``,
  because ``documents.py`` serialises datetimes with ``.timestamp()``.
* ``last_published__is_null`` exists because Typesense has no IS NULL operator
  and the Authoring MFE filters on "has ever been published".
"""

from __future__ import annotations

from ..documents import Fields

# Typesense caps per_page at 250 and max_facet_values defaults to 10, silently -
# a truncated facet list is not distinguishable from a complete one in the
# response, so every faceted request must set it.
MAX_PER_PAGE = 250
MAX_FACET_VALUES = 1000

# Suffix for the companion boolean that stands in for a NULL check.
IS_NULL_SUFFIX = "__is_null"

# Fields written as an explicit None that the UI needs to filter on.
NULLABLE_FILTERABLE_FIELDS = [Fields.last_published]


def _tags_field(level: str) -> dict:
    return {"name": f"{Fields.tags}.{level}", "type": "string[]", "facet": True, "optional": True}


def collection_schema(collection_name: str) -> dict:
    """The Typesense collection definition for the Studio content index."""
    return {
        "name": collection_name,
        # tags.levelN, collections.key, published.*, breadcrumbs.usage_key
        "enable_nested_fields": True,
        "fields": [
            # Auto-create anything not declared below, as edx-search's engine does.
            {"name": ".*", "type": "auto"},

            # identity / scoping
            {"name": Fields.usage_key, "type": "string", "facet": True, "optional": True},
            {"name": Fields.block_id, "type": "string", "optional": True},
            {"name": Fields.type, "type": "string", "facet": True, "optional": True},
            {"name": Fields.block_type, "type": "string", "facet": True, "optional": True},
            {"name": Fields.context_key, "type": "string", "facet": True, "optional": True},
            {"name": Fields.org, "type": "string", "facet": True, "optional": True},
            {"name": Fields.access_id, "type": "int64", "facet": True, "optional": True},

            # text
            {"name": Fields.display_name, "type": "string", "optional": True, "sort": True},
            {"name": Fields.description, "type": "string", "optional": True},
            {"name": Fields.content, "type": "object", "optional": True},

            # These must come before the content wildcard; see the module docstring.
            {
                "name": f"{Fields.content}.{Fields.problem_types}",
                "type": "string[]",
                "facet": True,
                "optional": True,
            },
            {"name": f"{Fields.content}.{Fields.child_usage_keys}", "type": "string[]", "optional": True},
            {"name": f"{Fields.content}.{Fields.child_display_names}", "type": "string[]", "optional": True},
            {"name": rf"{Fields.content}\..*", "type": "string", "stem": True, "optional": True},

            # publish state
            {"name": Fields.publish_status, "type": "string", "facet": True, "optional": True},
            {"name": Fields.published, "type": "object", "optional": True},
            {
                "name": f"{Fields.published}.{Fields.published_display_name}",
                "type": "string",
                "optional": True,
            },
            {
                "name": f"{Fields.published}.{Fields.published_description}",
                "type": "string",
                "optional": True,
            },
            {
                "name": f"{Fields.published}.{Fields.published_num_children}",
                "type": "int32",
                "optional": True,
            },
            {"name": f"{Fields.published}.{Fields.published_content}", "type": "object", "optional": True},
            {
                "name": f"{Fields.published}.{Fields.published_content}.{Fields.child_usage_keys}",
                "type": "string[]",
                "optional": True,
            },
            {
                "name": f"{Fields.published}.{Fields.published_content}.{Fields.child_display_names}",
                "type": "string[]",
                "optional": True,
            },

            # sortable timestamps - float, not int64; see the module docstring
            {"name": Fields.created, "type": "float", "optional": True, "sort": True},
            {"name": Fields.modified, "type": "float", "optional": True, "sort": True},
            {"name": Fields.last_published, "type": "float", "optional": True, "sort": True},
            {
                "name": f"{Fields.last_published}{IS_NULL_SUFFIX}",
                "type": "bool",
                "facet": True,
                "optional": True,
            },

            # hierarchical tag facets
            {"name": Fields.tags, "type": "object", "optional": True},
            _tags_field(Fields.tags_taxonomy),
            _tags_field(Fields.tags_level0),
            _tags_field(Fields.tags_level1),
            _tags_field(Fields.tags_level2),
            _tags_field(Fields.tags_level3),

            # collections
            {"name": Fields.collections, "type": "object", "optional": True},
            {
                "name": f"{Fields.collections}.{Fields.collections_display_name}",
                "type": "string[]",
                "facet": True,
                "optional": True,
            },
            {
                "name": f"{Fields.collections}.{Fields.collections_key}",
                "type": "string[]",
                "facet": True,
                "optional": True,
            },

            # breadcrumbs
            {"name": Fields.breadcrumbs, "type": "object[]", "optional": True},
            {
                "name": f"{Fields.breadcrumbs}.{Fields.usage_key}",
                "type": "string[]",
                "facet": True,
                "optional": True,
            },
            {
                "name": f"{Fields.breadcrumbs}.display_name",
                "type": "string[]",
                "optional": True,
            },

            # containers this item belongs to
            {"name": Fields.units, "type": "object", "optional": True},
            {"name": Fields.subsections, "type": "object", "optional": True},
            {"name": Fields.sections, "type": "object", "optional": True},

            {"name": Fields.num_children, "type": "int32", "optional": True},
        ],
    }


# Meilisearch derives keyword-search priority from the *order* of its searchable
# attributes list. Typesense wants the order plus explicit descending weights.
QUERY_BY_FIELDS = [
    Fields.display_name,
    Fields.block_id,
    Fields.content,
    Fields.description,
    f"{Fields.tags}.{Fields.tags_taxonomy}",
    f"{Fields.tags}.{Fields.tags_level0}",
    f"{Fields.tags}.{Fields.tags_level1}",
    f"{Fields.tags}.{Fields.tags_level2}",
    f"{Fields.tags}.{Fields.tags_level3}",
    f"{Fields.collections}.{Fields.collections_display_name}",
    f"{Fields.collections}.{Fields.collections_key}",
    f"{Fields.published}.{Fields.published_display_name}",
    f"{Fields.published}.{Fields.published_description}",
]

# Descending, evenly spaced, highest first. Typesense allows 1..127.
QUERY_BY_WEIGHTS = [127 - (index * 9) for index in range(len(QUERY_BY_FIELDS))]
