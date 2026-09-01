"""
Can Typesense drive the Studio tag filter tree?

Replicates fetchAvailableTagOptions() from frontend-app-authoring's
search-manager against a real Typesense, using the same document shape
searchable_doc_tags() produces.
"""
import json, urllib.parse, urllib.request, urllib.error

BASE, KEY, NAME = "http://localhost:18112", "facetkey", "studio_content"  # override for your own instance
TAG_SEP = " > "
ok = fail = 0

def call(method, path, body=None, ct="application/json"):
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"X-TYPESENSE-API-KEY": KEY, "Content-Type": ct})
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read().decode()
            try:
                return r.status, json.loads(raw or "{}")
            except json.JSONDecodeError:
                # the import endpoint answers with NDJSON, one result per document
                return r.status, [json.loads(line) for line in raw.strip().split("\n") if line]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()

def check(label, cond, detail=""):
    global ok, fail
    if cond: ok += 1; print(f"  [PASS] {label}" + (f" -- {detail}" if detail else ""))
    else:    fail += 1; print(f"  [FAIL] {label} -- {detail}")

# ---------------------------------------------------------------- collection
call("DELETE", f"/collections/{NAME}")
schema = {
    "name": NAME, "enable_nested_fields": True,
    "fields": [
        {"name": ".*", "type": "auto"},
        {"name": "display_name", "type": "string", "optional": True},
        {"name": "block_type", "type": "string", "facet": True, "optional": True},
        {"name": "context_key", "type": "string", "facet": True, "optional": True},
        {"name": "tags", "type": "object", "optional": True},
        {"name": "tags.taxonomy", "type": "string[]", "facet": True, "optional": True},
        {"name": "tags.level0", "type": "string[]", "facet": True, "optional": True},
        {"name": "tags.level1", "type": "string[]", "facet": True, "optional": True},
        {"name": "tags.level2", "type": "string[]", "facet": True, "optional": True},
        {"name": "tags.level3", "type": "string[]", "facet": True, "optional": True},
    ],
}
st, body = call("POST", "/collections", schema)
check("collection with 5 hierarchical tag facets created", st == 201, str(body)[:150])

# --------------------------------------------------- documents (real shape)
def tagged(doc_id, block_type, name, tag_lineages):
    """tag_lineages: list of [taxonomy, l1, l2, ...] exactly as searchable_doc_tags builds them."""
    tags = {"taxonomy": [], "level0": [], "level1": [], "level2": [], "level3": []}
    for parts in tag_lineages:
        if parts[0] not in tags["taxonomy"]:
            tags["taxonomy"].append(parts[0])
        for level in range(4):
            value = TAG_SEP.join(parts[0:level + 2])
            key = f"level{level}"
            if value not in tags[key]:
                tags[key].append(value)
            if len(parts) == level + 2:
                break
    return {"id": doc_id, "block_type": block_type, "display_name": name,
            "context_key": "lib:Org1:LibA", "tags": tags}

# A taxonomy deliberately containing sibling values that share a prefix
# ("Canada" / "Canada Extra"), which is where a naive prefix match goes wrong.
docs = [
    tagged("d1", "html",    "Vancouver doc",  [["Location", "North America", "Canada", "Vancouver"]]),
    tagged("d2", "html",    "Toronto doc",    [["Location", "North America", "Canada", "Toronto"]]),
    tagged("d3", "problem", "CanadaExtra",    [["Location", "North America", "Canada Extra", "Nowhere"]]),
    tagged("d4", "html",    "Mexico doc",     [["Location", "North America", "Mexico"]]),
    tagged("d5", "problem", "Brazil doc",     [["Location", "South America", "Brazil"]]),
    tagged("d6", "html",    "Hard doc",       [["Difficulty", "Hard"]]),
    tagged("d7", "problem", "Hard+Vancouver", [["Difficulty", "Hard"], ["Location", "North America", "Canada", "Vancouver"]]),
    tagged("d8", "html",    "Easy doc",       [["Difficulty", "Easy"]]),
    tagged("d9", "video",   "Untagged",       []),
]
nd = "\n".join(json.dumps(d) for d in docs).encode()
st, _ = call("POST", f"/collections/{NAME}/documents/import?action=upsert", nd, ct="text/plain")
check(f"{len(docs)} tagged documents indexed", st == 200 and all(r["success"] for r in _), str(_)[:200])

# ------------------------------------------------- the MFE's query, ported
def facet_search(facet_name, facet_query=None, q="*", filter_by=None, num_typos=0, max_values=1000):
    params = {"q": q, "query_by": "display_name", "per_page": "0",
              "facet_by": facet_name, "max_facet_values": str(max_values)}
    if facet_query is not None:
        params["facet_query"] = f"{facet_name}:{facet_query}"
        params["facet_query_num_typos"] = str(num_typos)
    if filter_by:
        params["filter_by"] = filter_by
    qs = urllib.parse.urlencode(params)
    st, body = call("GET", f"/collections/{NAME}/documents/search?{qs}")
    if st != 200:
        return st, None
    counts = body["facet_counts"]
    hits = counts[0]["counts"] if counts else []
    return st, [(c["value"], c["count"]) for c in hits]

print("\n=== 1. root level: facet on tags.taxonomy (no parent) ===")
st, hits = facet_search("tags.taxonomy")
check("root taxonomies with counts", st == 200 and sorted(hits) == [("Difficulty", 3), ("Location", 6)], str(hits))

print("\n=== 2. expand a taxonomy: children via facet_query prefix ===")
st, hits = facet_search("tags.level0", facet_query="Location")
check("level0 under 'Location'", st == 200 and sorted(hits) == [
    ("Location > North America", 5), ("Location > South America", 1)], str(hits))

