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

``indexing_latency_benchmark.py``
    Single-document indexing latency against index size, Meilisearch vs
    Typesense on one machine, with both indexes configured the way
    ``index_config.py`` configures ``studio_content``. Written for
    openedx/openedx-platform#38993. Results on a 12-core workstation, median
    of 7, at a 500,000-document index: Meilisearch 1.12.8 1499 ms, Meilisearch
    1.53.1 1142 ms, Typesense 30.2 6 ms. Meilisearch's cost rises with index
    size (727 ms at 50K to 1142 ms at 500K); Typesense stays flat at 3-6 ms.
    Absolute numbers are machine-specific; the ratios within one run are the
    point.

Run the facet spikes against a local Typesense::

    docker run -d --rm -p 18112:8108 typesense/typesense:30.2 \
        --data-dir /tmp --api-key=facetkey --enable-cors
    python typesense_hierarchical_facets.py
