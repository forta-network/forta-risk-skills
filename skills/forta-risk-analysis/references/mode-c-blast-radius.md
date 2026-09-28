# Mode C: blast radius

**The question:** if this thing is compromised, who loses money, and how much?

Queries are in `query-patterns.md`. Any market where the subject is collateral also needs `defensive-borrowing.md`.

## C1. Pick the sub-mode first. Picking wrong fails silently

| The user gives you | Sub-mode | Question answered |
|---|---|---|
| A dependency only | **Ecosystem-wide** (C3) | Rank everyone who loses. "What breaks if this asset fails?" |
| A dependency **and** a specific vault, wallet, Safe, protocol or address | **Targeted** (C4) | One number for that one party, plus the chain that carries it. "What does this asset's failure cost this vault?" |

Targeted is not ecosystem-wide filtered to one row. Two failure modes, both silent:

- **The floor is anchored to the wrong denominator.** Ecosystem-wide prunes at a fraction of the subject's size. A $2M path is noise against a multi-billion asset and gets pruned at hop 2, yet it can be 100% of the target's portfolio. The answer is discarded before the traversal reaches the target, and the output looks complete.
- **The frontier explodes before it arrives.** A major asset has hundreds of markets, thousands of pools and thousands of borrowers at hop 1. Expanding that outward to depth 4 will not survive the cost guard, so you never get deep enough to find a target six hops away.

So targeted mode traverses from the target inward toward the dependency, the direction most dependency edges already point, with the frontier bounded by the target's own portfolio. C2 applies to both; then take C3 or C4.

## C2. Direction, and why the obvious fields cannot answer this

**`AT_RISK` cannot answer this question**, even though `at_stake_usd` looks like the metric you want. Those edges run failure surface to focus token and are in practice inbound only on any asset worth analysing. The type answers "what can hurt this token", which is Mode A; there is no outbound `at_stake_usd` to read. On inbound holder edges `at_stake_usd` is still useful as a cross-check: for a holder, exposure and amount at stake are numerically the same as `HOLDS.usd_value`.

**Precomputed impact and cascade node fields are neighbourhood aggregates, not directional ones.** They span both directions and overlapping paths, so their sum runs above the subject's own size and includes nodes the subject cannot damage. Orientation only; never a report figure.

**Take canonical direction from `get_schema`, every time.** This table does not repeat it. It carries what the schema cannot: for this question, which way to walk relative to canonical, and what fraction to size the hop with. Walking from the dependent party toward the thing it depends on, almost everything runs with canonical, and the exceptions are what bite:

| Edge | Toward the dependency | Fraction |
|---|---|---|
| `HOLDS` | **against canonical**, match on the holder | `usd_value` / target NAV |
| `LENDING_COLLATERAL` | **against canonical**, from a market to its collateral | see C4 |
| `CURATES` | **against canonical**, to find your curator | 1.0 |
| `VAULT_ALLOCATION` | with canonical | `share_pct`, `allocated_usd` |
| `VAULT_ASSET` | with canonical | denomination, 1.0 |
| `ORACLE_DEP` | with canonical | 1.0 where the edge is marked `pricing_authority` (a market's price source) or `value_defining` (a token's own price). Both are edge properties; how the oracle itself prices is `oracle_pricing_type` on the oracle node |
| `RECEIPT_FOR` | with canonical | 1.0 |
| `WRAP_UNWRAP` | with canonical | 1.0; a backing edge despite its category. Check its population in the census |
| `BACKED_BY` | with canonical | impaired backing / total backing |
| `RESERVE_BACKING` | with canonical; it lands on a **treasury address**, not a backing asset | not a fraction |
| `BRIDGE_BACKED_BY` | with canonical | 1.0 on the wrapped supply |
| `DEBT_FOR` | with canonical | 1.0; its target may be the asset or the market, and `get_schema` says how to tell |
| `SUBORDINATE_TO` | with canonical | first-loss: the junior absorbs first |
| `POOL_ASSET` | with canonical | 1.0 for an LP, **not** the token's weight |
| `ADMIN_OF` | with canonical | 1.0, or `extractable_usd` |
| `CUSTODY_VIA` | with canonical | 1.0; the hub is a super-node |
| `LENDING_BORROW` | with canonical, debt side | see C4: the sign flips |

