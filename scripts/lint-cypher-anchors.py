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
REL = re.compile(r"\[\s*`?(\w*)`?\s*(?::\s*([\w|]+))?\s*(\*[\d.]*)?\s*(\{[^}]*\})?\s*\]")
# An id compared with a literal or a parameter (either side), or tested against a list.
# A join between two variables (m.id = y) is not a filter and is not flagged.
ID_FILTER = re.compile(
    r"(?<![\w).])[A-Za-z]\w*\.id\s*(?:=~|=\s*(?:''|\$|\w+\s*\()|IN\s*[\[$])"
    r"|(?:''|\$\w+)\s*=\s*[A-Za-z]\w*\.id\b", re.I)
# X.id = y / X.id IN y against a variable: a filter when y is an UNWIND value or a
# WITH-bound list, unless X is the far end of a hop from a bound node (then it is a join).
ID_VAR = re.compile(r"(?<![\w).])([A-Za-z]\w*)\.id\s*(?:=|IN)\s*([A-Za-z]\w*)\b(?!\s*[.(])", re.I)
TRAVERSES = re.compile(r"\)\s*<?-")
UNWIND_AS = re.compile(r"\bAS\s+(\w+)", re.I)
WORD = re.compile(r"\b\w+\b")

# Keys that reach an index. Node: the :Entity label+property indexes in the
# indexer's pkg/graphstore/memgraph/schema.go (they only apply with the :Entity label).
# Edge: its edge-property indexes, per edge type. Any other key is a filter applied
# after the scan, not an anchor.
NODE_KEYS = {"id", "subcategory", "lending_protocol", "protocol", "pool_protocol", "project",
             "market_kind", "vault_kind", "implementation", "source"}
EDGE_KEYS = {"LENDING_BORROW": {"market_id", "market", "debt_token"},
             "LENDING_COLLATERAL": {"market_id_hash", "ilk_bytes"},
             "AT_RISK": {"target_id"}}
# Every edge-property index, including ones too unselective to anchor on. A filter on one
# of these, on a hop that starts from an already-bound node, lets the planner serve the hop
# from the index and rebind that node (the indexer's docs/memgraph-scan-edge-type-rebind.md,
# "Tier B"): refused, or wrong rows.
EDGE_INDEXED = {t: set(k) for t, k in EDGE_KEYS.items()}
EDGE_INDEXED.setdefault("HOLDS", set()).add("usd_value")
EDGE_INDEXED.setdefault("POOL_ASSET", set()).add("protocol")


def keys(props):
    """Property-map keys, ignoring colons inside quoted values."""
    props = re.sub(r"'[^']*'|\"[^\"]*\"", "''", props or "")
    return [k.strip() for k in re.findall(r"(\w+)\s*:", props)]


def types(t):
    """Edge types named in a relationship pattern; None when it names none (any type)."""
    return set(t.split("|")) if t else None


def keyed(table, rtypes, ks):
    """Whether any key in ks is indexed on any of the edge types (the conservative side,
    for rebind warnings). An untyped hop may be any type."""
    pools = table.values() if rtypes is None else [table.get(t, ()) for t in rtypes]
    return any(k in pool for pool in pools for k in ks)


def anchors(table, rtypes, ks):
    """Whether ks reaches an index on EVERY edge type of the hop. An untyped hop, or an
    alternation with an unindexed arm, cannot seek on the key."""
    return bool(rtypes) and all(any(k in table.get(t, ()) for k in ks) for t in rtypes)


def coalesce_spans(text):
    """(start, end) of every coalesce(...) call, parentheses balanced."""
    spans = []
    for m in re.finditer(r"\bcoalesce\s*\(", text, re.I):
        depth, i = 1, m.end()
        while i < len(text) and depth:
            depth += {"(": 1, ")": -1}.get(text[i], 0)
            i += 1
        spans.append((m.start(), i))
    return spans


def split_patterns(body):
    """Split a MATCH body into its comma-separated patterns (commas at depth 0 only)."""
    out, depth, cur = [], 0, ""
    for ch in body:
        depth += ch in "([{"
        depth -= ch in ")]}"
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    return out + [cur]


