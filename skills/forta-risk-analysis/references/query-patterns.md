# Query patterns

Tested Cypher for the Forta Risk Graph connector. Prefer the typed tool named in each section; use these where no tool answers the question.

## How every query here is shaped

- **No `graph_id` anywhere.** The server injects its partition onto every node pattern you leave unanchored and rejects a different one, so writing it only hard-codes a partition id that changes. Never put it on a relationship: many edges carry no stamp, so the filter hides real edges and the empty result reads as an absence.
- **Every node pattern carries `:Entity`, and every query anchors on a selective key**: a node id in the pattern, several ids fed through `UNWIND`, or an indexed property such as `lending_protocol`. `read_cypher` checks the plan first and refuses one that reads the whole partition, and two shapes that look bounded do exactly that: an unlabelled pattern (`(n {id:'...'})`), which cannot use an index, and a list in `WHERE` (`WHERE n.id IN [...]`), which filters after the scan. Write `UNWIND [...] AS x MATCH (n:Entity {id:x})` instead: the same rows, one index seek per id, one call.
- **Put `WITH n` between an id lookup and the traversal from it**, as every multi-id query below does. Without it the planner folds the two into one clause, starts from the far side, and the query is refused although the ids are there.
- **Ids per call.** A plain lookup (one row per id) takes up to 50 ids. A traversal from the ids takes about 10, because one `LIMIT` is shared by every id: the rows it drops are the lower-ranked ones of every id, silently. If a traversal's row count reaches its `LIMIT`, split the batch and run it again.
- **Directed and single-typed.** Undirected or multi-type matches on high-degree nodes time out, and an undirected match also mixes a node's dependencies with its dependents.
- **`LIMIT` on everything, aggregates included, and above the row count you expect.** It is capped at 1000, and a higher value is rejected rather than clamped. A result that fills its `LIMIT` exactly is reported as truncated, even a one-row aggregate, so `LIMIT 1` on an aggregate reads as a lower bound.
- **Single-argument `round()` only**; `round(x, 2)` fails. So does a multi-branch `CASE` combined with wide aggregation.
- **Relationship types are checked before the query runs.** A name the vocabulary does not carry (`UPGRADE_CTRL` has never existed; `DEPENDS_ON` is a category) is rejected with the list of live types. The same check misreads a list or pattern comprehension containing a `:` (`[(n)<-[:DVN_VERIFIES]-(d) | d.id]`, `[x IN l WHERE x:Entity | x.id]`) and rejects it as an unknown type, although the type exists: write it as a `MATCH` or `OPTIONAL MATCH` with `collect` or `count`.
- **Project explicit fields.** A relationship dump on a high-degree node runs past 100k characters and consumes the context in one call. Never call bare `keys()` on a `lending_market` node, which carries 200+ properties; filter the list (`[k IN keys(m) WHERE k CONTAINS 'cap']`) or project named fields.
- **On a timeout, narrow the query** rather than retrying it unchanged. Two independent frontier queries in one message run in parallel and the guard tolerates it.

## Contents

