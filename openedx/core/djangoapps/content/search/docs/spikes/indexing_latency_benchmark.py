"""
Same-machine head-to-head: single-document indexing latency vs index size,
Meilisearch 1.53.1 against Typesense 30.2.

Both indexes are configured the way Studio configures its studio_content
index (openedx/core/djangoapps/content/search/index_config.py), and seeded
with documents shaped like searchable_doc_for_library_block() output, because
the cost being measured is a function of that configuration -- 20 filterable
attributes, 13 searchable, 4 sortable, a distinct attribute and sort-first
ranking rules.

Absolute numbers are machine-specific and NOT comparable with figures measured
elsewhere in the thread. The ratio between the two engines on one machine is
the point.
"""
import json, statistics, sys, time, urllib.error, urllib.parse, urllib.request

MEILI, MKEY = "http://localhost:17701", "benchkey_benchkey_benchkey"
TS, TKEY = "http://localhost:18113", "benchkey"
INDEX = "studio_content"

def req(url, method="GET", body=None, headers=None, timeout=600):
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    r = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        raw = resp.read().decode()
        try:
            return json.loads(raw or "{}")
        except json.JSONDecodeError:
            return [json.loads(l) for l in raw.strip().split("\n") if l]

def mh(extra=None):
    h = {"Authorization": f"Bearer {MKEY}", "Content-Type": "application/json"}
    h.update(extra or {})
    return h

def th(extra=None):
    h = {"X-TYPESENSE-API-KEY": TKEY, "Content-Type": "application/json"}
    h.update(extra or {})
    return h

# ---- the real Studio index configuration -------------------------------
FILTERABLE = ["block_id","block_type","context_key","usage_key","org","tags",
    "tags.taxonomy","tags.level0","tags.level1","tags.level2","tags.level3",
    "collections","collections.display_name","collections.key","type","access_id",
    "last_published","content.problem_types","publish_status","breadcrumbs.usage_key"]
SEARCHABLE = ["display_name","block_id","content","description","tags",
    "collections","tags.taxonomy","tags.level0","tags.level1","tags.level2",
    "tags.level3","collections.display_name","collections.key",
    "published.display_name","published.description"]
SORTABLE = ["display_name","created","modified","last_published"]
RANKING = ["sort","words","typo","proximity","attribute","exactness"]

def doc(i):
    """Shaped like searchable_doc_for_library_block()."""
    return {
        "id": f"blk{i}", "type": "library_block",
        "usage_key": f"lb:Org1:LibA:html:blk{i}", "block_id": f"blk{i}",
        "block_type": "html" if i % 3 else "problem",
        "display_name": f"Component {i} about photosynthesis",
        "description": "Plants converting light into chemical energy",
        "context_key": f"lib:Org1:Lib{i % 50}", "org": "Org1", "access_id": i % 500,
        "created": 1724889600.5 + i, "modified": 1735689600.5 + i,
        "last_published": 1735689600.5 if i % 4 else None,
        "publish_status": "published" if i % 4 else "never",
        "breadcrumbs": [{"display_name": "Library A"}],
        "content": {"html_content": f"body text for component {i} " * 8}
                   if i % 3 else
                   {"capa_content": f"problem text {i} " * 8,
                    "problem_types": ["multiplechoiceresponse"]},
        "collections": {"display_name": [f"Coll{i % 20}"], "key": [f"COL_{i % 20}"]},
        "tags": {"taxonomy": ["Subject"], "level0": [f"Subject > Topic{i % 30}"],
                 "level1": [f"Subject > Topic{i % 30} > Sub{i % 7}"],
                 "level2": [], "level3": []},
    }

# ---- Meilisearch -------------------------------------------------------
def meili_wait(uid):
    while True:
        t = req(f"{MEILI}/tasks/{uid}", headers=mh())
        if t["status"] in ("succeeded", "failed", "canceled"):
            return t
        time.sleep(0.05)

def meili_setup():
    try:
        meili_wait(req(f"{MEILI}/indexes/{INDEX}", "DELETE", headers=mh())["taskUid"])
    except urllib.error.HTTPError:
        pass
    meili_wait(req(f"{MEILI}/indexes", "POST", {"uid": INDEX, "primaryKey": "id"}, mh())["taskUid"])
    meili_wait(req(f"{MEILI}/indexes/{INDEX}/settings", "PATCH", {
        "distinctAttribute": "usage_key", "filterableAttributes": FILTERABLE,
        "searchableAttributes": SEARCHABLE, "sortableAttributes": SORTABLE,
        "rankingRules": RANKING}, mh())["taskUid"])

def meili_seed(docs):
    uid = req(f"{MEILI}/indexes/{INDEX}/documents", "POST", docs, mh())["taskUid"]
    t = meili_wait(uid)
    assert t["status"] == "succeeded", t
    return t

