# Query patterns for `risk-graph-rt-v3`

Tested Cypher for the Forta Risk Graph connector. Every **node** pattern binds
`graph_id:'risk-graph-rt-v3'`; no **relationship** pattern does, and none should — many edges carry
no partition stamp, so binding it there hides real edges and the empty result reads as an absence
(server instructions, rule 1). Confirm the partition id and the node-id conventions from the server
rather than from this line.

**Every node pattern carries the `:Entity` label, and every query anchors on a selective key** — a
node id written into the pattern, several ids fed through `UNWIND`, or an indexed property such as
`lending_protocol`. `read_cypher` checks the plan before it runs anything and refuses a query that
would read the whole partition, and two shapes that look bounded do exactly that:

- **An unlabelled pattern** — `(n {id:'…', graph_id:'…'})` — cannot use an index, so it plans a
  scan of every node however specific its properties are.
- **A list in `WHERE`** — `MATCH (n:Entity {graph_id:'…'}) WHERE n.id IN [...]` — seeks the
  partition on `graph_id` alone and filters afterwards. Write
  `UNWIND [...] AS x MATCH (n:Entity {id:x, graph_id:'…'})` instead: the same rows, one index seek
  per id. It is also one call where the list form invites one call per id.

**How many ids per call.** A plain lookup (one row per id, as in Safe governance) takes up to 50 ids,
the same cap as the `signer_overlap_for_safes` template. A traversal from the ids takes about 10:
not because the server refuses more, but because one `LIMIT` is shared by every id in the call. The
limit keeps the top rows by the sort key across all the ids together, so in a wide batch the rows it
drops are the lower-ranked ones of every id, silently. If a traversal's row count reaches its
`LIMIT`, split the batch and run it again. A traversal that returns one row per id (the fan-out
check below) is bounded by its `LIMIT` in ids instead.

When the listed nodes are then traversed, put `WITH n` between the id lookup and the traversal, as
every multi-id query below does. Without it the planner folds the two `MATCH` clauses into one and
starts from the far side or from the whole edge type, so the query is refused although the ids are
there.

## Contents