- [Orientation](#orientation)
- [Value deployment](#deployment)
- [Wallets and Safes](#wallets)
- [Dependencies and roles](#dependencies)
- [Oracles](#oracles)
- [Backing and nested collateral](#backing)
- [Borrowers](#borrowers)
- [Control closure](#closure)
- [Concentration and shared components](#concentration)
- [Confirming a concept exists](#probing)
- [Per-borrower LTV and health (morpho_blue)](#borrowerltv)
- [Recursing outward from a vault (A3 depth)](#recurse)
- [Defensive borrowing inputs](#defensive)

---

<a name="orientation"></a>
## Orientation

**Resolve a node.** Project explicitly rather than calling `resolve_address` on anything high-degree:

```cypher
MATCH (n:Entity {id:'<entity>'})
RETURN n.id AS id, coalesce(n.primary_label,n.label,n.name) AS label, n.symbol AS symbol,
       n.category AS cat, n.subcategory AS sub, n.project AS project,
       n.lending_protocol AS proto, n.vault_kind AS vaultKind, n.usd_price AS price,
       n.total_supply_raw AS supply, n.decimals AS dec, n.is_proxy AS isProxy, n.proxy_type AS proxyType
```

**Which relationship types exist.** `available_traversals({nodeId})` gives exact counts and `absentTypes`. The Cypher equivalent, once per direction, where you also want the far side's subcategory:

```cypher
MATCH (n:Entity {id:'<entity>'})-[r]->(m:Entity)
RETURN type(r) AS rel, m.subcategory AS mcat, count(*) AS c
ORDER BY c DESC LIMIT 60
```

**What an edge carries today.** Properties are added to edges as the graph develops, and `keys()` is the only check that shows them:

```cypher
MATCH (n:Entity {id:'<entity>'})-[r:ORACLE_DEP]->(m:Entity)
RETURN m.id AS other, keys(r) AS edgeAttrs LIMIT 25
```

Edges whose `keys()` differ from their siblings carry extra meaning; investigate rather than assuming uniformity.

**A ticker to a node.** Use the resolver, not Cypher: no index covers `symbol` or `name`, so a Cypher lookup on either reads the whole partition and the plan check refuses it.

```
resolve_entities({query: "WBTC", subcategories: ["token"]})
```

Expect decoys: confirm the candidate against the canonical address before anchoring anything on it.

---

<a name="deployment"></a>
## Value deployment

**Vault NAV:** `get_denominator({nodeId})` first. The allocations themselves:

```cypher
MATCH (v:Entity {id:'<vault>'})-[r:VAULT_ALLOCATION]->(t:Entity)
RETURN t.id AS target, coalesce(t.primary_label,t.label,t.name) AS label, t.subcategory AS sub,
       r.allocated_usd AS usd, r.share_pct AS pct, r.market_key AS marketKey,
       r.adapter_address AS adapter, r.adapter_type AS adapterType,
       t.collateral_asset AS collat, t.loan_asset AS loan, t.lltv AS lltv
ORDER BY usd DESC LIMIT 50
```

Edges out of the vault are its allocations; edges into it are other entities allocating into it. Two checks on the result: `allocated_usd / share_pct` should be identical on every row (that value is total assets, with the A1 caveats on what agreement proves), and a `sum(share_pct)` below 1.0 is idle or unitemised deployment.

**Adapters, aggregated.** On a Morpho Vault V2 one adapter can route everything:

```cypher
MATCH (v:Entity {id:'<vault>'})-[r:VAULT_ALLOCATION]->(t:Entity)
RETURN r.adapter_address AS adapter, r.adapter_type AS type,
       count(*) AS markets, sum(toFloat(r.allocated_usd)) AS usd
ORDER BY usd DESC LIMIT 20
```

**Redemption capacity of a Morpho Vault V2**, two tiers (Mode A, A2). The instant, penalty-free tier is on the node. Absent is not zero, and it is not additive across vaults that designate the same market:

```cypher
MATCH (v:Entity {id:'<vault>'})
RETURN v.instant_liquidity_usd AS instantLiquidityUsd, v.instant_liquidity_basis AS basis,
       v.liquidity_market_key AS designatedMarket, v.liquidity_adapter_address AS liquidityAdapter,
       v.instant_liquidity_updated_at AS updatedAt, v.vault_kind AS vaultKind
```

The force-deallocatable tier (any holder, at the adapter's penalty) is not stored. This cross-check sums min(allocation, market liquidity) over the funded markets other than the designated one; the protocol's `forceDeallocatableLiquidity` is the figure to report (`protocol-crosschecks.md`):

```cypher
MATCH (v:Entity {id:'<vault>'})-[r:VAULT_ALLOCATION]->(m:Entity)
WHERE coalesce(m.market_key, '') <> coalesce(v.liquidity_market_key, '-')
  AND coalesce(toFloat(r.allocated_usd), 0) > 0
RETURN count(m) AS markets, count(m.available_liquidity_usd) AS marketsWithLiquidity,
       round(sum(CASE WHEN toFloat(r.allocated_usd) < toFloat(m.available_liquidity_usd)
                      THEN toFloat(r.allocated_usd) ELSE toFloat(m.available_liquidity_usd) END)) AS forceDeallocatableUsd
LIMIT 5
```

A market with no liquidity figure drops out of the sum, so `marketsWithLiquidity` below `markets` makes it a lower bound.

**Protocol-level supply.** Scope on `lending_protocol` and group by subcategory: on the Aave family the per-reserve stats sit on nodes typed `atoken`, so a `lending_market`-only filter misses nearly all of the protocol.

```cypher
MATCH (n:Entity {lending_protocol:'<protocol>'})
WHERE n.total_supplied_usd IS NOT NULL
RETURN n.subcategory AS sub, count(n) AS nodes,
       round(sum(toFloat(n.total_supplied_usd))) AS supplied,
       round(sum(toFloat(coalesce(n.total_borrowed_usd,0)))) AS borrowed
ORDER BY supplied DESC LIMIT 20
```

State which population the total covers; `get_schema`'s `total_supplied_usd` caveat explains why no such sum is custody.

---

<a name="wallets"></a>
## Wallets and Safes

**Everything a wallet holds.** A wallet is the target of `HOLDS`; the token is the source.

```cypher
MATCH (w:Entity {id:'<wallet>'})<-[r:HOLDS]-(t:Entity)
WHERE r.usd_value IS NOT NULL
RETURN t.id AS token, coalesce(t.primary_label,t.label,t.name) AS label, t.symbol AS sym,
       t.usd_price AS price, t.project AS project,
       r.usd_value AS usd, r.quantity_raw AS qty, r.updated_block AS blk,
       r.token_address AS tokenAddr, startNode(r).id AS startId
ORDER BY usd DESC LIMIT 50
```

`tokenAddr` and `startId` must both equal `token` (native ETH carries no `token_address`); if they equal the wallet, the direction is backwards and you are reading peer holdings. The balance is `quantity_raw` (`balance_raw` does not exist and returns null). The `usd_value` filter is load-bearing: unpriced rows sort first under `ORDER BY ... DESC`.

**Who controls a Safe:** `get_governance({nodeId})` first. For several at once, one index seek per id, up to 50 per call:

```cypher
UNWIND ['<safe1>','<safe2>'] AS safeId
MATCH (n:Entity {id:safeId})
RETURN n.id AS id,
       coalesce(n.safe_threshold, n.multisig_threshold) AS threshold,
       n.owner_labels AS ownerLabels, n.timelock_delay_seconds AS timelock,
       n.safe_probe_status AS probe, n.safe_probe_not_safe_reason AS probeReason,
       n.blockscout_name AS bsName, n.proxy_type AS proxyType, n.subcategory AS sub
```

`owner_labels` is a JSON string, so parse it. A threshold of 1 with a single owner and `safe_probe_status = 'ok'` is a confirmed single private key: say so in those words. A stored threshold or timelock of 0 can be a failed read rather than a value; `get_governance` says which.

**Signers shared across Safes:** the curated template, one call for up to 50 Safes, one row per signer sitting on more than one of them:

```
read_cypher({template: "signer_overlap_for_safes", params: {safeIds: ["<safe1>", "<safe2>"]}})
```

A Safe in no row either shares no signer with the others or has no `OWNS` edges at all. Tell the two apart with the owner count below before calling a Safe independent.

**Owners of one Safe.** Count distinct owners, never edges: different writers record the same ownership separately, so one owner can sit behind more than one `OWNS` edge.

```cypher
MATCH (s:Entity {id:'<safe>'})-[:OWNS]->(o:Entity)
RETURN count(DISTINCT o.id) AS owners, collect(DISTINCT o.id) AS ownerIds
```

Where governance keys are absent from the node entirely:

```cypher
MATCH (n:Entity {id:'<safe>'})
RETURN [k IN keys(n) WHERE k CONTAINS 'sign' OR k CONTAINS 'thresh' OR k CONTAINS 'owner'] AS govKeys,
       n.blockscout_name AS bsName, n.proxy_type AS proxyType,
       n.subcategory AS sub, n.is_verified AS verified
```

A verified contract named `SafeProxy` with `proxy_type = 'master_copy'` is a Safe, and a Safe always has owners and a threshold. Read them from a Safe UI or on-chain when the Safe is material to the conclusion.

**Residual approvals**, a real risk row on a wallet that appears nowhere else:

```cypher
MATCH (w:Entity {id:'<wallet>'})-[r:APPROVES]->(sp:Entity)
RETURN sp.id AS spender, coalesce(sp.primary_label,sp.label,sp.blockscout_name) AS lbl,
       r.token_contract AS token, r.is_unlimited AS unlimited,
       r.value AS rawValue, r.updated_block AS blk LIMIT 40
```

Compare each allowance against the position it relates to. An allowance larger than its position is worth flagging even when `is_unlimited` is false.

**Look-through on a held vault.** Size the wallet's slice as `holding_usd x share_pct`, using the allocations query above against the held vault; this never needs a share price.

**Supply sanity, before trusting any derived share price.** Both sides are raw units in the share token's decimals, so compare them directly:

```cypher
MATCH (t:Entity {id:'<vault>'})-[r:HOLDS]->(h:Entity)
WHERE r.quantity_raw IS NOT NULL
RETURN count(*) AS holders, sum(toFloat(r.quantity_raw)) AS heldRaw,
       toFloat(t.total_supply_raw) AS supplyRaw
LIMIT 5
```

If `heldRaw` exceeds `supplyRaw`, more shares are held than exist, so the supply figure is understated and any share price derived from it is inflated: report the position at `HOLDS.usd_value` instead. The second invariant: `total_supply_raw x usd_price` must never be below `sum(allocated_usd)`.

---

<a name="dependencies"></a>
## Dependencies and roles

**One hop out from a set of nodes.** Batch the source ids; the `src` column keeps the mapping straight:

```cypher
UNWIND ['<id1>','<id2>'] AS nId
MATCH (n:Entity {id:nId})
WITH n
MATCH (n)-[r:LENDING_COLLATERAL]->(d:Entity)
RETURN n.id AS src, d.id AS dep, coalesce(d.primary_label,d.label,d.name) AS label,
       d.subcategory AS sub, r.protocol AS protocol, r.usage_as_collateral_enabled AS pledgeable,
       r.total_collateral_usd AS collatUsd, r.ltv AS ltv,
       r.liquidation_threshold AS lt, r.liquidation_bonus AS bonus,
       r.emode_categories AS emode, r.last_checked_block AS checkedBlk
ORDER BY src LIMIT 80
```

Repeat per relationship type, one directed type per query, taking the set from the census rather than a list. `read_cypher` rejects a name the vocabulary does not carry, but a tool's relationship-type filter (`get_node_relationships`) does not check it and returns zero rows silently, which reads as absence.

**The admin set of a token:** `get_admin_risk` and `get_governance` first. In Cypher, read `ADMIN_OF` outgoing from the token. `ADMIN_CTRL` and `ADMIN_OF` record the same fact in opposite directions, never add them, and `ADMIN_OF` is the leg to count on, because `ADMIN_CTRL` carries parallel duplicates:

```cypher
MATCH (t:Entity {id:'<token>'})-[r:ADMIN_OF]->(a:Entity)
RETURN a.id AS admin, coalesce(a.primary_label,a.label,a.nametag) AS label,
       a.subcategory AS sub, collect(DISTINCT coalesce(r.role_name, r.role)) AS roles,
       a.is_contract AS isContract, coalesce(a.safe_threshold,a.multisig_threshold) AS threshold,
       a.max_key_value_usd AS keyValue
ORDER BY keyValue DESC LIMIT 60
```

**Roles from node properties**, which sometimes carry more than the edges, with the discovery status that classifies an empty set (Mode B, B6):

```cypher
UNWIND ['<id1>','<id2>'] AS nId
MATCH (n:Entity {id:nId})
RETURN n.id AS id, n.admin_roles AS rolesJson, n.max_key_value_usd AS maxKey,
       n.at_risk_admin_at_stake_usd AS adminAtStake, n.is_contract AS isContract,
       n.is_proxy AS isProxy, n.proxy_type AS proxyType, n.implementation AS impl,
       n.owner_discovery_status AS ownerStatus, n.role_member_discovery_status AS roleStatus,
       n.safe_probe_status AS probe, n.safe_probe_not_safe_reason AS probeReason
```

`admin_roles` is a JSON string mapping address to a list of role names. Parse it and reconcile it against the edge query, which carries the role on the relationship. `is_proxy = false` is not evidence of immutability: read `proxy_type` and the role set.

---

<a name="oracles"></a>
## Oracles

**The price authority.** Two edge markers answer two different questions, so read both. `pricing_authority = true` marks the oracle that prices a venue's collateral: on a Morpho market it is the market's own oracle, and that edge carries `value_defining = false` on purpose. `value_defining = true` marks an oracle that determines a token's own price (token and aToken consumers). Filtering on `value_defining` alone returns nothing on a Morpho market. `get_schema` describes only `value_defining`; `pricing_authority` is newer, and `keys()` on the edge shows both.

```cypher
UNWIND ['<market1>','<market2>'] AS mId
MATCH (m:Entity {id:mId})
WITH m
MATCH (m)-[r:ORACLE_DEP]->(o:Entity)
WHERE r.pricing_authority = true OR r.value_defining = true
RETURN m.id AS market, o.id AS oracle,
       coalesce(o.primary_label,o.label,o.nametag,'unlabelled') AS label,
       r.pricing_authority AS pricingAuthority, r.value_defining AS valueDefining,
       o.oracle_pair AS pair, r.market_id AS mktId, r.protocol AS protocol
ORDER BY market LIMIT 40
```

`market_id` is supporting evidence where present, never the filter: keying on it returns Morpho markets only and drops genuine price paths elsewhere. Both markers are thin, so on many subjects expect few rows or none. Both are edge properties. How the oracle itself prices (`oracle_pricing_type`) is on the oracle node at the far end, beside its `oracle_pricing_classify_*` attempt markers: an attempt with no type means it was tried and not classified, which is unknown, not a finding. Every position no marker resolves keeps its oracle row, worded as candidates not confirmed to a single feed, with its dollar figure.

**The feeds behind a market oracle**, with their pairs:

```cypher
UNWIND ['<oracle1>','<oracle2>'] AS oId
MATCH (o:Entity {id:oId})
WITH o
MATCH (o)-[r:ORACLE_DEP]->(f:Entity)
RETURN o.id AS marketOracle, f.id AS feed,
       coalesce(f.primary_label,f.label,f.nametag) AS feedLabel, f.subcategory AS sub,
       f.oracle_pair AS pair, f.oracle_pair_identified AS pairIdentified, r.value_defining AS vd
ORDER BY marketOracle LIMIT 40
```

`oracle_pair` is the feed's pair. `oracle_pair_identified: false` means no declarable pair, and absent means the feed was never consulted: a different claim. Never infer a pair from a feed's name. Report an unidentified feed by address with its impact figure rather than dropping it.

**Oracle admins over the whole feed set** is sound even where per-market attribution is not: collect the feed addresses, run the admin-set query above over them, and rank the admins by the value beneath them.

---

<a name="backing"></a>
## Backing and nested collateral

`get_backing({nodeId})` first: it separates real collateral legs from proof-of-reserve attestations and reports every layer with its count. For the custody side, `HOLDS` runs token to holder, and `r.token_address` always equals the source, which settles any doubt:

```cypher
MATCH (a:Entity {id:'<one side>'})-[r:HOLDS]->(b:Entity {id:'<other side>'})
RETURN a.id AS src, b.id AS dst, r.token_address AS tokenAddr, r.usd_value AS usd
```

If `token_address` equals `src`, then `dst` holds `src`. So what backs a token is **inbound** and its holders are **outbound**.

```cypher
// What a collateral token custodies: the underlying is the source.
UNWIND ['<collateral token>'] AS tId
MATCH (t:Entity {id:tId})
WITH t
MATCH (t)<-[r:HOLDS]-(u:Entity)
WHERE coalesce(toFloat(r.usd_value), 0) > 0
RETURN t.symbol AS collateral, u.id AS backing,
       coalesce(u.label,u.name) AS label, u.symbol AS bsym, r.usd_value AS usd
ORDER BY usd DESC LIMIT 40
```

The `coalesce` is load-bearing: `usd_value` is indexed on `HOLDS`, and a bare `r.usd_value > 0` after `WITH t` lets the planner answer the hop from that index instead of from `t`, which rebinds `t` and is refused.

The dominant row by USD is the backing; the tail is stray tokens transferred into the contract. `native HOLDS WETH` and `stETH HOLDS wstETH` both resolve this way. Known ids: native ETH is `native`, stETH is `0xae7ab96520de3a18e5e111b5eaab095312d7fe84`, eETH is `0x35fa164735182de50811e8e2e824cfb9b6118ac2`.

```cypher
// Holders of the entity: redemption-pressure concentration. Outbound.
MATCH (t:Entity {id:'<entity>'})-[r:HOLDS]->(h:Entity)
WHERE r.usd_value IS NOT NULL
RETURN h.id AS holder, coalesce(h.primary_label,h.label,h.symbol,h.blockscout_name) AS label,
       h.subcategory AS sub, h.project AS proj, r.usd_value AS usd
ORDER BY usd DESC LIMIT 30
```

Reading this backwards does not merely lose data; it invents dependencies, presenting a peer depositor's admin keys as control over your collateral.

Two more backing types, run separately rather than as one multi-type match:

```cypher
MATCH (b:Entity {id:'<token>'})-[r:RESERVE_BACKING]->(a:Entity)
RETURN a.id AS treasury, coalesce(a.label,a.name) AS label, a.symbol AS sym,
       endNode(r).id = startNode(r).id AS selfLoop LIMIT 20
```

Repeat with `:BRIDGE_BACKED_BY`, which lands on the lockbox or OFT adapter; the adapter's verifiers arrive on incoming `DVN_VERIFIES`. Both types are sparse and carry the same scam-token population as `HOLDS`, so filter by hand. Custodial arrangements behind a wrapped asset sit off-chain: name the custodian for what it is rather than treating its absence from the graph as a result.

Then resume the dependency walk from whatever you land on. Nested tokens have their own admin sets, invisible if you stop at the wrapper.

---

<a name="borrowers"></a>
## Borrowers

`get_concentration({nodeId: <market>, groupBy: "owner"})` first, and `get_levered_position({nodeId})` for one borrower. For raw rows, anchor on the node the borrow edge points at, which depends on the protocol (`get_schema`, `LENDING_BORROW`):

- **Aave v3 and its forks, Morpho Blue:** the reserve (loan) **token**, the same address the edge carries as `reserve_id`, never a market id. Morpho legs carry the market as `market_id`.
- **Euler v2:** the vault.
- **Aave v4, Compound v3, Maker:** the market node (the v4 reserve market, the Comet, the ilk market).

Filtering on `r.protocol` or `r.reserve_id` alone reads every borrow edge in the partition, which the plan check refuses.

```cypher
MATCH (t:Entity {id:'<reserve token address>'})<-[r:LENDING_BORROW]-(b:Entity)
WHERE r.debt_usd IS NOT NULL AND r.protocol = '<protocol>'
RETURN b.id AS borrower, b.category AS cat, b.subcategory AS sub,
       round(toFloat(r.debt_usd)) AS debtUsd, r.last_stat_block AS blk
ORDER BY debtUsd DESC LIMIT 30
```

An empty result here is never on its own evidence of no borrowers. It usually means the wrong id shape or a protocol with no per-borrower writer: settle it with `available_traversals` on the anchor and the market's `total_borrowed_usd` before writing anything down. On morpho_blue, anchor one market exactly on the edge's `market_id` instead (Per-borrower LTV below): several markets can share a loan and collateral pair.

**Shared control among the top borrowers.** Owner grouping merges sub-accounts only, so two borrower contracts run by one controller rank as two owners. Read the admins of the top-ranked borrower contracts; a controller over two or more of them makes their debt one exposure:

```cypher
UNWIND ['<borrower1>','<borrower2>','<borrower3>'] AS bId
MATCH (b:Entity {id:bId})
WITH b
MATCH (b)-[r:ADMIN_OF]->(a:Entity)
WITH a, collect(DISTINCT b.id) AS borrowers, collect(DISTINCT coalesce(r.role_name, r.role)) AS roles
RETURN a.id AS controller, coalesce(a.primary_label,a.label,a.nametag,a.blockscout_name) AS label,
       size(borrowers) AS nBorrowers, borrowers, roles
ORDER BY nBorrowers DESC LIMIT 20
```

Report a shared controller's combined share as its own row, labelled inferred from shared control.

**Owner grouping.** Rows a query returns are stamped by the server with `canonical_owner_id` and its basis, plus a `canonicalBorrowerOwners` block on the response: read the stamp rather than deriving anything. A query that aggregates inside Cypher cannot see the stamp, so group on the equivalent expression the server names in that block. The query below uses the 19-byte EVC owner prefix, which matches the server's `evc_subaccount_prefix_19_bytes` basis; if the server names a different expression, use that. Either way the error prevented is ranking on the raw borrower id, which splits one owner across its sub-accounts. The whole protocol in one call, so an owner whose sub-accounts borrow from different vaults stays one row:

```cypher
MATCH (m:Entity {lending_protocol:'euler_v2'})
WITH m
MATCH (m)<-[r:LENDING_BORROW]-(b:Entity)
WHERE r.protocol = 'euler_v2' AND coalesce(toFloat(r.debt_usd), 0) > 0
RETURN substring(b.id,0,40) AS ownerPrefix,
       count(DISTINCT b) AS subAccounts, count(DISTINCT m) AS vaults,
       round(sum(toFloat(r.debt_usd))) AS debtUsd
ORDER BY debtUsd DESC LIMIT 30
```

Positions with no price carry a null `debt_usd` and drop out of this ranking, so every share it produces is a lower bound. Count them before quoting one: `count(r)` against `count(r.debt_usd)` over the same match.

---

<a name="closure"></a>
## Control closure

**The protocol's own contracts** (B2). `project` is the right field here only because the question is branding:

```cypher
MATCH (n:Entity {project:'<brand>'})
WHERE n.subcategory IN ['contract','admin','vault','protocol']
RETURN n.id AS id, coalesce(n.primary_label,n.label,n.blockscout_name,n.nametag) AS label,
       n.subcategory AS sub, n.is_proxy AS isProxy,
       n.at_risk_admin_at_stake_usd AS adminAtStake, n.max_key_value_usd AS maxKey
LIMIT 60
```

**Bounded upward walk** (B3). Walk `ADMIN_CTRL` against its canonical direction. Parallel duplicate edges repeat paths, so deduplicate before the `LIMIT`:

```cypher
UNWIND ['<root1>','<root2>'] AS rootId
MATCH (root:Entity {id:rootId})
WITH root
MATCH p = (root)<-[:ADMIN_CTRL*1..4]-(ctrl:Entity)
WITH DISTINCT root.id AS rootId, [n IN nodes(p) | n.id] AS path, length(p) AS hops, ctrl
RETURN rootId, hops, path, ctrl.subcategory AS sub,
       coalesce(ctrl.primary_label,ctrl.label,ctrl.blockscout_name) AS lbl,
       coalesce(ctrl.safe_threshold, ctrl.multisig_threshold) AS thr,
       ctrl.is_contract AS isC, ctrl.safe_probe_status AS probe
ORDER BY hops LIMIT 70
```

Two or three roots per call. Depth 4 is safe; 5 starts timing out on dense roots. Exclude a known hub with `WHERE NOT '<hub>' IN [n IN nodes(p) | n.id]` before the `WITH`.

**Fan-out check, before expanding anything** (B4):

```cypher
UNWIND ['<frontier1>','<frontier2>'] AS bId
MATCH (b:Entity {id:bId})
WITH b
MATCH (b)<-[:ADMIN_CTRL]-(a:Entity)
RETURN b.id AS node, count(DISTINCT a.id) AS inboundAdmins,
       count(DISTINCT a.subcategory) AS distinctSubcats,
       collect(DISTINCT a.subcategory) AS subcats
ORDER BY inboundAdmins DESC LIMIT 30
```

More than about 50 inbound admins across 4 or more subcategories is a role registry, not a control set: record the degree, mark the branch unresolved, do not expand it. It returns one row per frontier id, so keep the frontier within its `LIMIT 30`.

---

<a name="concentration"></a>
## Concentration and shared components

**Supplier concentration**, who else supplies a market alongside the subject:

```cypher
UNWIND ['<market1>'] AS mId
MATCH (m:Entity {id:mId})
WITH m
MATCH (m)<-[r:VAULT_ALLOCATION]-(s:Entity)
RETURN m.id AS market, s.id AS supplier,
       coalesce(s.primary_label,s.label,s.symbol) AS label, r.allocated_usd AS usd
ORDER BY market, usd DESC LIMIT 120
```

A high share is a two-way risk: hard to exit without moving the market, and majority absorption of any bad debt. A market with exactly one non-zero supplier makes that supplier's loss the market's whole debt.

**Shared components across several nodes**, to test whether dependencies that look independent are:

```cypher
UNWIND ['<id1>','<id2>','<id3>'] AS nId
MATCH (n:Entity {id:nId})
WITH n
MATCH (n)<-[r:ADMIN_CTRL]-(f:Entity)
RETURN f.id AS shared, coalesce(f.primary_label,f.label,f.nametag,'unlabelled') AS label,
       f.subcategory AS sub, count(DISTINCT n.id) AS hitCount,
       collect(DISTINCT coalesce(r.role_name, r.role)) AS roles
ORDER BY hitCount DESC LIMIT 30
```

A shared upstream component (an admin, an aggregator, a custodian) is internal correlation, which is what you are looking for. A shared downstream consumer (another vault reading the same feed) is systemic contagion, a different finding. Distinguish the two explicitly.

---

<a name="probing"></a>
## Confirming a concept exists

Before describing something as outside the data, confirm it two ways, by node type and by relationship type: the concept may exist under a name you did not guess.

```cypher
UNWIND ['<guess1>','<guess2>'] AS guess
MATCH (n:Entity {subcategory:guess})
RETURN guess AS sub, count(n) AS c ORDER BY c DESC LIMIT 20
```

For relationship types, `get_schema({includeCensus: true})` gives exact partition-wide counts with sparse and deprecated flags, and `available_traversals({nodeId})` gives one node's present types plus a measured `absentTypes` list. Read the counts from those calls, never from a list written in a file: a type can be thin and entirely correct where it exists, so thin is a reason to check the population, not to skip the type.

---

<a name="borrowerltv"></a>
## Per-borrower LTV and health (morpho_blue)

For one borrower, `get_levered_position({nodeId})`: on morpho_blue its debt legs carry the protocol writer's per-position collateral, `positionLtv` and `healthFactor`, trust-filtered. For a whole market, read the borrow edges:

```cypher
MATCH (b:Entity)-[r:LENDING_BORROW {market_id:'<market_key>'}]->(t:Entity)
WHERE r.collateral_usd IS NOT NULL
RETURN b.id AS borrower, r.collateral_token AS collToken, r.collateral_usd AS collUsd,
       r.debt_usd AS debtUsd, 100.0*r.debt_usd/r.collateral_usd AS ltvPct,
       toFloat(r.lltv)/1e16 AS lltvPct, r.health_factor AS hf, r.last_stat_block AS blk
ORDER BY r.debt_usd DESC LIMIT 20
```

Report the LTV and the LLTV and let the gap between them speak. The drawdown that reaches liquidation is `1 - ltvPct/lltvPct`: keep it for your own reading and check it against the protocol's `priceVariationToLiquidationPrice` in A8, but do not give it a column.

**Verify `collateral_usd` before quoting it.** A degenerate collateral price produces a phantom shortfall that reads as insolvency:

```cypher
// implied unit price vs the usd_price on the collateral node
MATCH (b:Entity)-[r:LENDING_BORROW {market_id:'<market_key>'}]->(t:Entity)
WHERE r.collateral_usd IS NOT NULL
WITH r, toFloat(r.collateral_raw) AS craw
MATCH (c:Entity {id:r.collateral_token})
RETURN c.symbol AS sym, c.usd_price AS nodePrice, c.decimals AS dec,
       sum(r.collateral_usd) / (sum(craw) / 10.0^c.decimals) AS impliedPrice
```

Within a few percent is clean; orders of magnitude apart is a fabricated price. Also check the `last_stat_block` spread across legs you compare: an LTV mixing legs refreshed days apart is not a health factor.

---

<a name="recurse"></a>
## Recursing outward from a vault (A3 depth)

`dependency_closure({nodeId: <vault>, maxHops: 4})` gives the candidate set, collateral and base asset included. For the layer table, walk each layer directed and one type at a time.

```cypher
// hop 2: the collateral behind each funded market (incoming to the market)
MATCH (v:Entity {id:'<vault>'})-[a:VAULT_ALLOCATION]->(m:Entity)
WHERE a.allocated_usd > 1
WITH m, sum(a.allocated_usd) AS exposure
MATCH (m)<-[:LENDING_COLLATERAL]-(c:Entity)
RETURN c.id AS collId, c.symbol AS coll, collect(m.id) AS markets, sum(exposure) AS exposure
ORDER BY exposure DESC LIMIT 60
```

```cypher
// hop 3: one dependency type at a time from the collateral set, in its upstream direction
UNWIND ['<collateral1>','<collateral2>'] AS cId
MATCH (c:Entity {id:cId})
WITH c
MATCH (c)-[r:BACKED_BY]->(x:Entity)
RETURN c.symbol AS coll, x.id AS dep, coalesce(x.symbol,x.primary_label,x.blockscout_name) AS label,
       r.source AS source
LIMIT 60
```

Rerun the hop-3 query per type, in the direction that reaches what the collateral depends on: outgoing `ADMIN_OF` (its admins), `BACKED_BY`, `RESERVE_BACKING`, `BRIDGE_BACKED_BY`, `WRAP_UNWRAP`, `RECEIPT_FOR`, `DEBT_FOR`, `ORACLE_DEP` and `EXIT_VIA`, and incoming `HOLDS` (what it custodies). The opposite directions are the collateral's dependents (its holders, the markets and adapters that consume its price, wrappers built on it) and do not belong in this layer.

```cypher
// hop 4: the feed behind the price-authority oracle of each market
MATCH (v:Entity {id:'<vault>'})-[a:VAULT_ALLOCATION]->(m:Entity)
WHERE a.allocated_usd > 1
WITH m, a
MATCH (m)-[od:ORACLE_DEP]->(o:Entity)
WHERE od.pricing_authority = true OR od.value_defining = true
WITH m, a, o
MATCH (o)-[:ORACLE_DEP]->(f:Entity)
RETURN m.id AS mkt, a.allocated_usd AS alloc, o.id AS oracleId,
       coalesce(o.primary_label,o.blockscout_name,o.id) AS oracleName,
       f.id AS feedId, f.oracle_pair AS pair, coalesce(f.primary_label,f.blockscout_name,f.id) AS feed
ORDER BY alloc DESC LIMIT 200
```

A multi-market vault returns a large payload; parse it from a file rather than reading it into context. Then repeat the hop-3 pattern on each underlying until a branch yields nothing new. A typical chain runs six hops before it terminates: vault to market to a wrapped LST to its underlying to that underlying's backing contract. For the control chain above any node it reaches, use the bounded upward walk and fan-out guard in Control closure.

---

<a name="defensive"></a>
## Defensive borrowing inputs

Three reads size the rush for one market; `defensive-borrowing.md` says what the numbers mean.

**1. Market state.** Never call bare `keys()` on a `lending_market` node.

```cypher
MATCH (m:Entity {id:'<market_node_id>'})
RETURN m.collateral_asset AS collat, m.loan_asset AS loan, m.market_key AS marketKey,
       toFloat(m.lltv)/1e16                      AS lltvPct,
       round(toFloat(m.total_supplied_usd))      AS supplied,
       round(toFloat(m.total_borrowed_usd))      AS borrowed,
       round(toFloat(m.available_liquidity_usd)) AS liq,
       m.utilization_pct AS util, m.lending_protocol AS proto
```

Caps are not on the market node, and where they live depends on the protocol (`defensive-borrowing.md`). Morpho Blue has none. Aave v3 writes both caps onto the underlying token node, in whole tokens; they are Aave v3's own, so never apply them to a fork such as Spark, whose caps are read on-chain (`protocol-crosschecks.md`):

```cypher
UNWIND ['<underlying token>'] AS tId
MATCH (t:Entity {id:tId})
RETURN t.symbol AS sym, t.borrow_cap AS borrowCapTokens, t.supply_cap AS supplyCapTokens,
       t.borrow_cap_block AS borrowCapBlock, t.supply_cap_block AS supplyCapBlock
LIMIT 5
```

Compound v3's collateral supply cap is on the collateral's edge to its Comet:

```cypher
MATCH (c:Entity {id:'<collateral token>'})-[r:LENDING_COLLATERAL]->(m:Entity {id:'<comet>'})
RETURN r.protocol AS protocol, r.supply_cap AS supplyCapRaw, r.supply_cap_block AS capBlock,
       c.decimals AS collateralDecimals
```

**2. Collateral actually posted, and who posted it.** On morpho_blue, key one market exactly by the edge's `market_id` (the market's `market_key`). Anchoring on the loan token and filtering by collateral token merges every market that shares the pair, whatever its LLTV or oracle.

```cypher
MATCH (b:Entity)-[r:LENDING_BORROW {market_id:'<market_key>'}]->(t:Entity)
WHERE r.protocol = 'morpho_blue'
RETURN count(*)                              AS borrowers,
       round(sum(toFloat(r.debt_usd)))       AS debt,
       round(sum(toFloat(r.collateral_usd))) AS collat,
       round(max(toFloat(r.debt_usd)))       AS topDebt,
       collect(DISTINCT r.collateral_token)  AS collTokens,
       min(r.last_stat_block) AS oldestBlk, max(r.last_stat_block) AS newestBlk
LIMIT 5
```

On the Aave family, anchor on the reserve token as in Borrowers. Run the implied-price verifier (Per-borrower LTV) on `collat` before using it: a degenerate price inflates the collateral room.

**3. Can new collateral be posted without limit?** This decides whether the posted-collateral figure is a ceiling or merely a floor. A mint role in the set, with `max_key_value_usd` approaching the asset's market cap, means no limit: the attacker's collateral supply is whatever they mint.

```cypher
MATCH (a:Entity {id:'<asset>'})
RETURN a.admin_roles            AS roles,
       a.max_key_value_usd      AS maxKey,
       a.owner_discovery_status AS status,
       a.total_supply_raw AS supply, a.usd_price AS px, a.decimals AS dec
```

**A trap on Vault V2 anchors.** Filtering the vault's outbound `LENDING_COLLATERAL` by collateral token to find "the market where asset X is collateral" can return zero rows while the exposure is real: on an adapter-routed V2 vault the per-market split sits on outbound `VAULT_ALLOCATION` (carrying `market_key`, `adapter_address`, `share_pct`), and the `LENDING_COLLATERAL` set is sparse or holds only stray dust markets. Read the allocations, take the target market, then query the market node directly.