def meili_single(i):
    """create -> delete -> create, timing the final create, as the thread does."""
    d = doc(i)
    meili_wait(req(f"{MEILI}/indexes/{INDEX}/documents", "POST", [d], mh())["taskUid"])
    meili_wait(req(f"{MEILI}/indexes/{INDEX}/documents/{d['id']}", "DELETE", headers=mh())["taskUid"])
    start = time.perf_counter()
    meili_wait(req(f"{MEILI}/indexes/{INDEX}/documents", "POST", [d], mh())["taskUid"])
    return time.perf_counter() - start

def meili_count():
    return req(f"{MEILI}/indexes/{INDEX}/stats", headers=mh())["numberOfDocuments"]

# ---- Typesense ---------------------------------------------------------
def ts_setup():
    try:
        req(f"{TS}/collections/{INDEX}", "DELETE", headers=th())
    except urllib.error.HTTPError:
        pass
    fields = [{"name": ".*", "type": "auto"}]
    for f in ["usage_key","block_id","type","block_type","context_key","org","publish_status"]:
        fields.append({"name": f, "type": "string", "facet": True, "optional": True})
    fields += [
        {"name": "access_id", "type": "int64", "facet": True, "optional": True},
        {"name": "display_name", "type": "string", "optional": True, "sort": True},
        {"name": "description", "type": "string", "optional": True},
        {"name": "content", "type": "object", "optional": True},
        {"name": "content.problem_types", "type": "string[]", "facet": True, "optional": True},
        {"name": r"content\..*", "type": "string", "stem": True, "optional": True},
        {"name": "created", "type": "float", "optional": True, "sort": True},
        {"name": "modified", "type": "float", "optional": True, "sort": True},
        {"name": "last_published", "type": "float", "optional": True, "sort": True},
        {"name": "last_published__is_null", "type": "bool", "facet": True, "optional": True},
        {"name": "tags", "type": "object", "optional": True},
        {"name": "collections", "type": "object", "optional": True},
        {"name": "breadcrumbs", "type": "object[]", "optional": True},
        {"name": "breadcrumbs.usage_key", "type": "string[]", "facet": True, "optional": True},
    ]
    for lvl in ["taxonomy","level0","level1","level2","level3"]:
        fields.append({"name": f"tags.{lvl}", "type": "string[]", "facet": True, "optional": True})
    for sub in ["display_name","key"]:
        fields.append({"name": f"collections.{sub}", "type": "string[]", "facet": True, "optional": True})
    req(f"{TS}/collections", "POST",
        {"name": INDEX, "enable_nested_fields": True, "fields": fields}, th())

def ts_prep(d):
    d = dict(d)
    if d.get("last_published") is None:
        d.pop("last_published", None); d["last_published__is_null"] = True
    else:
        d["last_published__is_null"] = False
    return d

def ts_import(docs, action="upsert"):
    body = "\n".join(json.dumps(ts_prep(d)) for d in docs).encode()
    res = req(f"{TS}/collections/{INDEX}/documents/import?action={action}", "POST", body,
              th({"Content-Type": "text/plain"}))
    if isinstance(res, dict):  # a single-document import answers with one JSON object
        res = [res]
    bad = [r for r in res if not r.get("success")]
    assert not bad, bad[:2]

def ts_single(i):
    d = doc(i)
    ts_import([d])
    req(f"{TS}/collections/{INDEX}/documents/{d['id']}", "DELETE", headers=th())
    start = time.perf_counter()
    ts_import([d])
    return time.perf_counter() - start

def ts_count():
    return req(f"{TS}/collections/{INDEX}", headers=th())["num_documents"]

# ---- run ---------------------------------------------------------------
SIZES = [int(x) for x in (sys.argv[1].split(",") if len(sys.argv) > 1 else ["50000","150000","300000"])]
RUNS = 7
BATCH = 5000

print(f"sizes={SIZES} runs_per_size={RUNS}\n")
meili_setup(); ts_setup()
print(f"{'index size':>11} | {'Meilisearch 1.53.1':>19} | {'Typesense 30.2':>15} | {'ratio':>6}")
print("-" * 62)

seeded = 0
results = []
for target in SIZES:
    while seeded < target:
        chunk = [doc(i) for i in range(seeded, min(seeded + BATCH, target))]
        meili_seed(chunk); ts_import(chunk)
        seeded += len(chunk)
    mc, tc = meili_count(), ts_count()
    assert abs(mc - tc) <= 1, f"index sizes diverged: meili={mc} ts={tc}"

    m = statistics.median(meili_single(900_000_000 + r) for r in range(RUNS))
    t = statistics.median(ts_single(900_000_000 + r) for r in range(RUNS))
    results.append((mc, m, t))
    print(f"{mc:>11,} | {m*1000:>16.0f} ms | {t*1000:>12.0f} ms | {m/t:>5.1f}x")

print("\nbatch of 100 documents at the largest size:")
batch = [doc(950_000_000 + i) for i in range(100)]
start = time.perf_counter(); meili_seed(batch); mb = time.perf_counter() - start
start = time.perf_counter(); ts_import(batch); tb = time.perf_counter() - start
print(f"  Meilisearch {mb*1000:.0f} ms   Typesense {tb*1000:.0f} ms   ratio {mb/tb:.1f}x")
