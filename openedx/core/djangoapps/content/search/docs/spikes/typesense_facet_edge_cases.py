"""Edge cases: tag values containing characters that could break facet_query / filter_by."""
import json, urllib.parse, urllib.request, urllib.error
BASE, KEY, NAME = "http://localhost:18112", "facetkey", "edge_tags"
SEP = " > "
ok = fail = 0
def call(method, path, body=None, ct="application/json"):
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    req = urllib.request.Request(BASE+path, data=data, method=method,
                                 headers={"X-TYPESENSE-API-KEY": KEY, "Content-Type": ct})
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read().decode()
            try: return r.status, json.loads(raw or "{}")
            except json.JSONDecodeError:
                return r.status, [json.loads(l) for l in raw.strip().split("\n") if l]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
def check(label, cond, detail=""):
    global ok, fail
    if cond: ok+=1; print(f"  [PASS] {label}" + (f" -- {detail}" if detail else ""))
    else:    fail+=1; print(f"  [FAIL] {label} -- {detail}")

call("DELETE", f"/collections/{NAME}")
call("POST", "/collections", {"name": NAME, "enable_nested_fields": True, "fields": [
    {"name": ".*", "type": "auto"},
    {"name": "display_name", "type": "string", "optional": True},
    {"name": "tags", "type": "object", "optional": True},
    {"name": "tags.taxonomy", "type": "string[]", "facet": True, "optional": True},
    {"name": "tags.level0", "type": "string[]", "facet": True, "optional": True},
    {"name": "tags.level1", "type": "string[]", "facet": True, "optional": True},
    {"name": "tags.level2", "type": "string[]", "facet": True, "optional": True},
    {"name": "tags.level3", "type": "string[]", "facet": True, "optional": True}]})

def build(lineages):
    tags = {"taxonomy": [], "level0": [], "level1": [], "level2": [], "level3": []}
    for parts in lineages:
        if parts[0] not in tags["taxonomy"]: tags["taxonomy"].append(parts[0])
        for lvl in range(4):
            v = SEP.join(parts[0:lvl+2]); k = f"level{lvl}"
            if v not in tags[k]: tags[k].append(v)
            if len(parts) == lvl+2: break
    return tags

# Tag names taken from the kinds of thing course teams actually type.
docs = [
  {"id":"e1","display_name":"colon","tags":build([["Subject: Science","Physics: Classical","Newton's 3rd"]])},
  {"id":"e2","display_name":"backtick","tags":build([["Odd`Taxonomy","a`b"]])},
  {"id":"e3","display_name":"quotes","tags":build([['Say "Hi"','the "best" one']])},
  {"id":"e4","display_name":"amp","tags":build([["A && B","C || D"]])},
  {"id":"e5","display_name":"deep4","tags":build([["L0","L1","L2","L3","L4"]])},
  {"id":"e6","display_name":"unicode","tags":build([["Idiomas","Español","Nivel — A1"]])},
]
st,res = call("POST", f"/collections/{NAME}/documents/import?action=upsert",
              "\n".join(json.dumps(d) for d in docs).encode(), ct="text/plain")
check("documents with awkward tag names indexed", st==200 and all(r["success"] for r in res), str(res)[:200])

def facet(field, fq=None, filt=None, typos=0):
    p = {"q":"*","query_by":"display_name","per_page":"0","facet_by":field,"max_facet_values":"1000"}
    if fq is not None:
        p["facet_query"] = f"{field}:{fq}"; p["facet_query_num_typos"]=str(typos)
    if filt: p["filter_by"]=filt
    st,b = call("GET", f"/collections/{NAME}/documents/search?{urllib.parse.urlencode(p)}")
    if st!=200: return st, b
    c=b["facet_counts"]
    return st, [x["value"] for x in (c[0]["counts"] if c else [])]

def quote(v):  # the backend's filter renderer
    return "`" + str(v).replace("`","") + "`"

def results(filt):
    p={"q":"*","query_by":"display_name","per_page":"50","filter_by":filt}
    st,b=call("GET", f"/collections/{NAME}/documents/search?{urllib.parse.urlencode(p)}")
    if st!=200: return st,b
    return st, sorted(h["document"]["id"] for h in b.get("hits",[]))

print("\n=== facet_query when the tag value itself contains a colon ===")
st,v = facet("tags.level0", fq="Subject: Science")
check("facet_query parses past a colon inside the value", st==200 and v==["Subject: Science > Physics: Classical"], f"{st} {v}")

print("\n=== filter_by on awkward values ===")
st,ids = results(f'tags.level0:={quote("Subject: Science > Physics: Classical")}')
check("filter on a value containing colons", ids==["e1"], str(ids))
st,ids = results(f'tags.level0:={quote("A && B > C || D")}')
check("filter on a value containing && and ||", ids==["e4"], str(ids))
st,ids = results(f'tags.level0:={quote('Say "Hi" > the "best" one')}')
check("filter on a value containing double quotes", ids==["e3"], str(ids))
st,ids = results(f'tags.level0:={quote("Idiomas > Español")}')
check("filter on a non-ASCII value", ids==["e6"], str(ids))

print("\n=== backtick: the renderer strips it, and the round trip still matches ===")
st,ids = results(f'tags.level0:={quote("Odd`Taxonomy > a`b")}')
check("stripping the backtick still selects the document", ids==["e2"], f"ids={ids}")
st,ids = results('tags.level0:=`zzz nonsense`')
check("...and := has not become loose: an unrelated value matches nothing", ids==[], f"ids={ids}")

print("\n=== := is exact, not token-based (the correctness question) ===")
st,ids = results('tags.level0:=`Subject: Science > Physics: Classical`')
check("exact value selects only its own document", ids==["e1"], str(ids))

print("\n=== full 4-level depth ===")
st,v = facet("tags.level3", fq="L0 > L1 > L2 > L3")
check("level3 (the deepest supported) faceted", st==200 and v==["L0 > L1 > L2 > L3 > L4"], f"{st} {v}")
st,ids = results(f'tags.level3:={quote("L0 > L1 > L2 > L3 > L4")}')
check("filter at level3", ids==["e5"], str(ids))

print("\n" + "="*62); print(f"{ok}/{ok+fail} checks passed")
raise SystemExit(1 if fail else 0)
