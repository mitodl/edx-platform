Search backend spikes
#####################

Throwaway-but-kept scripts that answer a specific feasibility question by
running it against a real search engine, rather than reasoning from API docs.
They are not part of the test suite: they need a live server and they prove
design questions, not code correctness.

``typesense_hierarchical_facets.py``
    Can Typesense drive the Studio tag filter tree? Replicates
    ``fetchAvailableTagOptions()`` from frontend-app-authoring's
    ``search-manager`` against the document shape ``searchable_doc_tags()``
    produces: hierarchical facets, lazy child expansion, ``hasChildren``
    look-ahead, counts under an active query and filters, and multi-select
    tag paths. 18 checks.

``typesense_facet_edge_cases.py``
    The awkward tag values -- names containing ``:``, ``&&``/``||``, double
    quotes, backticks and non-ASCII -- plus the full four-level depth, and
    confirmation that ``:=`` matches exactly where ``:`` does not (selecting
    "Canada" must not also select "Canada Extra"). 11 checks.

Run either against a local Typesense::

    docker run -d --rm -p 18112:8108 typesense/typesense:30.2 \
        --data-dir /tmp --api-key=facetkey --enable-cors
    python typesense_hierarchical_facets.py
