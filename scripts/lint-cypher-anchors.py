#!/usr/bin/env python3
"""Anchor lint for every ```cypher block under skills/.

read_cypher EXPLAINs a query before running it and refuses a plan that reads
the whole partition. Four shapes produce that plan while looking bounded, and a
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

This is a structural proxy for the server's plan gate, not the gate itself: it
cannot see planner statistics. Run new examples through read_cypher as well.
"""
import re
import sys
from pathlib import Path

FENCE = re.compile(r"```cypher\n(.*?)```", re.S)
CLAUSE = re.compile(r"\b(OPTIONAL MATCH|MATCH|WHERE|WITH|RETURN|UNWIND|ORDER BY)\b")
NODE = re.compile(r"\((\w*)(:\w+)?\s*(\{[^}]*\})?\s*\)")
REL_PROPS = re.compile(r"\[\w*:\w+\s*\{[^}]+\}\]")
ID_FILTER = re.compile(r"(?<![\w).])[a-z]\w*\.id\s*(=|IN\b)")
UNWIND_AS = re.compile(r"\bAS\s+(\w+)")


def keys(props):
    return [k.strip() for k in re.findall(r"(\w+)\s*:", props or "")]


def check(q):
    """Return a list of violations for one Cypher statement."""
    q = re.sub(r"//[^\n]*", "", q)
    errs, bound, unwound = [], set(), set()
    parts = CLAUSE.split(q)
    clauses = [(parts[i], parts[i + 1]) for i in range(1, len(parts) - 1, 2)]
    for kw, body in clauses:
        if kw == "UNWIND":
            unwound.update(UNWIND_AS.findall(body))
            continue
        if kw == "WHERE" and ID_FILTER.search(body):
            errs.append("id-in-where: put the id in the node pattern, or UNWIND a list into it")
        if kw not in ("MATCH", "OPTIONAL MATCH"):
            continue
        nodes = NODE.findall(body)
        for var, label, props in nodes:
            if props and not label:
                errs.append("unlabelled: (%s %s) needs :Entity" % (var, props))
        anchored = any(
            (var in bound and not props) or any(k != "graph_id" for k in keys(props))
            for var, _, props in nodes
        ) or bool(REL_PROPS.search(body))
        if kw == "MATCH" and not anchored:
            errs.append("graph-only: MATCH %s anchors on graph_id alone" % body.strip().splitlines()[0])
        if "-[" in body and any(
            re.search(r"\bid\s*:\s*%s\b" % re.escape(u), props or "")
            for _, _, props in nodes for u in unwound
        ):
            errs.append("folded: put WITH <node> between the UNWIND-fed id lookup and the traversal")
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
     "MATCH (c:Entity {graph_id:'g'})-[:LENDING_COLLATERAL]->(m) RETURN c", None),
    ("MATCH (a:Entity {id:'0x1', graph_id:'g'}) RETURN startNode(r).id = a.id AS outbound", None),
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