Three traps in that table:

- **`LENDING_COLLATERAL` walked the wrong way does not error; it answers a different question.** For a supplier vault, the collateral behind its loans is reached against canonical, from the market. With canonical, from a token, you get which markets accept it as collateral: who depends on me, not what I depend on. Both return rows; only one is your question.
- **`RESERVE_BACKING` does not decompose backing.** It lands on a treasury that holds some. A treasury holds many unrelated assets, so reading its holdings as the token's backing manufactures dependencies that do not exist.
- **`BACKED_BY` needs its `source` filtered before it is a decomposition.** One source points at a proof-of-reserve aggregator, not a backing asset; `get_backing` separates the two for you.

**Path helpers give candidates, never answers.** `find_path` is semantically blind: it returns a shortest chain over edges regardless of meaning, and will stitch `HOLDS` walked as "things that hold the subject" onto an `AT_RISK` edge, two true edges and no transmission path. `find_connections` summarises intermediaries per relationship class; `dependency_closure` follows the server's dependency edge set and can report a node as a leaf well inside `maxHops` when a bound binds. Use all three for candidates. Never size from them, and never conclude absence from them.

**Turn a zero into a measured zero with `available_traversals`.** An empty `read_cypher` result is unknown; `absentTypes` is counted absent. That is the difference between "this market has no borrowers" and "I cannot see this market's borrowers", and only one of them is a finding.

## C3. Ecosystem-wide

1. **Denominator first; this is where it goes wrong most expensively.** Resolve as in `mode-a-dependency.md`, A1, then look at holder composition before dividing by anything (query-patterns, Backing and nested collateral: holders of the entity). If controllers, peg keepers, flash lenders or factories dominate, `total_supply_raw` is not your denominator: for a CDP stablecoin use collateral-backed debt, summed across every controller. Getting this wrong can turn a real near-half concentration into an apparent sub-1% non-event. For an ordinary token, market cap is fine. `blast_radius` reports competing valuations under `valuation` and declares none authoritative.
2. **Hop 1, the holder side:** `blast_radius({subjectId, floorUsd: <your floor>})`. It returns three buckets that must never be added together:
   - `additive`: the union of direct positions in the subject, everyone holding it, pro rata. This is hop 1.
   - `reattributed`: holders of derivatives (receipt tokens, LP tokens, backed stablecoins), the same dollars one hop out with better-named victims. It adds zero new dollars by construction.
   - `unattributed`: derivative holder value with no measured subject position. In no total.
   Read `mechanisms[]`: a mechanism with `sized: false` (admin-key compromise, oracle manipulation) is unmodelled, not zero, and belongs to a different vector (C4, name the vector).
3. **Beyond hop 1, walk outward** along the types the subject actually has, read from the C2 table backwards: outbound `HOLDS` for holders, incoming `LENDING_COLLATERAL` for markets where it is collateral, incoming `POOL_ASSET` for pools, incoming `RECEIPT_FOR` for the claim layer, `ADMIN_CTRL` for an admin subject.
4. **Floor:** 0.1% of the denominator or $1M, whichever is smaller, and state it. No fixed hop limit: depth is not a proxy for importance, the largest single victim often sits at hop 2 while a small one sits at hop 3, and depth 4 to 5 is normal. Loops terminate on their own once you attenuate, because each pass multiplies by a fraction below 1; report a cycle as a named loop rather than unrolling it.

**Sizing. Four rules, each an order of magnitude if you get it wrong.**

**1. Attenuate. Never propagate a downstream node at full value.**

```
impact at hop n+1 = downstream node value x (impaired backing / total backing at that node)
```

Without this, transitive closure eats the graph: an asset that is a fraction of a percent of a large stablecoin's backing propagates as the whole stablecoin, and the total exceeds the subject's own market cap several times over. Low-fraction paths still belong in the output, with the percentage and an explicit "this does not break the peg": "exposed at 28bps" is a useful answer, not a row to suppress. High-fraction paths must not be attenuated away either: a subject that is 47% of something's backing propagates at close to full value.

**2. Keep three kinds of dollars in separate tables, never summed across.**