- [Orientation](#orientation)
- [Value deployment](#deployment)
- [Wallets and Safes](#wallets)
- [Dependencies](#dependencies)
- [Oracles](#oracles)
- [Backing and nested collateral](#backing)
- [Borrowers](#borrowers)
- [Control closure](#closure)
- [Concentration and shared components](#concentration)
- [Confirming a concept exists](#probing)
- [Per-borrower LTV and health (morpho_blue)](#borrowerltv)
- [Recursing outward from a vault (A3 depth)](#recurse)
- [Cross-checking against Morpho (A8)](#crosscheck)
- [Defensive borrowing inputs](#defensive)

---

<a name="orientation"></a>
## Orientation

**Resolve a node.** Project explicitly rather than calling `resolve_address` on anything
high-degree, where the payload can exceed 150k characters.

```cypher
MATCH (n:Entity {id:'<entity>', graph_id:'risk-graph-rt-v3'})
RETURN n.id AS id, coalesce(n.primary_label,n.label,n.name) AS label, n.symbol AS symbol,
       n.category AS cat, n.subcategory AS sub, n.project AS project,
       n.lending_protocol AS proto, n.usd_price AS price,
       n.total_supply_raw AS supply, n.is_proxy AS isProxy
```

**Map the structural vocabulary.** Run this first on anything new, once per direction. It is
cheap and tells you which relationship types exist before you pull rows.

```cypher
MATCH (n:Entity {id:'<entity>', graph_id:'risk-graph-rt-v3'})-[r]->(m:Entity {graph_id:'risk-graph-rt-v3'})
RETURN type(r) AS rel, r.subcategory AS sub, m.category AS cat,
       m.subcategory AS mcat, count(*) AS c
ORDER BY c DESC LIMIT 60
```

**Inspect edge attributes** when you need to know what an edge carries:

```cypher
MATCH (n:Entity {id:'<entity>', graph_id:'risk-graph-rt-v3'})-[r:ORACLE_DEP]->(m:Entity {graph_id:'risk-graph-rt-v3'})
RETURN m.id AS other, keys(r) AS edgeAttrs LIMIT 25
```

Edges whose `keys()` differ from their siblings carry extra meaning. Investigate rather than
assuming uniformity.

**Find a node from a ticker.** Use the resolver, not Cypher: no index covers `symbol` or `name`,
so a Cypher lookup on either reads the whole partition and the plan check refuses it.

```
resolve_entities({query: "WBTC", subcategories: ["token"]})
```

Expect decoys: confirm the candidate against the canonical address before anchoring anything on it.

---

<a name="deployment"></a>
## Value deployment

**Vault allocations into markets:**

```cypher
MATCH (v:Entity {id:'<vault>', graph_id:'risk-graph-rt-v3'})-[r]->(t:Entity {graph_id:'risk-graph-rt-v3'})
WHERE r.subcategory = 'VAULT_ALLOCATION'
RETURN t.id AS target, coalesce(t.label,t.name) AS label, t.subcategory AS sub,
       r.allocated_usd AS usd, r.share_pct AS pct, r.adapter_address AS adapter,
       r.adapter_type AS adapterType, t.collateral_asset AS collat,
       t.loan_asset AS loan, t.lltv AS lltv
ORDER BY usd DESC LIMIT 50
```

Direction matters: edges **out** of the vault are its allocations, edges **in** are other
entities allocating into it. Undirected matching mixes them, and the inbound rows are easy to
mistake for tiny allocations of your own.

Two checks on the result:

- `allocated_usd / share_pct` must be **identical on every row**. That value is the vault's
  total assets, and agreement is the strongest available confirmation of the denominator.
- `sum(share_pct)` below 1.0 means idle capital. Carry it as its own position, exposed to the
  vault contract and denomination asset but not to the markets.

**Adapters, aggregated.** For a Morpho Vault V2 one adapter can route everything:

```cypher
MATCH (v:Entity {id:'<vault>', graph_id:'risk-graph-rt-v3'})-[r]->(t:Entity {graph_id:'risk-graph-rt-v3'})
WHERE r.subcategory = 'VAULT_ALLOCATION'
RETURN DISTINCT r.adapter_address AS adapter, r.adapter_type AS type,
       count(*) AS markets, sum(toFloat(r.allocated_usd)) AS usd
ORDER BY usd DESC LIMIT 20
```

The summed `usd` is also a clean cross-check on the NAV denominator.

**Protocol-level supply.** Scope on `lending_protocol`:

```cypher
MATCH (n:Entity {graph_id:'risk-graph-rt-v3', lending_protocol:'<protocol>'})
WHERE n.subcategory = 'lending_market' AND n.total_supplied_usd IS NOT NULL
RETURN count(*) AS mkts, round(sum(toFloat(n.total_supplied_usd))) AS supplied,
       round(sum(toFloat(coalesce(n.total_borrowed_usd,0)))) AS borrowed
```

---

<a name="wallets"></a>
## Wallets and Safes

**Everything a wallet holds.** A wallet is the **target** of `HOLDS`; the token is the source.

```cypher
MATCH (w:Entity {id:'<wallet>', graph_id:'risk-graph-rt-v3'})<-[r:HOLDS]-(t:Entity {graph_id:'risk-graph-rt-v3'})
WHERE r.usd_value IS NOT NULL
RETURN t.id AS token, coalesce(t.label,t.name) AS label, t.symbol AS sym,
       t.usd_price AS price, t.project AS project,
       r.usd_value AS usd, r.quantity_raw AS qty, r.updated_block AS blk,
       r.token_address AS tokenAddr, startNode(r).id AS startId
ORDER BY usd DESC LIMIT 50
```

`tokenAddr` and `startId` must both equal `token`. If they equal the wallet, the direction is
backwards and you are reading peer holdings. The share balance is `quantity_raw`;
`balance_raw` does not exist and returns null. The `usd_value` filter is load-bearing, since
null-valued scam-token rows sort to the top under `ORDER BY usd DESC`.

**Safe governance. Run this on every multisig you reach, including the subject itself:**

```cypher
UNWIND ['<safe1>','<safe2>'] AS safeId
MATCH (n:Entity {id:safeId, graph_id:'risk-graph-rt-v3'})
RETURN n.id AS id,
       coalesce(n.safe_threshold, n.multisig_threshold) AS threshold,
       n.owner_labels AS ownerLabels, n.timelock_delay_seconds AS timelock,
       n.safe_probe_status AS probe, n.safe_probe_not_safe_reason AS probeReason,
       n.blockscout_name AS bsName, n.proxy_type AS proxyType, n.subcategory AS sub
```

Pass up to 50 Safes in one call; the list feeds one index seek per id.

`owner_labels` is a JSON **string**, so parse it. A threshold of 1 with a single owner and
`safe_probe_status = 'ok'` is a confirmed single private key: say so in those words.

**Signers shared across Safes.** Use the curated template: one call for up to 50 Safes, one row per
signer that sits on more than one of them.

```
read_cypher({template: "signer_overlap_for_safes", params: {safeIds: ["<safe1>", "<safe2>"]}})
```

A Safe that appears in no row either shares no signer with the others or has no `OWNS` edges at
all. Tell the two apart with the owner count below before calling a Safe independent.

**Owners of one Safe.** Count distinct owners, never edges:

```cypher
MATCH (s:Entity {id:'<safe>', graph_id:'risk-graph-rt-v3'})-[:OWNS]->(o:Entity {graph_id:'risk-graph-rt-v3'})
RETURN count(DISTINCT o.id) AS owners, collect(DISTINCT o.id) AS ownerIds
```

One owner can sit behind more than one `OWNS` edge, because different writers record the same
ownership separately, so `count(r)` overstates the signer set. The duplicates are a known defect being collapsed; until
that lands, `DISTINCT` on the owner id is the correct count, and it stays correct afterwards.

Where the governance keys are absent from the node entirely, confirm with:

```cypher
MATCH (n:Entity {id:'<safe>', graph_id:'risk-graph-rt-v3'})
RETURN [k IN keys(n) WHERE k CONTAINS 'sign' OR k CONTAINS 'thresh'
        OR k CONTAINS 'owner'] AS govKeys,
       n.blockscout_name AS bsName, n.proxy_type AS proxyType,
       n.subcategory AS sub, n.is_verified AS verified
```

A verified contract named `SafeProxy` with `proxy_type = 'master_copy'` is a Safe, and a Safe
always has owners and a threshold. Read them from a Safe UI or on-chain when the Safe is
material to the conclusion.

**Residual approvals.** A real risk row on a wallet that appears nowhere else:

```cypher
MATCH (w:Entity {id:'<wallet>', graph_id:'risk-graph-rt-v3'})-[r:APPROVES]->(sp:Entity {graph_id:'risk-graph-rt-v3'})
RETURN sp.id AS spender, coalesce(sp.primary_label,sp.label,sp.blockscout_name) AS lbl,
       r.token_contract AS token, r.is_unlimited AS unlimited,
       r.value AS rawValue, r.updated_block AS blk LIMIT 40
```

Compare each allowance against the position it relates to. An allowance larger than its
position is worth flagging even when `is_unlimited` is false.

**Look-through on a held vault.** Size the wallet's slice as `holding_usd x share_pct`; this
never needs a share price. Use the allocations query above against the held vault.

**Supply sanity, before trusting any derived share price:**

```cypher
MATCH (t:Entity {id:'<vault>', graph_id:'risk-graph-rt-v3'})-[r:HOLDS]->(h:Entity {graph_id:'risk-graph-rt-v3'})
WHERE r.quantity_raw IS NOT NULL
RETURN count(*) AS holders, sum(toFloat(r.quantity_raw))/1e18 AS sumShares
```

If `sumShares` exceeds `total_supply_raw`, more shares are held than exist, so the supply
figure is understated and any share price derived from it is inflated. Report the position at
`HOLDS.usd_value` instead. Second invariant, also one query: `total_supply_raw x usd_price`
must never be below `sum(allocated_usd)`, because a vault cannot lend out more than it owns.

---

<a name="dependencies"></a>
## Dependencies

**One hop out from a set of nodes.** Batch the source ids; the `src` column keeps the mapping
straight and it is far cheaper than one call per node.

```cypher
UNWIND ['<id1>','<id2>'] AS nId
MATCH (n:Entity {id:nId, graph_id:'risk-graph-rt-v3'})
WITH n
MATCH (n)-[r:LENDING_COLLATERAL]->(d:Entity {graph_id:'risk-graph-rt-v3'})
RETURN n.id AS src, d.id AS dep, coalesce(d.primary_label,d.label,d.name) AS label,
       d.subcategory AS sub, r.usage_as_collateral_enabled AS pledgeable,
       r.total_collateral_usd AS collatUsd, r.ltv AS ltv,
       r.liquidation_threshold AS lt, r.liquidation_bonus AS bonus,
       r.max_borrow_capacity_usd AS borrowCeiling
ORDER BY src LIMIT 80
```

Repeat per relationship type: `ORACLE_DEP`, `ADMIN_OF`, `ADMIN_CTRL`, `CUSTODY_VIA`,
`OWNS_ADMIN`, `CURATES`, `BACKED_BY`, `RESERVE_BACKING`, `BRIDGE_BACKED_BY`, `EXIT_VIA`, `RECEIPT_FOR`.
**`DEPENDS_ON` and `UPGRADE_CTRL` do not exist** — a category and an unimplemented name. Both return
zero rows silently, which reads as absence. One type per query keeps you inside the
cost guard; a multi-type `WHERE type(r) IN [...]` against high-degree nodes times out.

**Admin and control edges for a token:**

```cypher
MATCH (a:Entity {graph_id:'risk-graph-rt-v3'})-[r:ADMIN_CTRL]->(t:Entity {id:'<token>', graph_id:'risk-graph-rt-v3'})
RETURN DISTINCT a.id AS admin, coalesce(a.primary_label,a.label,a.nametag) AS label,
       a.subcategory AS sub, r.role AS role, a.is_contract AS isContract,
       coalesce(a.safe_threshold,a.multisig_threshold) AS threshold,
       a.max_key_value_usd AS keyValue
ORDER BY role LIMIT 60
```

`ADMIN_OF` mirrors `ADMIN_CTRL` with the same `role`, so one directed query on one type returns
the full set. Deduplicate by address for counting, and check any finding against how the
contract actually works.

**Roles from node properties**, which sometimes carry more than the edges:

```cypher
MATCH (n:Entity {id:'<asset>', graph_id:'risk-graph-rt-v3'})
RETURN n.admin_roles AS rolesJson, n.max_key_value_usd AS maxKey,
       n.at_risk_admin_at_stake_usd AS adminAtStake, n.is_proxy, n.proxy_type,
       n.implementation, n.timelock_delay_seconds AS timelock,
       n.owner_discovery_status AS ownerStatus,
       n.role_member_discovery_status AS roleStatus
```

`admin_roles` is a JSON **string** mapping address to a list of role names. Parse it and
reconcile against the edge query, which carries the role on the relationship.

---

<a name="oracles"></a>
## Oracles

**The market's price authority.** Filter on `value_defining = true`:

```cypher
UNWIND ['<market1>','<market2>'] AS mId
MATCH (m:Entity {id:mId, graph_id:'risk-graph-rt-v3'})
WITH m
MATCH (m)-[r:ORACLE_DEP]->(o:Entity {graph_id:'risk-graph-rt-v3'})
WHERE r.value_defining = true
RETURN m.id AS market, o.id AS oracle,
       coalesce(o.primary_label,o.label,o.nametag,'unlabelled') AS label,
       r.market_id AS mktId, r.protocol AS protocol
ORDER BY market LIMIT 40
```

`market_id` is supporting evidence where present, never the filter: keying on it returns Morpho
markets only and drops genuine price paths on other protocols. Read the marker's own coverage from
`get_schema`'s `value_defining` entry rather than from a figure written here — it is thin, and thin
enough that on many subjects you should expect few rows or none. On a **token** node the property
does not exist at all; use `oracle_pricing_type` there (server instructions, rule 8). Every position
the marker does not resolve keeps its oracle row, worded as candidates not confirmed to a single
feed, with its dollar figure.

**Upstream feeds behind a market oracle.** Match on label, since aggregator typing is
unreliable:

```cypher
UNWIND ['<oracle1>','<oracle2>'] AS oId
MATCH (o:Entity {id:oId, graph_id:'risk-graph-rt-v3'})
WITH o
MATCH (o)-[r:ORACLE_DEP]->(f:Entity {graph_id:'risk-graph-rt-v3'})
WHERE (coalesce(f.primary_label,f.label,f.nametag,'') CONTAINS 'Aggregator'
       OR coalesce(f.primary_label,f.label,'') CONTAINS 'Feed')
RETURN o.id AS marketOracle, f.id AS feed,
       coalesce(f.primary_label,f.label,f.nametag) AS feedLabel,
       f.subcategory AS sub, r.value_defining AS vd
ORDER BY marketOracle LIMIT 40
```

Feed nodes carry no pair identification, so name a pair only where the address justifies it and
label it as inferred. Report an unidentified feed by address with its impact figure rather than
dropping it.

**Oracle admins over the whole feed set** is sound even where per-market attribution is not:
collect the feed addresses, then run the `ADMIN_CTRL` query above over that set and rank the
admins by the value beneath them.

---

<a name="backing"></a>
## Backing and nested collateral

`HOLDS` runs **token to holder**, and `r.token_address` always equals the source, which settles
any doubt:

```cypher
MATCH (a:Entity {id:'<one side>', graph_id:'risk-graph-rt-v3'})-[r:HOLDS]->(b:Entity {id:'<other side>', graph_id:'risk-graph-rt-v3'})
RETURN a.id AS src, b.id AS dst, r.token_address AS tokenAddr, r.usd_value AS usd
```

If `token_address` equals `src`, then `dst` holds `src`. So **backing is inbound** and
**holders are outbound**.

```cypher
// BACKING of a collateral token. The underlying is the source.
UNWIND ['<collateral tokens>'] AS tId
MATCH (t:Entity {id:tId, graph_id:'risk-graph-rt-v3'})
WITH t
MATCH (t)<-[r:HOLDS]-(u:Entity {graph_id:'risk-graph-rt-v3'})
WHERE coalesce(toFloat(r.usd_value), 0) > 0
RETURN t.symbol AS collateral, u.id AS backing,
       coalesce(u.label,u.name) AS label, u.symbol AS bsym, r.usd_value AS usd
ORDER BY usd DESC LIMIT 40
```

The `coalesce` is load-bearing: `usd_value` is indexed on `HOLDS`, and a bare `r.usd_value > 0` after
`WITH t` lets the planner answer the hop from that index instead of from `t`, which rebinds `t` and
is refused. Wrapping the property keeps the filter off the index.

The dominant row by USD is the backing; the tail is stray tokens transferred into the contract.
`native HOLDS WETH` and `stETH HOLDS wstETH` both resolve this way. Known ids: native ETH is
`native`, stETH is `0xae7ab96520de3a18e5e111b5eaab095312d7fe84`, eETH is
`0x35fa164735182de50811e8e2e824cfb9b6118ac2`.

```cypher
// HOLDERS of the entity: redemption-pressure concentration. Outbound.
MATCH (t:Entity {id:'<entity>', graph_id:'risk-graph-rt-v3'})-[r:HOLDS]->(h:Entity {graph_id:'risk-graph-rt-v3'})
WHERE r.usd_value IS NOT NULL
RETURN h.id AS holder, coalesce(h.label,h.symbol,h.blockscout_name) AS label,
       h.subcategory AS sub, h.project AS proj, r.usd_value AS usd
ORDER BY usd DESC LIMIT 30
```

Reading this backwards does not merely lose data, it invents dependencies: it presents a peer
depositor's admin keys as control over your collateral.

Two more backing types, run separately rather than as one multi-type match:

```cypher
MATCH (a:Entity {graph_id:'risk-graph-rt-v3'})-[r:RESERVE_BACKING]->(b:Entity {id:'<token>', graph_id:'risk-graph-rt-v3'})
RETURN a.id AS backing, coalesce(a.label,a.name) AS label, a.symbol AS sym,
       startNode(r).id AS startId LIMIT 20
```

Repeat with `:BRIDGE_BACKED_BY`. Both are sparse and carry the same scam-token population as
`HOLDS`, so filter by hand. Custodial arrangements behind a wrapped asset sit off-chain: name
the custodian for what it is rather than treating its absence from the graph as a result.

Then resume the dependency walk from whatever you land on. Nested tokens have their own admin
sets, invisible if you stop at the wrapper.

---

<a name="borrowers"></a>
## Borrowers

Anchor on the node the borrow edge points at, which depends on the protocol:

- **Aave v3 and its forks, Morpho Blue:** the reserve (loan) **token**, the same address the edge
  carries as `reserve_id`, never a market id.
- **Euler v2:** the vault.
- **Aave v4, Compound v3, Maker:** the market node (the v4 reserve market, the Comet, the ilk market),
  whose id the Aave v4 and Compound v3 edges also carry as `market`. Anchoring on the token here
  returns zero rows.

Filtering on `r.protocol` or `r.reserve_id` alone reads every borrow edge in
the partition, which the plan check refuses.

```cypher
MATCH (t:Entity {id:'<reserve token address>', graph_id:'risk-graph-rt-v3'})<-[r:LENDING_BORROW]-(b:Entity {graph_id:'risk-graph-rt-v3'})
WHERE r.debt_usd IS NOT NULL AND r.protocol = '<protocol>'
RETURN b.id AS borrower, b.category AS cat, b.subcategory AS sub,
       round(toFloat(r.debt_usd)) AS debtUsd, r.last_stat_block AS blk
ORDER BY debtUsd DESC LIMIT 30
```

Zero rows means the wrong id shape or an uncovered protocol, not an absence of borrowers:
confirm which before writing anything down.

**Prefer `get_concentration({nodeId, groupBy:"owner"})` over the aggregate below.** It groups on the
server's own owner basis and refuses to publish a share or ranking over a population it could not
page in full, which the query below will happily do. Use the query only where the tool refuses and
you have narrowed the population enough that it pages completely.

**On owner grouping, note which of two forms applies.** A query that returns borrower **rows** is
stamped by the server with `canonical_owner_id` and `canonical_owner_id_basis` per row, plus a
`canonicalBorrowerOwners` block on the response — read that stamp rather than deriving anything. A
query that **aggregates inside Cypher**, like the one below, cannot see the stamp: it is served on
the response, not exposed to the query engine. For that case the server publishes the equivalent
expression in the same block; take it from there. Either way the error being prevented is ranking on
the raw borrower id, which splits one owner across its sub-accounts and under-reports its share:

The whole protocol in one call, so an owner whose sub-accounts borrow from different vaults stays one
row. It anchors on the indexed `lending_protocol` key. Euler's debt-token nodes carry the same key and
simply contribute no rows, so no type filter is needed; one would drop a vault whose type has not been
settled yet.

```cypher
MATCH (m:Entity {graph_id:'risk-graph-rt-v3', lending_protocol:'euler_v2'})
WITH m
MATCH (m)<-[r:LENDING_BORROW]-(b:Entity {graph_id:'risk-graph-rt-v3'})
WHERE r.protocol = 'euler_v2' AND coalesce(toFloat(r.debt_usd), 0) > 0
RETURN substring(b.id,0,40) AS ownerPrefix,
       count(DISTINCT b) AS subAccounts, count(DISTINCT m) AS vaults,
       round(sum(toFloat(r.debt_usd))) AS debtUsd
ORDER BY debtUsd DESC LIMIT 30
```

Positions in a vault with no price carry a null `debt_usd` and drop out of this ranking, so every
share it produces is a lower bound. Count them before quoting one: `count(r)` against
`count(r.debt_usd)` over the same match.


---

<a name="closure"></a>
## Control closure

**Bounded upward walk.** Walk `ADMIN_CTRL` against its canonical direction, which `get_schema` names:

```cypher
UNWIND $roots AS rootId
MATCH (root:Entity {id:rootId, graph_id:'risk-graph-rt-v3'})
WITH root
MATCH p = (root)<-[:ADMIN_CTRL*1..4]-(ctrl:Entity {graph_id:'risk-graph-rt-v3'})
RETURN length(p) AS hops, [n IN nodes(p) | n.id] AS path,
       ctrl.subcategory AS sub, coalesce(ctrl.primary_label,ctrl.label,ctrl.blockscout_name) AS lbl,
       coalesce(ctrl.safe_threshold, ctrl.multisig_threshold) AS thr,
       ctrl.is_contract AS isC, ctrl.safe_probe_status AS probe
ORDER BY hops LIMIT 70
```

Two or three roots per call. Depth 4 is safe, 5 starts timing out on dense roots. Exclude a
known hub with `NOT $hub IN [n IN nodes(p) | n.id]`.

**Fan-out check, before expanding anything:**

```cypher
UNWIND $frontier AS bId
MATCH (b:Entity {id:bId, graph_id:'risk-graph-rt-v3'})
WITH b
MATCH (b)<-[:ADMIN_CTRL]-(a:Entity {graph_id:'risk-graph-rt-v3'})
RETURN b.id, count(DISTINCT a.id) AS inboundAdmins,
       count(DISTINCT a.subcategory) AS distinctSubcats,
       collect(DISTINCT a.subcategory) AS subcats
ORDER BY inboundAdmins DESC LIMIT 30
```

More than about 50 inbound admins across 4 or more subcategories is a role registry, not a
control set. Record the degree, mark the branch unresolved, and do not expand it.

It returns one row per frontier id, so keep `$frontier` within its `LIMIT 30`; beyond that,
split.

---

<a name="concentration"></a>
## Concentration and shared components

**Supplier concentration** (who else supplies a market alongside the subject):

```cypher
UNWIND ['<markets>'] AS mId
MATCH (m:Entity {id:mId, graph_id:'risk-graph-rt-v3'})
WITH m
MATCH (m)<-[r]-(s:Entity {graph_id:'risk-graph-rt-v3'})
WHERE r.subcategory = 'VAULT_ALLOCATION'
RETURN m.id AS market, s.id AS supplier,
       coalesce(s.label,s.symbol) AS label, r.allocated_usd AS usd
ORDER BY market, usd DESC LIMIT 120
```

A high share is a two-way risk: hard to exit without moving the market, and majority absorption
of any bad debt.

**Shared components across several nodes.** Use this to test whether dependencies that look
independent actually are:

```cypher
UNWIND ['<id1>','<id2>','<id3>'] AS nId
MATCH (n:Entity {id:nId, graph_id:'risk-graph-rt-v3'})
WITH n
MATCH (n)<-[r:ADMIN_CTRL]-(f:Entity {graph_id:'risk-graph-rt-v3'})
RETURN f.id AS shared, coalesce(f.primary_label,f.label,f.nametag,'unlabelled') AS label,
       f.subcategory AS sub, count(DISTINCT n.id) AS hitCount,
       collect(DISTINCT r.role) AS roles
ORDER BY hitCount DESC LIMIT 30
```

Read the result carefully. A shared **upstream** component (an admin, an aggregator, a
custodian) is internal correlation and is what you are looking for. A shared **downstream**
consumer (another vault reading the same feed) is systemic contagion, a different finding.
Distinguish the two explicitly.

---

<a name="probing"></a>
## Confirming a concept exists

Before describing something as outside the data, confirm it two ways: by node type and by
relationship type. One probe alone is not enough, since the concept may exist under a name you
did not guess.

```cypher
UNWIND ['<guess1>','<guess2>'] AS guess
MATCH (n:Entity {graph_id:'risk-graph-rt-v3', subcategory:guess})
RETURN guess AS sub, count(n) AS c ORDER BY c DESC LIMIT 20
```

```cypher
MATCH (m:Entity {id:'<node>', graph_id:'risk-graph-rt-v3'})-[r]->(n:Entity {graph_id:'risk-graph-rt-v3'})
RETURN type(r) AS rel, count(*) AS c ORDER BY c DESC LIMIT 30
```

Relationship types available in this partition include `HOLDS`, `AT_RISK`, `ORACLE_DEP`,
`LENDING_COLLATERAL`, `LENDING_BORROW`, `DEBT_FOR`, `ADMIN_CTRL`, `ADMIN_OF`,
`CUSTODY_VIA`, `OWNS`, `OWNS_ADMIN`, `CURATES`, `VAULT_ASSET`,
`VAULT_ALLOCATION`, `POOL_ASSET`, `RECEIPT_FOR`, `RESERVE_BACKING`, `BRIDGE_BACKED_BY`,
`BACKED_BY`, `WRAP_UNWRAP`, `EXIT_VIA`, `AFFECTS`, `SERVICE_FOR`, `DEPLOYED_BY`, `APPROVES`.

**Do not trust any prose list of types, including this one.** Get the live census, which gives exact
counts and flags the deprecated and unpopulated types:

```
get_schema({includeCensus: true})
```

Read the counts from that call rather than from this file. Every partition-wide figure moves, and a
stale count is worse than none: some types sit thin enough that an empty traversal over them proves
nothing about the entity you queried, and only the census tells you which ones today. A type can
also be thin and entirely correct where it exists, so thin is a reason to check the population, not
a reason to skip the type.

For one node rather than the partition, `available_traversals({nodeId})` gives present types with
exact counts plus an **`absentTypes`** list, which is a measured absence rather than a guess.

---

<a name="borrowerltv"></a>
## Per-borrower LTV and health (morpho_blue)

`LENDING_BORROW` carries the borrower's own collateral on morpho_blue, so read the edge rather than
inferring a basis with `get_levered_position`, which is built for protocols that issue a receipt or
share token and reports `unresolved` where collateral is escrowed instead.

```cypher
MATCH (b:Entity {graph_id:'risk-graph-rt-v3'})-[r:LENDING_BORROW {market_id:'<market_key>'}]->(t:Entity {graph_id:'risk-graph-rt-v3'})
WHERE r.collateral_usd IS NOT NULL
RETURN b.id AS borrower, r.collateral_token AS collToken, r.collateral_usd AS collUsd,
       r.debt_usd AS debtUsd,
       100.0*r.debt_usd/r.collateral_usd AS ltvPct,
       toFloat(r.lltv)/1e16 AS lltvPct,
       r.last_stat_block AS blk
ORDER BY r.debt_usd DESC LIMIT 20
```

**Report the LTV and the LLTV, and let the gap between them speak.** The drawdown that reaches
liquidation is `1 - ltvPct/lltvPct`, and it used to be a column of its own. It is not, because a
third percentage derived from two already on the row asks the reader to work out which of the three
is the input. Keep it for your own reading, and check it against the protocol's own
`priceVariationToLiquidationPrice` in A8, but do not give it a column.

**Verify `collateral_usd` before quoting it.** A degenerate collateral price produces a phantom
shortfall that reads as insolvency:

```cypher
// implied unit price vs the collateral node's own usd_price
MATCH (b:Entity {graph_id:'risk-graph-rt-v3'})-[r:LENDING_BORROW {market_id:'<market_key>'}]->(t:Entity {graph_id:'risk-graph-rt-v3'})
WHERE r.collateral_usd IS NOT NULL
WITH r, toFloat(r.collateral_raw) AS craw
MATCH (c:Entity {id:r.collateral_token, graph_id:'risk-graph-rt-v3'})
RETURN c.symbol, c.usd_price AS nodePrice, c.decimals AS dec,
       sum(r.collateral_usd) / (sum(craw) / 10.0^c.decimals) AS impliedPrice
```

Within a few percent is clean. Orders of magnitude apart is fabricated: known degenerate assets are
sDeUSD and deUSD (1e-8), USR ($0.122), RLP ($0.147).

Also check `last_stat_block` spread across legs you compare. An LTV mixing legs refreshed days
apart is not a health factor.

---

<a name="recurse"></a>
## Recursing outward from a vault (A3 depth)

`dependency_closure` does **not** reach a vault's collateral: it walks `LENDING_COLLATERAL`
in its canonical direction only, so from a vault or market anchor it
reaches the oracles and not the collateral. Walk the layers yourself.

```cypher
// hop 2 collateral: INCOMING to the market
MATCH (v:Entity {graph_id:'risk-graph-rt-v3', id:'<vault>'})-[a:VAULT_ALLOCATION]->(m:Entity {graph_id:'risk-graph-rt-v3'})
WHERE a.allocated_usd > 1
MATCH (m)<-[:LENDING_COLLATERAL]-(c:Entity {graph_id:'risk-graph-rt-v3'})
WITH DISTINCT c, sum(a.allocated_usd) AS exposure
// hop 3: everything that collateral depends on, grouped by type and direction
MATCH (c)-[r]-(x:Entity {graph_id:'risk-graph-rt-v3'})
WHERE type(r) IN ['ADMIN_CTRL','ADMIN_OF','BACKED_BY','RESERVE_BACKING','BRIDGE_BACKED_BY',
                  'WRAP_UNWRAP','RECEIPT_FOR','HOLDS','ORACLE_DEP','EXIT_VIA','DEBT_FOR']
RETURN c.symbol AS coll, c.id AS collId, exposure, type(r) AS rel,
       startNode(r).id = c.id AS outbound, count(*) AS n,
       collect(DISTINCT coalesce(x.symbol,x.primary_label,x.blockscout_name,x.id))[0..5] AS examples
ORDER BY exposure DESC, n DESC LIMIT 60
```

```cypher
// hop 4: the feed behind the feed
MATCH (v:Entity {graph_id:'risk-graph-rt-v3', id:'<vault>'})-[a:VAULT_ALLOCATION]->(m:Entity {graph_id:'risk-graph-rt-v3'})
WHERE a.allocated_usd > 1
MATCH (m)-[od:ORACLE_DEP]->(o:Entity {graph_id:'risk-graph-rt-v3'}) WHERE od.value_defining = true
OPTIONAL MATCH (o)-[r2]-(x:Entity {graph_id:'risk-graph-rt-v3'})
WHERE type(r2) IN ['ORACLE_DEP','ADMIN_CTRL','ADMIN_OF','SERVICE_FOR','DEPLOYED_BY']
RETURN substring(m.id,14,10) AS mkt, a.allocated_usd AS alloc, o.id AS oracleId,
       coalesce(o.primary_label,o.blockscout_name,o.id) AS oracleName, type(r2) AS rel,
       startNode(r2).id = o.id AS outbound,
       coalesce(x.primary_label,x.blockscout_name,x.symbol,x.id) AS other, x.id AS otherId
ORDER BY alloc DESC LIMIT 400
```

The second query returns a large payload on a multi-market vault — well past 100k characters — so
parse it from a file rather than reading it into context. Then repeat the hop-3 pattern on each
underlying until a branch yields nothing new. A typical chain runs six hops before it terminates:
vault to market to a wrapped LST to its underlying to that underlying's backing contract. Walked
that way it surfaces a collateral token
controlled by a **single key** over 27.85% of NAV, a node named `DummyFeed` in a `value_defining`
price path for 22.34%, and three of five collateral assets with **zero** `EXIT_VIA` venues.

Use Mode B's B3 bounded `<-[:ADMIN_CTRL*1..4]-` walk and B4 fan-out guard for the control chain
above any of those nodes.

---

<a name="crosscheck"></a>
## Cross-checking against Morpho (A8)

See SKILL.md A8 for the checklist and the schema traps. The endpoint is
`https://blue-api.morpho.org/graphql`, no auth, and `app.morpho.org` is a JS shell with no data in
the HTML. Write the query to a file; shell quoting mangles inlined nested quotes.

```bash
curl -s -X POST https://blue-api.morpho.org/graphql \
  -H 'Content-Type: application/json' -d @/tmp/q.json | python3 -m json.tool
```

Introspect rather than guessing a field: `{ __type(name:"VaultV2"){ fields{ name } } }`.

<a name="defensive"></a>
## Defensive borrowing inputs

Three reads size the rush scenario for one market. See SKILL.md for what the
numbers mean; these are the queries that produce them.

**1. Market state, including both caps.** Never call `keys()` on a
`lending_market` node, they carry 200+ properties. A **null cap is the absence
of a cap**, not a cap of zero: coalescing it deletes the effect being measured.

```cypher
MATCH (m:Entity {id:'<market_node_id>', graph_id:'<PARTITION>'})
RETURN m.collateral_asset AS collat, m.loan_asset AS loan,
       toFloat(m.lltv)/1e16                     AS lltvPct,
       round(toFloat(m.total_supplied_usd))     AS supplied,
       round(toFloat(m.total_borrowed_usd))     AS borrowed,
       round(toFloat(m.available_liquidity_usd)) AS liq,
       m.utilization_pct AS util,
       m.borrow_cap_usd  AS borrowCap,   // null on Morpho Blue: no cap exists
       m.supply_cap_usd  AS supplyCap,
       m.lending_protocol AS proto
```

**2. Collateral actually posted, and who posted it.** `reserve_id` is the loan
**token** address, not a market id; passing a market id returns zero rows and
reads as "no borrowers".

```cypher
MATCH (t:Entity {id:'<loan_token_address>', graph_id:'<PARTITION>'})<-[r:LENDING_BORROW]-(b:Entity {graph_id:'<PARTITION>'})
WHERE r.protocol = 'morpho_blue'
  AND r.collateral_token = '<collateral_token_address>'
RETURN count(*)                              AS borrowers,
       round(sum(toFloat(r.debt_usd)))       AS debt,
       round(sum(toFloat(r.collateral_usd))) AS collat,
       round(max(toFloat(r.debt_usd)))       AS topDebt
```

Run the implied-price verifier on `collat` before using it:
`Σ collateral_usd / (Σ collateral_raw / 10^decimals)` against the collateral
node's `usd_price`. A degenerate price here inflates the collateral room.

**3. Can new collateral be posted without limit?** This decides whether the
posted-collateral figure is a ceiling or merely a floor. A mint role in the set,
with `max_key_value_usd` approaching the asset's market cap, means the answer is
no limit: the attacker's collateral supply is whatever they mint.

```cypher
MATCH (a:Entity {id:'<asset>', graph_id:'<PARTITION>'})
RETURN a.admin_roles            AS roles,      // JSON string, parse it
       a.max_key_value_usd      AS maxKey,
       a.owner_discovery_status AS status,      // 'ok' = the set was probed
       a.total_supply_raw AS supply, a.usd_price AS px, a.decimals AS dec
```

**A trap worth knowing on Vault V2 anchors.** Filtering the vault's outbound
`LENDING_COLLATERAL` by `collateral_token_address` to find "the market where
asset X is collateral" can return **zero rows** while the exposure is real: on
an adapter-routed V2 vault the per-market split sits on outbound
`VAULT_ALLOCATION` (carrying `market_key`, `adapter_address`, `share_pct`) and
the `LENDING_COLLATERAL` set is sparse or holds only stray dust markets. Read
the allocations, take `market_key`, then query the market node directly.