def check(q):
    """Return a list of violations for one Cypher statement."""
    # Blank out string literals first: their contents are values, never syntax, and a
    # keyword or '//' inside one must not split a clause or start a comment.
    q = re.sub(r"'[^']*'|\"[^\"]*\"", "''", q)
    q = re.sub(r"//[^\n]*|/\*.*?\*/", "", q, flags=re.S)
    errs, bound, unwound, relvars, far = [], set(), set(), set(), set()
    parts = CLAUSE.split(q)
    clauses = [(re.sub(r"\s+", " ", parts[i].upper()), parts[i + 1]) for i in range(1, len(parts) - 1, 2)]
    unwind_lookup = False  # the previous clause was an UNWIND-fed id lookup with no traversal
    hop_rels = {}  # relationship variable -> edge types, on the last hop from a bound node
    for kw, body in clauses:
        if kw == "WHERE":
            for v, rt in hop_rels.items():
                wrapped = coalesce_spans(body)
                for m in re.finditer(r"\b%s\.(\w+)\b" % re.escape(v), body):
                    if any(a <= m.start() < b for a, b in wrapped):
                        continue
                    if keyed(EDGE_INDEXED, rt, [m.group(1)]):
                        errs.append("edge-index-rebind: %s.%s is edge-indexed and this hop starts from a "
                                    "bound node; wrap it in coalesce(%s.%s, ...) so the planner cannot "
                                    "rebind" % (v, m.group(1), v, m.group(1)))
            if relvars and re.search(r"\b(%s)\.graph_id\b" % "|".join(map(re.escape, relvars)), body):
                errs.append("rel-graph-id: never filter a relationship on graph_id")
            if ID_FILTER.search(body) or any(
                    x not in far and y in unwound for x, y in ID_VAR.findall(body)):
                errs.append("id-in-where: put the id in the node pattern, or UNWIND a list into it")
            continue
        hop_rels = {}
        if kw == "UNWIND":
            unwound.update(UNWIND_AS.findall(body))
            continue
        if kw == "WITH":
            aliases = set(UNWIND_AS.findall(body))
            unwound |= {a for a in aliases if re.search(r"\[[^\]]*\]\s+AS\s+%s\b" % a, body, re.I)}
            bound = (bound & set(WORD.findall(body.split(" WHERE ")[0]))) | aliases
            unwind_lookup = False
            continue
        if kw not in ("MATCH", "OPTIONAL MATCH"):
            continue
        fed_any = trav_any = False
        for pat in split_patterns(body):
            nodes = NODE.findall(pat)
            rels = [(v, types(t), props) for v, t, _, props in REL.findall(pat)]
            traverses = bool(TRAVERSES.search(pat))
            for var, label, props in nodes:
                if props and not label:
                    errs.append("unlabelled: (%s %s) needs :Entity" % (var, props))
            for _, _, props in rels:
                if "graph_id" in keys(props):
                    errs.append("rel-graph-id: never bind graph_id on a relationship")
            relvars.update(v for v, _, _ in rels if v)
            seeks = ["Entity" in re.split(r"\s*:\s*", label) and any(k in NODE_KEYS for k in keys(props))
                     for _, label, props in nodes]
            uses_bound = [bool(var) and var in bound for var, _, _ in nodes]
            rel_seek = any(anchors(EDGE_KEYS, rt, keys(props)) for _, rt, props in rels)
            if nodes and not (any(seeks) or any(uses_bound) or rel_seek):
                errs.append("graph-only: %s %s anchors on graph_id alone"
                            % (kw, (pat.strip().splitlines() or [""])[0]))
            if nodes and traverses and not seeks[0] and not uses_bound[0] and any(uses_bound[1:]):
                errs.append("bound-last: start the pattern at the already-bound node (and keep any "
                            "edge-indexed filter on that hop out of the index; see edge-index-rebind)")
            if any(uses_bound) and traverses:
                if any(keyed(EDGE_INDEXED, rt, keys(props)) for _, rt, props in rels):
                    errs.append("edge-index-rebind: an edge-indexed key in the relationship map of a hop "
                                "from a bound node lets the planner rebind it; filter in WHERE through "
                                "coalesce(...) instead")
                hop_rels.update({v: rt for v, rt, _ in rels if v})
                far.update(var for (var, _, _), b in zip(nodes, uses_bound) if var and not b)
            fed = any(re.search(r"\bid\s*:\s*%s\b" % re.escape(u), props or "")
                      for _, _, props in nodes for u in unwound)
            if traverses and (fed or unwind_lookup):
                errs.append("folded: put WITH <node> between the UNWIND-fed id lookup and the traversal")
            fed_any |= fed
            trav_any |= traverses
            if fed_any and trav_any and not (fed and traverses):
                errs.append("folded: put WITH <node> between the UNWIND-fed id lookup and the traversal")
            if any(seeks) or any(uses_bound) or rel_seek:
                bound.update(var for var, _, _ in nodes if var)
        unwind_lookup = fed_any and not trav_any
        bound.update(var for pat in split_patterns(body) for var, _, _ in NODE.findall(pat) if var)
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
    # follow-ups: the remaining false negatives and the id-join false positive
    ("MATCH (n:Entity {id:'x',graph_id:'g'}) "
     "OPTIONAL MATCH (a:Entity {graph_id:'g'})-[r:HOLDS]->(b:Entity {graph_id:'g'}) RETURN a", "graph-only"),
    ("MATCH (b:Entity {id:'x',graph_id:'g'}) WITH b "
     "MATCH (b), (a:Entity {graph_id:'g'})-[:ADMIN_CTRL]->(c:Entity {graph_id:'g'}) RETURN a", "graph-only"),
    ("UNWIND $x AS y MATCH (n:Entity {id:y,graph_id:'g'})-->(m:Entity {graph_id:'g'}) RETURN m", "folded"),
    ("MATCH (a:Entity {graph_id:'g'})-[r:HOLDS {market_id:'x'}]->(b:Entity {graph_id:'g'}) RETURN b", "graph-only"),
    ("MATCH (n:Token {id:'x', graph_id:'g'}) RETURN n", "graph-only"),
    ("MATCH (n:Entity {id:'x',graph_id:'g'})-[r:HOLDS]->(m:Entity {graph_id:'g'}) "
     "WHERE r.graph_id = 'g' RETURN m", "rel-graph-id"),
    ("MATCH (n:Entity {graph_id:'g'})-[r:HOLDS]->(m:Entity {graph_id:'g'}) WHERE '0x1' = n.id RETURN m", "id-in-where"),
    ("MATCH (n:Entity {graph_id:'g'}) where n.id in ['0x1'] RETURN n", "id-in-where"),
    ("UNWIND $x AS y MATCH (n:Entity {id:y, graph_id:'g'}) WITH n, y "
     "MATCH (n)-[r:HOLDS]->(m:Entity {graph_id:'g'}) WHERE m.id = y RETURN m", None),
    ("MATCH (t:Entity {id:'x',graph_id:'g'}) WITH t "
     "MATCH (t)<-[r:LENDING_BORROW]-(b:Entity {graph_id:'g'}) WHERE r.protocol = 'aave_v3' RETURN b", None),
    # follow-up review round
    ("UNWIND $x AS y MATCH (n:Entity {id:y, graph_id:'g'}), (n)-[:OWNS]->(m:Entity {graph_id:'g'}) RETURN m", "folded"),
    ("UNWIND ['0x1','0x2'] AS y MATCH (m:Entity {graph_id:'g', subcategory:'token'}) WHERE m.id = y RETURN m", "id-in-where"),
    ("WITH ['0x1'] AS ids MATCH (n:Entity {graph_id:'g', subcategory:'token'}) WHERE n.id IN ids RETURN n", "id-in-where"),
    ("MATCH (n:Entity {graph_id:'g', subcategory:'token'}) WHERE n.id = toLower($a) RETURN n", "id-in-where"),
    ("MATCH (n:Entity {graph_id:'g', subcategory:'token'}) WHERE n.id =~ '0x12.*' RETURN n", "id-in-where"),
    ("MATCH (a:Entity {graph_id:'g'})-[r {market_id:'x'}]->(b:Entity {graph_id:'g'}) RETURN b", "graph-only"),
    ("MATCH (a:Entity {graph_id:'g'})-[r:LENDING_BORROW|HOLDS {market_id:'x'}]->(b:Entity {graph_id:'g'}) RETURN b", "graph-only"),
    ("MATCH (n:EntityX {id:'x', graph_id:'g'}) RETURN n", "graph-only"),
    ("MATCH (t:Entity {id:'x',graph_id:'g'}) WITH t MATCH (t)<-[r:HOLDS]-(u:Entity {graph_id:'g'}) "
     "WHERE toFloat(r.usd_value) > 0 RETURN u", "edge-index-rebind"),
    ("MATCH (n:Entity {id:'x',graph_id:'g'}) OPTIONAL MATCH (n:Entity {graph_id:'g'})-[r:HOLDS]->(m:Entity {graph_id:'g'}) RETURN m", None),
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