- **Direct holder loss:** `blast_radius.additive`, at most the subject's own value.
- **Re-attribution:** receipt-token holders, bridged supply, `blast_radius.reattributed`. The same dollars with better-named victims; adding them double-counts the largest position in the analysis.
- **New dollars:** bad debt in a loan asset, AMM counter-legs, anything denominated in something other than the subject. These legitimately push the total above the subject's market cap, and the new-dollars table alone can exceed it; `blast_radius` does not size them: compute them per market (sizing rule 4 below and `defensive-borrowing.md`) and report them in their own table.

There is no grand total across the three, and the report builder refuses to print one. Mind the naming clash between the tool and the builder's wide mode:

| Kind of dollars | `blast_radius` field | `build_blast_report.py` field |
|---|---|---|
| Direct holder loss | `additive` | `impacts` |
| New dollars | not sized | `additive` |
| Re-attribution | `reattributed` | `reattribution` |
| Unattributed | `unattributed` | none: state it in the notes |

**3. Union within a family, never sum.** A token, its admin, its custodian and its oracle all point at the same position. Label family subtotals as unions.

**4. Mechanism-specific sizing.**

- **AMM pools:** the LP loss is the whole pool, not the subject's leg, because a worthless asset gets arbitraged against the paired asset. Take the counter-leg from inbound `HOLDS` on the pool. Amplification runs about 1.0x to 3.3x. AMMs only: a vault has no arbitrage drain, so its counter-leg is not lost.
- **Isolated lending markets** (single collateral): bad debt equals the amount borrowed, before the defensive-borrowing rush (`defensive-borrowing.md`), which is usually the largest additive line on an ecosystem-wide run.
- **Multi-collateral markets:** pro-rate by the subject's share of the market's collateral, from inbound `HOLDS` on the market node.
- **`max_borrow_capacity_usd` is not a measurement.** It is a collateral balance times an LTV parameter (`get_schema`): it moves when governance changes an LTV and not when borrowing does. Never report it as borrowed value or bad debt; if nothing else exists, label it a configuration ceiling and keep it out of totals.
- **Borrowers flip sign.** Inbound `LENDING_BORROW` is debt denominated in the subject. If the subject becomes worthless those borrowers gain and the suppliers lose. Never add debt-side rows to an ecosystem-wide blast radius.
- **Minted-against is not lent-against.** Collateral backing issuance means peg risk. Collateral in a market where a stablecoin is lent means the stablecoin stays fully backed and the loss falls on its suppliers. Collapsing these is wrong in both directions.

## C4. Targeted

1. **Resolve both ends, then classify the target.** Its denominator and its inward vocabulary both depend on the class:

| Target class | Denominator | Where value sits |
|---|---|---|
| Vault | `get_denominator` (allocations plus idle, with the caveats in `mode-a-dependency.md`, A1) | outbound `VAULT_ALLOCATION`, `VAULT_ASSET` |
| Wallet or Safe | sum of inbound `HOLDS.usd_value` and nothing else; cross-check anything material externally | inbound `HOLDS` |
| Protocol | sum over its markets and vaults, deduped, scoped on `lending_protocol` (`mode-b-closure.md`, B1) | its own `lending_protocol` set |
| Token or receipt token | market cap, subject to the CDP warning in C3 | outbound `HOLDS` for holders |

2. **Every percentage in targeted mode is a share of the target's assets, never the subject's.** The mode answers "what does this cost me"; a percentage of the subject's market cap answers a question nobody asked.
3. **Expand inward from the target**, one query per (frontier, edge type). Do not use a variable-length pattern (`*1..6`): the cost guard refuses it, and you lose the per-hop fractions you need for sizing.
   - Keep a visited set. A node reached twice on one branch is a cycle: stop, name the loop, do not unroll it.
   - Carry the running product. Each frontier entry is `(node, path so far, cumulative fraction, position value at the top)`. Never prune on depth.
   - Prune on the target's floor: 0.5% of the target's NAV or $10,000, whichever is smaller. State it.
   - Project `primary_label`. On whole clusters of intermediaries `label`, `name` and `blockscout_name` are null or generic while `primary_label` carries the real attribution. An unnamed intermediary is a path the reader cannot check.
   - Batch frontier ids in groups of about 10; one `LIMIT` is shared by the whole batch, so a wide one truncates silently. If the row count reaches the limit, split and rerun.
