#!/usr/bin/env python3
"""Anchor lint for every ```cypher block under skills/.

read_cypher EXPLAINs a query before running it and refuses a plan that reads
the whole partition. These shapes produce that plan while looking bounded, and a
reader copying an example cannot see the difference:

  unlabelled   (n {id:'...'})           no index applies without :Entity, so
                                        the plan is ScanAll over every node
  id-in-where  WHERE n.id IN [...]      seeks on graph_id alone, filters after
  graph-only   MATCH (a:Entity {graph_id:'...'})-[r]->(b:Entity {graph_id:'...'})
                                        nothing selective to seek on
  folded       UNWIND $ids AS x         the planner folds the id lookup into
               MATCH (n:Entity {id:x, ...})-[r]->(m)
                                        the traversal and starts from the far
                                        side; put WITH n between them
  bound-last   MATCH (a:Entity {graph_id:'...'})-[:T]->(b)   with b bound
                                        starts from a, over the partition or the
                                        edge type; write (b)<-[:T]-(a) instead
  rel-graph-id [r {graph_id:'...'}]     refused outright, and hides unstamped edges

Only keys that reach an index count as anchors (NODE_KEYS / EDGE_KEYS below).

This is a structural proxy for the server's plan gate, not the gate itself: it
cannot see planner statistics. Run new examples through read_cypher as well.
"""
import re
import sys
from pathlib import Path

FENCE = re.compile(r"```cypher[ \t]*\r?\n(.*?)```", re.S | re.I)
CLAUSE = re.compile(r"\b(OPTIONAL\s+MATCH|MATCH|WHERE|WITH|RETURN|UNWIND|ORDER\s+BY)\b", re.I)
NODE = re.compile(r"\(\s*`?(\w*)`?\s*((?::\s*\w+)*)\s*(\{[^}]*\})?\s*\)")
REL = re.compile(r"\[\s*(\w*)\s*(?::\s*[\w|]+)?\s*(\*[\d.]*)?\s*(\{[^}]*\})?\s*\]")
ID_FILTER = re.compile(r"(?<![\w).])[A-Za-z]\w*\.id\s*(=|IN\b)", re.I)
UNWIND_AS = re.compile(r"\bAS\s+(\w+)", re.I)
WORD = re.compile(r"\b\w+\b")

# Keys that reach an index. Node: the :Entity label+property indexes in the
# indexer's pkg/graphstore/memgraph/schema.go. Edge: its edge-property indexes.
# Any other key is a filter applied after the scan, not an anchor.
NODE_KEYS = {"id", "subcategory", "lending_protocol", "protocol", "pool_protocol", "project",
             "market_kind", "vault_kind", "implementation", "source"}
EDGE_KEYS = {"market_id", "market", "debt_token", "market_id_hash", "ilk_bytes", "target_id"}
# Every edge-property index, including ones too unselective to anchor on (HOLDS.usd_value).
# A filter on one of these, on a hop that starts from an already-bound node, lets the
# planner serve the hop from the index and rebind that node (the indexer's
# docs/memgraph-scan-edge-type-rebind.md, "Tier B"): refused, or wrong rows.
EDGE_INDEXED = EDGE_KEYS | {"usd_value"}  # (POOL_ASSET.protocol is indexed too, on no other type)


def keys(props):
    """Property-map keys, ignoring colons inside quoted values."""
    props = re.sub(r"'[^']*'|\"[^\"]*\"", "''", props or "")
    return [k.strip() for k in re.findall(r"(\w+)\s*:", props)]