print("\n=== 3. deeper expand, and the shared-prefix trap ===")
st, hits = facet_search("tags.level1", facet_query="Location > North America")
check("level1 under 'Location > North America'", st == 200 and sorted(hits) == [
    ("Location > North America > Canada", 3),
    ("Location > North America > Canada Extra", 1),
    ("Location > North America > Mexico", 1)], str(hits))

st, hits = facet_search("tags.level2", facet_query="Location > North America > Canada")
# 'Canada Extra > Nowhere' shares the 'Canada' prefix - the MFE's exact-match
# post-processing is what removes it, and it is still required here.
raw = sorted(v for v, _ in hits)
exact = [v for v in raw if v.rsplit(TAG_SEP, 1)[0] == "Location > North America > Canada"]
check("prefix match pulls in the sibling 'Canada Extra'", 
      "Location > North America > Canada Extra > Nowhere" in raw, str(raw))
check("MFE's exact-parent post-processing removes it", sorted(exact) == [
    "Location > North America > Canada > Toronto",
    "Location > North America > Canada > Vancouver"], str(exact))

print("\n=== 4. typo tolerance is controllable (Meilisearch's is not) ===")
st, fuzzy = facet_search("tags.level0", facet_query="Locatoin", num_typos=2)
st, strict = facet_search("tags.level0", facet_query="Locatoin", num_typos=0)
check("facet_query_num_typos=2 matches a misspelt parent", len(fuzzy) > 0, str(fuzzy))
check("facet_query_num_typos=0 does not", len(strict) == 0, str(strict))

print("\n=== 5. hasChildren: facet the NEXT level down ===")
st, child_hits = facet_search("tags.level2", facet_query="Location > North America")
parents_with_children = {v.rsplit(TAG_SEP, 1)[0] for v, _ in child_hits}
check("Canada and Canada Extra have children, Mexico does not",
      "Location > North America > Canada" in parents_with_children
      and "Location > North America > Mexico" not in parents_with_children,
      str(sorted(parents_with_children)))

print("\n=== 6. tree respects the active search + filters ===")
st, hits = facet_search("tags.level0", facet_query="Location", filter_by="block_type:=`html`")
check("counts recomputed under a block_type filter",
      st == 200 and dict(hits).get("Location > North America") == 3, str(hits))
st, hits = facet_search("tags.taxonomy", q="Vancouver")
check("counts recomputed under a keyword search", st == 200 and dict(hits) == {"Location": 1}, str(hits))

print("\n=== 7. multi-select: several tag paths ANDed, across taxonomies ===")
def result_ids(filter_by):
    qs = urllib.parse.urlencode({"q": "*", "query_by": "display_name", "per_page": "50",
                                 "filter_by": filter_by})
    st, body = call("GET", f"/collections/{NAME}/documents/search?{qs}")
    return st, sorted(h["document"]["id"] for h in body.get("hits", []))

st, ids = result_ids('tags.level2:=`Location > North America > Canada > Vancouver`')
check("single deep tag selects its documents", ids == ["d1", "d7"], str(ids))

# The UI ANDs selections: "Difficulty > Hard" AND "…Canada > Vancouver"
st, ids = result_ids('tags.level0:=`Difficulty > Hard` && '
                     'tags.level2:=`Location > North America > Canada > Vancouver`')
check("two tags at different levels/taxonomies AND correctly", ids == ["d7"], str(ids))

# Selecting a parent and a child of the same branch
st, ids = result_ids('tags.level1:=`Location > North America > Canada` && '
                     'tags.level2:=`Location > North America > Canada > Toronto`')
check("parent AND child of the same branch", ids == ["d2"], str(ids))

# Mutually exclusive selections yield nothing, as the UI's AND semantics imply
st, ids = result_ids('tags.level0:=`Difficulty > Hard` && tags.level0:=`Difficulty > Easy`')
check("contradictory AND returns nothing", ids == [], str(ids))

print("\n=== 8. both tree requests in ONE round trip via multi_search ===")
searches = {"searches": [
    {"collection": NAME, "q": "*", "query_by": "display_name", "per_page": 0,
     "facet_by": "tags.level1", "facet_query": "tags.level1:Location > North America",
     "facet_query_num_typos": 0, "max_facet_values": 1000},
    {"collection": NAME, "q": "*", "query_by": "display_name", "per_page": 0,
     "facet_by": "tags.level2", "facet_query": "tags.level2:Location > North America",
     "facet_query_num_typos": 0, "max_facet_values": 1000},
]}
st, body = call("POST", "/multi_search", searches)
got = [[ (c["value"], c["count"]) for c in r["facet_counts"][0]["counts"] ] for r in body["results"]]
check("level + next-level facets fetched in a single request",
      st == 200 and len(got) == 2 and got[0] and got[1],
      f"level1={len(got[0])} values, level2={len(got[1])} values")

print("\n=== 9. the facet ceiling that drives 'mayBeMissingResults' ===")
many = [tagged(f"m{i}", "html", f"m{i}", [["Wide", f"Topic{i:03d}"]]) for i in range(300)]
call("POST", f"/collections/{NAME}/documents/import?action=upsert",
     "\n".join(json.dumps(d) for d in many).encode(), ct="text/plain")
st, hits = facet_search("tags.level0", facet_query="Wide", max_values=1000)
check("300 sibling tags returned in one request (Meilisearch caps at 100)",
      st == 200 and len(hits) == 300, f"{len(hits)} values")

print("\n" + "=" * 62)
print(f"{ok}/{ok + fail} checks passed")
raise SystemExit(1 if fail else 0)