4. **Then expand outward from the subject 2 to 3 hops and intersect.** A node in the intersection is a confirmed junction; meeting in the middle is what lets you reach 7 or 8 hops in total without either side exceeding the guard.
5. **When the two sides do not meet, that is a finding, not a null result.** The usual cause is allocations pointing at a protocol singleton rather than market ids, so the inward walk arrives at a node with thousands of markets beneath it and no way to tell which. Report the resolved part and the unresolved allocation as an explicit upper bound: "$X resolves to named markets, of which the subject reaches $Y; a further $Z routes through the singleton where the breakdown is unavailable, so $Z is an upper bound on additional exposure."

**Size each path** as position value at the top x the product of the transmission fractions along it.

- **Direct holding** (or a receipt token or wrapper for it): loss is the position value, fraction 1.0. Count the receipt route or the underlying route, never both.
- **LP position in a pool containing the subject:** loss is the whole LP position, fraction 1.0 capped at the position value.
- **Supplying a lending market where the subject is collateral.** The target does not hold the subject at all, and this is the most common way naive sizing overstates. Under full compromise the borrowers walk and the suppliers eat the debt the subject was backing:

  ```
  loss = (target's allocation / market total_supplied_usd) x (debt backed by the subject)
  ```

  Isolated market with the subject as sole collateral: debt backed is the market's `total_borrowed_usd`, so this reduces to `allocation x utilisation`. Utilisation is typically 0.80 to 0.95, so propagating the allocation whole overstates by whatever share sits idle. Multi-collateral: multiply again by the subject's share of the market's collateral, from the market's inbound `HOLDS`, not assumed. Report exposure (the allocation sitting behind the subject) and loss under compromise as two separate columns, then apply the rush in `defensive-borrowing.md`, which turns this current estimated loss into the maximum loss.

  Four things change these numbers:

  - Cap the supply share at 1.0. A vault's `allocated_usd` can exceed the market's own `total_supplied_usd`, an impossible state, usually refresh skew between two writers. Cap it and report the excess rather than hiding it: a large discrepancy means the two figures disagree, and the analysis does not establish which is wrong.
  - `total_collateral_usd` is effectively unpopulated on several protocols (its `get_schema` availability says which), and absence is not zero. On morpho_blue, sum the per-borrower `collateral_usd` on the market's borrow edges first (query-patterns, Defensive borrowing inputs, read 2). Only where neither exists is the loan side the only basis: then say collateralisation was not independently checked.
  - Check the market's borrower population before promising a cross-pipeline check. Comparing `total_supplied_usd - available_liquidity_usd` against the per-borrower debt sum (`get_concentration` on the market) is the strongest free corroboration available, but some markets carry no per-borrower edges, and then the borrowed figure is simply uncorroborated. Say so rather than implying it was verified.
  - Check whether the target is the market's only supplier (the market's inbound `VAULT_ALLOCATION`, ignoring $0 sibling edges). If it is, the share is 1.0 and the loss reduces to the market's whole debt, simpler and larger than a pro-rated guess.