def check(q):
    """Return a list of violations for one Cypher statement."""
    # Blank out string literals first: their contents are values, never syntax, and a
    # keyword or '//' inside one must not split a clause or start a comment.
    q = re.sub(r"'[^']*'|\"[^\"]*\"", "''", q)
    q = re.sub(r"//[^\n]*|/\*.*?\*/", "", q, flags=re.S)
    errs, bound, unwound = [], set(), set()
    parts = CLAUSE.split(q)
    clauses = [(re.sub(r"\s+", " ", parts[i].upper()), parts[i + 1]) for i in range(1, len(parts) - 1, 2)]
    unwind_lookup = False  # the previous clause was an UNWIND-fed id lookup with no traversal
    hop_rels = set()  # relationship variables of the last MATCH that expands from a bound node
    for kw, body in clauses:
        if kw == "WHERE" and hop_rels:
            m = re.search(r"(?<!\()\b(%s)\.(%s)\b" % ("|".join(map(re.escape, hop_rels)),
                                                     "|".join(sorted(EDGE_INDEXED))), body)
            if m:
                errs.append("edge-index-rebind: %s.%s is edge-indexed and this hop starts from a bound "
                            "node; wrap it in coalesce(%s.%s, ...) so the planner cannot rebind"
                            % (m.group(1), m.group(2), m.group(1), m.group(2)))
        if kw != "WHERE":
            hop_rels = set()
        if kw == "UNWIND":
            unwound.update(UNWIND_AS.findall(body))
            continue
        if kw == "WITH":
            aliases = set(UNWIND_AS.findall(body))
            bound = (bound & set(WORD.findall(body.split(" WHERE ")[0]))) | aliases
            unwind_lookup = False
            continue
        if kw == "WHERE" and ID_FILTER.search(body):
            errs.append("id-in-where: put the id in the node pattern, or UNWIND a list into it")
        if kw not in ("MATCH", "OPTIONAL MATCH"):
            continue
        nodes = NODE.findall(body)
        rels = REL.findall(body)
        traverses = "-[" in body or ")--(" in body
        for var, label, props in nodes:
            if props and not label:
                errs.append("unlabelled: (%s %s) needs :Entity" % (var, props))
        for _, _, props in rels:
            if "graph_id" in keys(props):
                errs.append("rel-graph-id: never bind graph_id on a relationship")
        seeks = [any(k in NODE_KEYS for k in keys(props)) for _, _, props in nodes]
        uses_bound = [var in bound and not props for var, _, props in nodes]
        rel_seek = any(k in EDGE_KEYS for _, _, props in rels for k in keys(props))
        if not (any(seeks) or any(uses_bound) or rel_seek) and (kw == "MATCH" or not bound):
            errs.append("graph-only: %s %s anchors on graph_id alone" % (kw, (body.strip().splitlines() or [""])[0]))
        if nodes and traverses and not seeks[0] and not uses_bound[0] and any(uses_bound[1:]):
            errs.append("bound-last: start the pattern at the already-bound node (and keep any "
                        "edge-indexed filter on that hop out of the index; see edge-index-rebind)")
        if any(uses_bound) and traverses:
            if any(k in EDGE_INDEXED for _, _, props in rels for k in keys(props)):
                errs.append("edge-index-rebind: an edge-indexed key in the relationship map of a hop "
                            "from a bound node lets the planner rebind it; filter in WHERE through "
                            "coalesce(...) instead")
            hop_rels = {v for v, _, _ in rels if v}
        fed = any(re.search(r"\bid\s*:\s*%s\b" % re.escape(u), props or "")
                  for _, _, props in nodes for u in unwound)
        if traverses and (fed or unwind_lookup):
            errs.append("folded: put WITH <node> between the UNWIND-fed id lookup and the traversal")
        unwind_lookup = fed and not traverses
        bound.update(var for var, _, _ in nodes if var)
    return errs