- **The base asset is often unreachable, and that is the finding.** Where the subject is a base asset, exposure usually arrives through a wrapper or staking derivative, and the last hop may not exist in the graph: a wrapped LST resolves to its liquid form and stops, with no backing edge onward to the native asset. A traversal trusting only graph edges then concludes a vault with tens of millions of staking collateral has zero base-asset exposure. Do not report that. State the derivative chain you measured, assert the final hop from outside the graph and label it asserted, with a fraction of 1.0 where the derivative is a pure claim: a staking derivative is not partially backed, so under base-asset-to-zero it goes to zero too.
- **The mirror trap is a hedged derivative.** A delta-neutral synthetic is base-asset-linked but not base-asset-exposed, and the graph models neither leg. Report it unresolved with an upper bound named "loss if this token were fully impaired", never as an exposure estimate.
- **Borrowing against the subject as collateral:** the sign flips, but not to zero. The target posted `C` and drew `D`; if the subject goes to zero it abandons the position, keeping `D` and losing `C`. Loss is its equity, `max(0, C - D)` (`get_levered_position` gives both legs). Never put gross collateral in a loss column, and never drop the row because borrowers gain.
- **Oracle dependency:** fraction 1.0 over the positions the feed actually prices, but only where the edge marks it as the price authority: `pricing_authority = true` for a market's collateral price, `value_defining = true` for a token's own price. Read both; filtering on `value_defining` alone misses every Morpho market. Coverage is thin, so a zero-row result means the price path is unresolved, not absent. Name every position it failed to resolve, with its dollar figure.
- **Admin key or upgrade control:** fraction 1.0 over the value beneath it, or `extractable_usd` where present. Prefer node-level at-stake stamps (`get_admin_risk`) for the headline and edges for attribution. The extractable figure is built from venue depth (market liquidity, pool counter-legs) at its own `*_updated_at`: where the venues' current figures differ from the ones it was built on, headline the current venue figures, and quote the stamp beside them with its time. Check the Safe verdict (`get_governance`) before writing that governance is unmodelled: a definitive `not_safe` is an EOA, which is a finding. Report the timelock on the path.
- **Stablecoin or derivative backed partly by the subject:** fraction = the subject's share of that token's backing. Attenuate; never propagate it whole.

**Dedupe, then sum. Key every path on its terminal position**, the leaf where the target's dollars actually sit: a market id, a pool id, or a token for a direct holding.

- Within a position key, take the maximum across channels, never the sum. One allocation can depend on the subject as collateral, through its oracle and through its admin key: one set of dollars with three reasons.
- Across distinct position keys, sum. Two allocations to two markets are different money.

**The hard invariant: a targeted total can never exceed the target's own NAV.** If it does, you have double-counted: go back to the keys. This is the opposite of the ecosystem-wide rule, where the total legitimately exceeds the subject's market cap because counter-legs and loan assets are new dollars; carrying that intuition into targeted mode means accepting an impossible number. The one real exception is a leveraged target, where loss can exceed equity; say so explicitly if you invoke it.

**Name the vector, and never take the maximum across vectors.** "The subject is compromised" is not one event. Value-to-zero, a wrong price and a drained admin key reach the target through different channels and produce different numbers. Pick the vector from the subject's class: an asset defaults to value-to-zero, an oracle to a wrong price, an admin key or upgrade proxy to a full drain, a custodian to loss of the custodied balance, a bridge to wrapped supply becoming unbacked. Take the maximum across channels within a vector; report vectors separately.

Mechanism and vector must agree, and it is mechanical enough to check:

| Mechanism | Transmits |
|---|---|
| Direct holding, receipt, LP, backing share | value to zero, custody loss, unbacked supply |
| Market collateral, isolated or multi | value to zero, wrong price, unbacked supply |
| Borrower equity | value to zero, unbacked supply |
| Oracle | wrong price **only** |
| Admin or upgrade control | drain **only** |

So under value-to-zero the oracle channel is not part of the answer. It is a real path and it belongs in the output, held out and labelled, never dropped: a path silently missing reads as a path never found. `scripts/build_blast_report.py` enforces this from `subject.vector_class` and prints what it held out.

Where the subject shares an admin, issuer or custodian with other assets, the common-cause version is a genuinely different and often much larger answer. Give it its own labelled section, never mixed into the path table.

## C5. Group and rank

Ecosystem-wide: group by family, the protocol or asset the impacted nodes belong to, not by abstract layer. A node shared across families goes at top level.

Targeted: the deliverable is the ranked path table, the path diagram (which carries the intermediaries), positions with no resolved path to the subject, and whatever remained unresolved or bounded. Build both sub-modes with `scripts/build_blast_report.py`, `mode: "wide"` or `mode: "targeted"`; the fixed report shape is in `report-style.md`.

## Mode C checklist

- Every target position either carries a path or appears in `unconnected`?
- Every path's mechanism able to transmit the declared vector, with non-transmitting paths held out and labelled?
- Every market where the subject is collateral carries a current and a maximum loss with the binding constraint and the price-authority feed named, or a stated reason it does not?
- The three kinds of dollars (direct, re-attributed, new) in separate tables, never summed across?