SELF_TEST = [
    ("MATCH (n {id:'0x1', graph_id:'g'}) RETURN n", "unlabelled"),
    ("MATCH (n:Entity {graph_id:'g'}) WHERE n.id IN ['0x1'] RETURN n", "id-in-where"),
    ("MATCH (a:Entity {graph_id:'g'})-[r:HOLDS]->(b:Entity {graph_id:'g'}) WHERE a.id = '0x1' RETURN b", "id-in-where"),
    ("MATCH (a:Entity {graph_id:'g'})-[r:HOLDS]->(b:Entity {graph_id:'g'}) RETURN b", "graph-only"),
    ("UNWIND $ids AS x MATCH (n:Entity {id:x, graph_id:'g'})-[r:OWNS]->(o:Entity {graph_id:'g'}) RETURN o", "folded"),
    ("MATCH (n:Entity {id:'0x1', graph_id:'g'}) RETURN n", None),
    ("UNWIND $ids AS x MATCH (n:Entity {id:x, graph_id:'g'}) WITH n MATCH (n)-[r:OWNS]->(o:Entity {graph_id:'g'}) RETURN o", None),
    ("MATCH (n:Entity {graph_id:'g', lending_protocol:'aave_v3'}) RETURN count(*)", None),
    ("MATCH (b:Entity {graph_id:'g'})-[r:LENDING_BORROW {market_id:'0xab'}]->(t:Entity {graph_id:'g'}) RETURN b", None),
    ("MATCH (v:Entity {id:'0x1', graph_id:'g'})-[a:VAULT_ALLOCATION]->(m:Entity {graph_id:'g'}) "
     "MATCH (c:Entity {graph_id:'g'})-[:LENDING_COLLATERAL]->(m) RETURN c", "bound-last"),
    ("MATCH (a:Entity {id:'0x1', graph_id:'g'}) RETURN startNode(r).id = a.id AS outbound", None),
    # review round: shapes the first version let through
    ("UNWIND $ids AS x MATCH (b:Entity {id:x, graph_id:'g'}) WITH b "
     "MATCH (a:Entity {graph_id:'g'})-[:ADMIN_CTRL]->(b) RETURN a", "bound-last"),
    ("UNWIND $ids AS x MATCH (b:Entity {id:x, graph_id:'g'}) WITH b "
     "MATCH (b)<-[:ADMIN_CTRL]-(a:Entity {graph_id:'g'}) RETURN a", None),
    ("MATCH ()-[r:LENDING_BORROW {graph_id:'g'}]->() RETURN r", "rel-graph-id"),
    ("MATCH (a:Entity {graph_id:'g'})-[r:HOLDS {token_address:'0x1'}]->(b:Entity {graph_id:'g'}) RETURN b", "graph-only"),
    ("UNWIND $ids AS x MATCH (n:Entity {id:x, graph_id:'g'}) "
     "MATCH (n)-[r:OWNS]->(o:Entity {graph_id:'g'}) RETURN o", "folded"),
    ("MATCH (n:Entity {graph_id:'g', symbol:'WBTC'}) RETURN n", "graph-only"),
    ("OPTIONAL MATCH (a:Entity {graph_id:'g'})-[r:HOLDS]->(b:Entity {graph_id:'g'}) RETURN b", "graph-only"),
    ("MATCH (a:Entity {id:'x', graph_id:'g'}) WITH 1 AS one "
     "MATCH (a)-[r:HOLDS]->(b:Entity {graph_id:'g'}) RETURN b", "graph-only"),
    ("match (n {graph_id:'g'}) return n", "unlabelled"),
    ("MATCH (n :Entity:Token {id:'x', graph_id:'g'}) RETURN n", None),
    ("MATCH (n:Entity {id:'https://x', graph_id:'a:b'}) RETURN n", None),
    # final review: Tier B, and string contents that looked like syntax
    ("UNWIND $ids AS x MATCH (t:Entity {id:x, graph_id:'g'}) WITH t "
     "MATCH (t)<-[r:HOLDS]-(u:Entity {graph_id:'g'}) WHERE r.usd_value > 0 RETURN u", "edge-index-rebind"),
    ("UNWIND $ids AS x MATCH (t:Entity {id:x, graph_id:'g'}) WITH t "
     "MATCH (t)<-[r:HOLDS]-(u:Entity {graph_id:'g'}) WHERE coalesce(toFloat(r.usd_value), 0) > 0 RETURN u", None),
    ("MATCH (t:Entity {id:'x', graph_id:'g'}) WITH t "
     "MATCH (t)<-[r:AT_RISK {target_id:'y'}]-(a:Entity {graph_id:'g'}) RETURN a", "edge-index-rebind"),
    ("MATCH (n:Entity {id:'x', graph_id:'g'}) WHERE n.label = 'match with where' RETURN n", None),
    ("MATCH (n:Entity {id:'x', graph_id:'g'}) WHERE n.name CONTAINS 'Match' RETURN n", None),
]


def self_test():
    bad = 0
    for q, want in SELF_TEST:
        got = check(q)
        ok = (not got) if want is None else any(e.startswith(want) for e in got)
        if not ok:
            print("  FAIL  self-test: want %s, got %s\n        %s" % (want or "clean", got or "clean", q))
            bad = 1
    return bad


def main():
    root = Path(__file__).resolve().parent.parent
    fail = self_test()
    n = 0
    for f in sorted(root.glob("skills/**/*.md")):
        text = f.read_text()
        for m in FENCE.finditer(text):
            n += 1
            line = text.count("\n", 0, m.start()) + 2
            for e in check(m.group(1)):
                print("  FAIL  %s:%d  %s" % (f.relative_to(root), line, e))
                fail = 1
    print("%s — %d cypher block(s) checked" % ("FAILED" if fail else "OK", n))
    return fail


if __name__ == "__main__":
    sys.exit(main())
