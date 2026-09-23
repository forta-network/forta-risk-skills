---
name: forta-risk-analysis
description: "Analyse on-chain dependency and control risk with the Forta Risk Graph. Answers three questions: what could make a vault, wallet, Safe or token lose money (dependency concentration); every contract and admin key that can cause user loss across a whole protocol, down to terminal keys (control closure); and who loses money if a given asset, oracle, admin key, custodian or bridge is compromised (blast radius). Use this whenever the user asks about risk, exposure, dependencies, single points of failure, concentration, contagion, admin keys, upgradeability, governance, choke points, who can take user funds, or what breaks if something fails, and whenever they paste a contract address in any risk context. Also use it to compare risk across several vaults or protocols, to refresh a previous analysis, and for follow-up questions about one dependency or one impacted party."
---

# Forta Risk Graph: dependency and control risk analysis

Every analysis here answers one of three questions. Pick the mode first, because the same graph read in the wrong direction gives a confident wrong answer.

| The question being asked | Mode | Typical subject |
|---|---|---|
| What could make this lose money? Where is the concentration? What does it depend on? | **A. Dependency concentration** | A vault, wallet, Safe, token or market that holds value |
| Who can take user funds? How deep do the dependencies go? What about upgradeability, admin keys, governance? | **B. Control closure** | A whole protocol (Aave, Morpho, Euler, Silo, Compound, Spark, Maker) |
| What breaks if this fails? Who is exposed to it? How systemic is it? | **C. Blast radius** | A dependency: an asset, oracle, admin key, custodian, bridge, market |

A and C are inverses. A starts from something that holds value and finds what can hurt it. C starts from something that can fail and finds who it hurts. If the subject holds value, it is A. If the subject is a thing other people depend on, it is C.

All three share the rules in the next two sections. Read them before writing a query.

---

## Connector rules

**Requirements.** This skill drives the **Forta Risk Graph** MCP connector and does nothing without it. It uses `read_cypher`, `get_schema`, `available_traversals`, `dependency_closure` and `get_levered_position`. If those tools are not available, say so rather than substituting a block explorer or a web search: the analysis depends on the graph's own edge model, and an answer assembled from other sources will not be the same answer. The report scripts need Python 3 and nothing else.

**Scope is Ethereum mainnet.** The graph holds no other chain, and nothing in a query result says so: a subject deployed across several chains returns its Ethereum slice with every completeness signal reading as full coverage. So before sizing anything, establish whether the subject is single-chain. If it is not, say which chain the figures cover and name the deployments excluded, in the report rather than in a footnote — a levered counterparty whose collateral sits mostly on another chain will otherwise read as thinly collateralised, and a protocol's largest market may simply be absent. The same applies to a bridged asset: the Ethereum-side lockbox is visible, the far side is not.

**The partition binds on nodes, never on relationships.** The server injects `graph_id` onto an unanchored node pattern for you; write it on nodes yourself only to be explicit. **Never put it on a relationship** — thousands of edges carry no stamp, so the filter hides real edges and the empty result reads as "no owner" (server instructions, rule 1). This is the highest-cost mistake available here, because it fails toward less risk and passes every consistency check.

**The partition is versioned and it moves.** Confirm it in one call rather than assuming, because a wrong value is rejected with a message that names the correct one:

```cypher
MATCH (n:Entity {id:'native', graph_id:'discover'}) RETURN count(n)
```

```
cypher: query must bind the configured graph_id partition:
graph_id bound to "discover", only "risk-graph-rt-v3" permitted
```

Use whatever it names for the rest of the analysis, and **state the partition id in the output** — a report whose figures cannot be traced to a graph version is not reproducible.

Query hygiene that keeps you inside the cost guard:

- Keep matches **directed** and **single-typed**. Undirected multi-type matches on high-degree nodes time out.
- Put a `LIMIT` on everything, including aggregates. `LIMIT` is capped at 1000, and a higher value is a rejection rather than a clamp.
- Use single-argument `round()`. Two-argument `round(x, 2)` fails, as does a multi-branch `CASE` combined with wide aggregation.
- Prefer `read_cypher` with explicit `RETURN` projections over relationship dumps. A dump on a high-degree node runs past 100k characters and will consume your context in one call.
- Never call `keys()` on a `lending_market` node. They carry 200+ properties. Project the fields you want: `name`, `collateral_asset`, `loan_asset`, `lltv`, `total_supplied_usd`, `total_borrowed_usd`, `utilization_pct`, `market_key`.
- If a query times out, **narrow it** rather than retrying it unchanged. Two frontier queries in parallel in one message is faster than serially and the guard tolerates it.

The partition is live and refreshes continuously, so two queries minutes apart can differ slightly. That is refresh, not disagreement: do not try to reconcile figures to the dollar across separate queries, and give one snapshot date for the whole analysis.

---

## Semantics come from the server

**This file does not define the graph. The server does.**

The MCP server ships its semantic rules in the `instructions` field on every `initialize`, rendered from the deployment's actual configuration and pinned by tests. `get_schema` serves the live edge vocabulary with the canonical direction of every type, property units, per-property availability and caveats, and the cross-cutting notes. Both arrive before your first tool call.

Restating any of that here would fork it, and the fork goes stale silently while reading as authoritative. That is the failure this rule exists to prevent, and it is not a cosmetic one: the graph's mistakes are not errors but **confident wrong numbers that pass every internal consistency check and point toward less risk than there really is.**

So, three habits, every run:

- **Call `get_schema` before the first traversal.** Retired and nonexistent edge types return `[]` silently, and `[]` reads as "no risk". Add `includeCensus: true` when population matters — a type can exist and be near-empty, and an empty traversal over a sparse type says nothing about the entity you queried.
- **Read `meta` before quoting any figure.** `page` for truncation, `declared` for which guarantees this response actually carries, `asOf.block` for what it is true at. A guarantee absent from `declared` is UNKNOWN, never zero.
- **Never sum across differing `asOf.block`.** The partition mutates mid-session. Before combining two reads into one derived number, compare their `asOf.block` and `last_stat_block`; if they differ, requery both rather than disclosing the gap. Disclosure is not a substitute for a consistent snapshot.
- **An old write stamp is not automatically stale, and on an interest-bearing token it always is.** Balance stamps are event-driven, so a quiescent position legitimately keeps an old block and the figure is still right. That defence fails on anything whose balance grows without a transfer: an aToken, a rebasing share, an accruing debt. There is no event for the writer to fire on, so the recorded quantity drifts below the real one for as long as the stamp is old, and the drift is invisible. Before differencing two such legs, check each one's age separately and say which is older: the staler leg carries the larger unrecorded accrual, and that decides which way the answer is biased. Check for a `reconciled_at` property too, and note that its **absence from the edge entirely** means the token is outside reconciler coverage, which is different from a null value on a covered token.

**Where workflow depends on a rule, name it, do not quote it.** "Group borrowers on the server's canonical owner basis" survives a rewording of the rule; a verbatim copy of the rule does not. Never restate a direction, a unit, a coverage figure or a population count in this file.

**Distinguish unknown from zero, every time.** A filtered query returning nothing means UNKNOWN. Name what the filter dropped, in dollars. A report that does not state its gaps is not finished: a number without its denominator, its coverage and its as-of block is not an answer.

### Gaps to state, never to fill

The server flags these as structurally absent. If an answer depends on one, say so rather than reasoning around it:

- **Escrowed collateral is not in the graph, so any levered entity is understated.** Say this in every report on a levered entity, not only where a tool returns `unresolved`.
- **Backing and custody one hop past the last on-chain token** is unmodelled for custodial wrappers and off-chain credit.
- **The base asset is reachable only through `HOLDS`**, never a dependency-typed edge. Any traversal restricted to a list of dependency types misses it, so include `HOLDS` explicitly or take the type set from `get_schema`.
- **Withdrawable capacity is not NAV.** See A2, which carries the mechanism and the prohibition.
- **Depositor concentration** appears in no position table, and a single dominant depositor is a solvency-of-exit event. See A8.

### Figures that need a second look before they ship

Judgment, not semantics: each row is a shape that reads as settled and is not. One query each.

| What you are looking at | The tell | What to do |
|---|---|---|
| A market's supply figure | The phantom signature described in `get_schema`'s `total_supplied_usd` caveat | Size it from the protocol singleton's actual `HOLDS` balance instead |
| A singleton against its own markets | `is_aggregate_node`, per the schema notes | Keep one level, drop the other, and say which |
| Oracle at-stake figures | Many oracles on one market carrying an identical value equal to the market's whole liquidity | Never rank or aggregate on oracle at-stake. Rank oracle admins over the feed set instead |
| A CDP token's supply | A holder list dominated by controllers, peg keepers, flash lenders or factories | `totalSupply` here includes supply minted to controllers and never borrowed. Use collateral-backed debt as the denominator |
| Any debt total implausible for the protocol's size | A large edge count against a trivial dollar total, or the reverse | Confirm against the protocol's own interface before reporting it |
| A concentration share, ranking or HHI | Whether `get_concentration` would have refused it | See A4. Never publish a statistic the tool declines to compute |
---

## The absence gate

**No sentence asserting a capability gap ships in a deliverable until you have verified it against the live graph in this run.** That covers every phrasing of "not modelled", "not computable", "not queryable", "no per-X edge", "no admin", "the graph cannot answer this". Two checks:

- **`keys(r)`** on the relationship, or `keys(n)` on the node. Properties are added to edges as the graph develops, and this is the only check that shows what an edge carries **today**. A newly added property changes no edge count, so a type census reads identically whether it is there or not; a schema description may not mention it yet; and a helper built before it existed will keep returning the answer it was built for. `keys()` is the ground truth, and it costs one call.
- **`keys()` sees graph properties only, and the server also serves fields that are not properties.** A response can carry per-row stamps and response-level blocks that no `keys()` call will ever list, because they are computed at the serving layer rather than stored on the node or edge. So `keys()` returning without a name is evidence about the **graph**, not about the **response**: run the actual query and read what comes back, including `meta`, before concluding a field is unavailable. Testing for a stored property when the documentation said "server-provided" is how this goes wrong.
- **`available_traversals`** on any node you are about to describe as a leaf, **and on every node whose position set you are about to report as complete.** Those are different claims and the second is the one that goes into a report: listing a sub-vault's holdings without measuring its edge types reports the assets and drops the liabilities, because a debt position is an edge type rather than a balance. It returns each present type with an exact uncapped count plus an **`absentTypes`** list, which turns a non-hit into a measured absence for free. Sampling a node's edges does not reveal which types exist. Mode B's B6 then tells you whether an empty admin set is a verified absence or an unconfirmed one — the difference between reporting a strength and inventing one.

**Check a traversal helper's directions against the schema before treating its result as coverage.** `dependency_closure` follows `LENDING_COLLATERAL` in its canonical direction, which for a vault or market anchor reaches the oracles rather than the collateral — usually the layer you wanted. Check the direction in `get_schema` and reason about where that lands from your anchor. Its breadth also goes mostly to the denomination asset's peers: every other market that accepts the same asset as collateral, which are not dependencies of your subject. And `truncated: true` reads the same whether a branch was cut by the hop bound or is not reachable by the edge set at all. Use it on **token** anchors, where it is the right tool; on a vault or market, walk the layers yourself in the directions the schema gives (A3), and use Mode B's B3 and B4 mechanics for the control chain above any node it reaches.

**Any figure quoted in this file is illustrative, not current.** The graph refreshes continuously and populations move in both directions as coverage extends and as retired edges are pruned, so re-measure anything you intend to report rather than quoting a number from here. This matters most for absence: a stale count is a rounding error, while a stale claim that something is unavailable becomes a caveat that misstates real risk.

---

## Defensive borrowing: what a failure costs once borrowers see it coming

**A lending market's loss is not what has been borrowed. It is what can be borrowed.**

An asset that is failing keeps its old price for as long as it takes the market's oracle to catch up, and that window is the whole game. Inside it, the rational move for anyone holding the asset is to post it as collateral, borrow whatever the market will lend, and walk away from the position. The borrowed asset is real and it leaves. The collateral left behind is worthless. The lender is left holding both the original debt and everything the borrowers drew on the way out.

So a report that sizes the loss at current utilisation understates it, and the part it drops is exactly the part a reader would care about: the liquidity that looked recoverable. **Never describe a market's unborrowed liquidity as recoverable under a value-to-zero vector.** It is the first thing to go.

**Four constraints bound the extra borrowing, and the smallest one binds:**

| Constraint | How to compute it | Where it comes from |
|---|---|---|
| Collateral already posted | `posted collateral x LLTV - debt already drawn` | `total_collateral_usd`, or `sum(collateral_usd)` over `LENDING_BORROW`; `toFloat(lltv)/1e16` for the percentage |
| Collateral that could still be posted | `(posted + postable) x LLTV - debt`, where `postable` is bounded by any cap on total collateral and is otherwise unbounded | a collateral supply cap where the protocol has one, and the attacker's reachable supply of the asset |
| Available liquidity | `available_liquidity_usd`, or `total_supplied_usd - total_borrowed_usd` | the market edge or node |
| Borrow cap headroom | `borrow cap - debt already drawn` | `borrow_cap_usd` |

```
extra borrowing = min(collateral room, available liquidity, borrow cap headroom)
loss under a rush = debt already drawn + extra borrowing
```

**Collateral already posted is a floor, not a ceiling, and treating it as a ceiling is the easy mistake.** The first constraint asks what today's borrowers could draw against collateral they have already committed. That is the right question only if the collateral set is frozen, which is exactly what a failing asset does not do: anyone holding the asset can post more of it and borrow against that too, and the worse the news the stronger the incentive. So the honest ceiling is the second constraint, and it is frequently **unbounded**, most obviously where the asset's own minter is among the compromised roles, because then the attacker's supply of collateral is whatever they choose to mint. Report the posted figure as context and let the postable figure be the one that competes for the minimum.

**When the collateral term drops out, the answer is decided entirely by liquidity and caps, and a cap is the only thing that can hold it below the whole market.** This is why the cap belongs in the model rather than as a footnote. Where a protocol caps how much of an asset may be posted as collateral, that cap converts an unbounded exposure into a computable one, and it is usually the number a risk committee actually controls. Where no cap exists, say so in the row: "no borrow cap, so nothing caps the draw here" is a finding, not a blank.

**Caps live in different places per protocol, and a null is not a zero.** Morpho Blue markets carry neither a borrow cap nor a collateral supply cap, so both terms read null and drop out of the minimum; the caps that do exist on Morpho are vault-level `absoluteCap` and `relativeCap`, which bound how much the *vault* supplies rather than how much borrowers can draw. Aave v3 reserves carry both a borrow cap and a supply cap. Compound v3 caps each collateral asset per Comet, which is precisely the constraint that stops an arbitrarily large collateral deposit. Coalescing any of these to zero makes the extra borrowing zero and silently deletes the whole effect.

**Liquidity usually binds, and that has a consequence worth stating plainly.** Borrowers post collateral well above what they draw, so collateral headroom is typically an order of magnitude larger than what is left in the market to lend. When liquidity binds, `drawn + liquidity` is the market's entire supplied amount, which means **a sole-supplier isolated market loses its whole position, not its utilised fraction.** On the LBTC/PYUSD market on 25 August 2026 the collateral headroom was $12,994,689 against $243,072 of liquidity, so the loss went from 92.60% of the position to 100% of it.

**Report both numbers, never one.** They answer different questions and a reader needs the pair. Head the report with exactly these two, and no third: **Current estimated loss**, the loss at the amounts borrowed today, and **Maximum loss**, the loss once borrowers draw everything they can reach, marked as the hot one, and show the three constraints with the binding one marked, so the reader can see which lever would change the answer.

**Where the effect does not apply, and saying so is part of the analysis:**

| Situation | Why |
|---|---|
| The asset is not accepted as collateral anywhere | Nothing to borrow against. The loss is the holding, full stop |
| The oracle updates faster than borrowers can act | Then the mechanism is liquidation, not borrowing, and the market keeps its liquidity |
| The market can be paused, and the pause is credible | Name the role that can pause it and its timelock. Do not assume it fires |
| A multi-collateral pool sized by pro-rata share | Pro-rating already attributes the asset's whole share of pool debt, so adding a rush on top double counts. Say which basis was used |

**In Mode A** this changes the impact figure for any collateral asset the subject lends against. **In Mode C** it changes the additive bad-debt figure for every market where the subject is collateral, which on an ecosystem-wide run is usually the largest additive line. Compute it per market and sum, rather than applying a factor to a protocol total.

## Mode A: dependency concentration

**The question:** if any single thing this entity depends on were compromised, how much of its value is exposed?

### A1. Resolve the subject and fix the denominator

Do not call `resolve_address` on a high-degree node; `nodeLimit` does not bound edges and the payload can exceed 150k characters. Project instead:

```cypher
MATCH (n:Entity {id:'<entity>', graph_id:'risk-graph-rt-v3'})
RETURN n.id AS id, coalesce(n.label,n.name) AS label, n.symbol AS symbol,
       n.category AS cat, n.subcategory AS sub, n.project AS project,
       n.usd_price AS price, n.total_supply_raw AS supply, n.is_proxy AS isProxy
```

Searching a ticker returns impersonators alongside the real asset, so disambiguate on `label`, a non-null `usd_price` and `category`, and state which one you analysed.

**Every percentage depends on the denominator, and there are three ways to compute a vault's NAV:**

1. **Sum of `allocated_usd` plus idle. Prefer this**, because it is provable: divide any `allocated_usd` by its `share_pct` and every position must imply the same total.

   **Agreement proves neither the coverage of the set nor the rows inside it.** `share_pct` is normalised across whatever the writer enumerated, so a truncated set sums to exactly 1.0000 and passes this test while missing most of the book. Measured on a Mellow-stack vault: three sub-vaults' shares summed to 1.0000 over $18.36M, and the accounts the same writer reported at $0 held $589.52M of collateral, because the figure counts plain token balances and excludes lending receipts. **Before trusting an allocation set, read each sub-account's balances and its own debt edges, and treat a $0 allocation as unmeasured rather than empty** — confirm emptiness on the account itself, not from its allocation row.

   **Agreement proves the denominator, not the per-market rows.** `allocated_usd` and `share_pct` are written together, so a row carrying a stale amount carries a matching stale share and the ratio still agrees across every position. The test cannot detect a partially refreshed allocation set, and `VAULT_ALLOCATION` carries no per-edge freshness marker either. Take the total from the ratio, then reconcile each market row against the protocol in A8 before sizing any single market.
2. `total_supply_raw` x `usd_price`. Use as a cross-check only. `usd_price` on a vault share token tends to track par rather than actual share price.
3. Sum of holder `HOLDS` balances. Useful as a **staleness detector**: if holders sum to more shares than `total_supply_raw`, the balance snapshot is skewed and any share price derived from that supply is inflated. Report the position at `HOLDS.usd_value` instead.

Two invariants worth one query each: holders cannot hold more shares than exist, and `total_supply_raw` x `usd_price` cannot be less than `sum(allocated_usd)`, because a vault cannot lend out more than it owns. Either failing tells you which field to distrust.

**Do not derive a share price as NAV divided by `total_supply_raw` and treat a disagreement with `usd_price` as a pricing error.** Both fields sit in that ratio, so a gap tells you something is off and nothing about which side. Verify a material position against the protocol's own interface, an on-chain `convertToAssets` read, or a portfolio aggregator, and treat that as ground truth over anything the graph implies.

**A wallet or Safe has one measure only.** Its NAV is the sum of its inbound `HOLDS` edges and nothing else, so the graph cannot self-check it. Cross-check material holdings externally.

Note the gap between total assets and value actually deployed downstream. Idle capital is still exposed to the entity's own contract and its denomination asset, but not to the markets, which is exactly why those dependencies outrank the protocol. If `share_pct` sums to exactly 1.0000 the graph is modelling zero idle.

**A `share_pct` shortfall has two possible causes, and the shortfall alone does not distinguish them.** Either (a) idle capital or deployment not itemised in the allocation set, or (b) individual allocation rows carrying stale amounts. Unmodelled deployment is the intuitive reading and it is not always the right one: a 15% shortfall against a hundred dollars of idle turned out to be three stale rows on a fully-allocated vault. Report the residual with both readings named, size nothing on it, and settle which it is in A8.

### A2. Map where the value goes

Probe the vocabulary first. One cheap query gives you the entity's whole structural shape:

```cypher
MATCH (n:Entity {id:'<entity>', graph_id:'risk-graph-rt-v3'})-[r]->(m:Entity {graph_id:'risk-graph-rt-v3'})
RETURN type(r) AS rel, r.subcategory AS sub, m.category AS cat,
       m.subcategory AS mcat, count(*) AS c
ORDER BY c DESC LIMIT 60
```

Repeat with the arrow reversed. For vaults look for `VAULT_ALLOCATION` and `VAULT_ASSET`; for tokens `HOLDS`, `AT_RISK` and `RESERVE_BACKING`; for markets `LENDING_COLLATERAL`, `LENDING_BORROW` and `ORACLE_DEP`; for wallets and Safes **inbound** `HOLDS` plus `APPROVES`, `OWNS`, `OWNS_ADMIN` and `DEPLOYED_BY`.

**Check whether the subject is itself a borrower, before sizing anything.** A vault in this stack can be a supplier, a borrower, or both, and Mode A's supplier frame will not tell you which: every other mention of `LENDING_BORROW` in this file is about a *market's* borrowers, which is a different question. Read the subject's own **outbound** `LENDING_BORROW` edges at every layer that holds value, sub-vaults included. They carry `debt_usd`, `debt_raw`, `protocol` and `last_stat_block` per account, and **this, not a debt-token balance, is the debt figure A6 needs.**

A balance-derived debt total is wrong two ways at once. It silently omits every protocol that mints no debt token, Morpho Blue and Euler among them: on a measured account a $22,422,155 Morpho Blue WETH borrow was invisible in the balances and appeared only on the debt edge, and it was 7% of the account's liabilities. And where both representations exist they can disagree without either row flagging it: one Spark USDT leg read $5,997,688 on the debt position and $20,800,015 on the token balance at the same block, a 3.5x spread. Where they disagree, report both and say which one the figures use.

Always pull `adapter_address` alongside allocations. For a Morpho Vault V2 one adapter can route 100% of assets into every market: a hop-1, entity-wide contract dependency that no other query surfaces. It is not itself an `Entity` node — it exists as a string property — so nothing hangs off it and its own admins and upgradeability have to be resolved separately.

**A `VAULT_ALLOCATION` edge with `allocated_usd: 0` is not "enabled but unfunded".** It may be a dead edge for a market the vault can no longer allocate to. It is common for a majority of a vault's zero-allocation edges to have no corresponding cap at the protocol, and for some to name collateral whose maturity has already passed. Caps have three states — unlimited, deliberately zeroed, never enabled — and a zero-allocation edge presents identically in all three. Calling any of them latent capacity invents exposure that cannot legally be taken. Resolve it in A8.

**Withdrawal capacity does not aggregate across markets, so do not compute it as `Σ min(available_liquidity_usd, allocated_usd) + idle`.** Every field that formula needs is available, and it overstates redeemable liquidity — on measured vaults by roughly 2x.

The mechanism is the reason. A Morpho Vault V2 binds a designated **`liquidityAdapter`** to one market. A redeeming depositor draws from idle plus that market only; reaching any other market requires an allocator transaction, which a depositor cannot trigger. So the figure is `min(vault position in the designated market, that market's available liquidity) + idle`, and **either side can bind** — on one vault the position was the constraint, on another the market's own liquidity was.

The designated adapter and the resulting liquidity are protocol state, so read them in A8 or state that withdrawal capacity is not computable. Do not publish a derived figure.

**Look through any holding that is itself a vault.** For a wallet, `HOLDS` shows where value *sits*, not where it is *deployed*. Size the wallet's slice of each downstream position as `holding_usd x share_pct`. This never needs a share price, which is what makes it safe. Three things to get right:

- If `share_pct` sums below 1.0 the shortfall is idle inside the held vault. Carry the wallet's share of it as its own position.
- Reconcile: look-through plus the share of idle must equal the holding's USD value. If not, you have a stale edge or the wrong `share_pct` set.
- **Label the access route.** A look-through row is lending exposure to a market collateralised by an asset, not ownership of that asset. A holder of collateral loses when it falls; a lender against it is unharmed by an orderly decline and loses only when a fast drawdown outruns liquidation at the market LLTV, or when the asset depegs from what the oracle prices it against. Say which rows are held and which are reached, in the row name.

### A3. Trace dependencies outward

**Take the type set from `get_schema({includeCensus: true})`, not from a list written here.** It returns every live type with an exact count plus deprecated and sparse flags, so it cannot go stale and it tells you which types are too thin for an empty traversal to mean anything. A name that is not in it returns zero rows, which is indistinguishable from "no such dependency" and reads as safety.

**Then add `HOLDS` by hand.** The base asset is reachable **only** through `HOLDS` and never through a dependency-typed edge, so a traversal built from the dependency categories alone silently misses it. This is a documented structural gap, not an oversight to work around.

**`OWNS_ADMIN` is not a role.** The schema defines it as derived transitive reach: a multisig linked to nodes its own signers control elsewhere. It is correct by definition and it is not governance. Only `ADMIN_CTRL`, `ADMIN_OF` and `CURATES` carry an assigned `role`. On one vault, five inbound `OWNS_ADMIN` Safes sat in exactly the slot where roles belong, held no role at the protocol, and were published in a governance table as if they did. If a governance row has no `role` string, label it derived reach or leave it out.

**`BRIDGE_BACKED_BY` reaches the lockbox or OFT adapter, and stops there.** The adapter's DVN quorum — the thing that would tell you whether one forged attestation is enough — is not populated on this population, so the traversal works and the qualifier does not. Report the adapters as present with **severity unknown**, name their count and the dollars behind them, and do not imply the bridge configuration is either safe or a single point of failure. Reading the required-DVN set is a `getConfig` call on the LayerZero endpoint if someone needs the real answer. Also **check `startNode(r).id != endNode(r).id`**: a wrapper can carry a bridge edge that is a self-loop, which counts as coverage and reaches nothing.

That set is **not sufficient on its own**. Also chase backing through inbound `HOLDS`, `RESERVE_BACKING` and `BRIDGE_BACKED_BY` from every collateral and denomination token, run separately rather than as one multi-type match. And project the Safe properties on every multisig you reach.

Two things to extract yourself, because no risk field carries them:

- **Maturity dates.** Fixed-income collateral carries its maturity in its token name (`PT-sUSDD-27AUG2026`). Compare it against the snapshot date. A matured PT still holding a balance, or one maturing inside the month, is a real finding.
- **What the collateral actually is.** A name like "AA tranche" means senior structured credit over one borrower's performance, and a PT is a claim redeemable at a maturity date. Either reframes a report from lending risk to counterparty risk, and the token name is the only place it appears.

**Recursion is the whole phase, and "keep going until you stop finding new things" is not a method.** Walk it as a fixed set of layers, per position above the floor. This table is the mechanism; without it the walk stops at hop 2 and the report reads as complete.

| Hop | From | Pull |
|---|---|---|
| 2 | each funded market | collateral (**inbound** `LENDING_COLLATERAL`), oracles (outbound `ORACLE_DEP`, then filter `value_defining`), borrowers (`LENDING_BORROW` keyed on `market_id`), custody (`CUSTODY_VIA`) |
| 3 | each collateral asset | `admin_roles` **and** the `ADMIN_CTRL` edge set, `BACKED_BY`, `WRAP_UNWRAP`, `RECEIPT_FOR`, `BRIDGE_BACKED_BY`, `RESERVE_BACKING`, `EXIT_VIA`, inbound `HOLDS` |
| 3 | each `value_defining` oracle | its own outbound `ORACLE_DEP` (the feed behind the feed), `admin_roles`, `DEPLOYED_BY` |
| 4 | each asset role holder | is it a contract or a key? `is_contract`, `safe_probe_status`, threshold, and its own `admin_roles` |
| 4 | each underlying reached at hop 3 | repeat the hop-3 row on it |
| 5+ | each new node | repeat until a branch yields nothing new, then record which of the three B3 conditions ended it |

Rank asset roles by what they can do to a depositor, not by name: **blacklist, freeze and clawback** can strand the position with no exploit and no protocol involvement, **pause** halts withdrawals and liquidations, **proxy admin and upgrader** replace the implementation, **minter and master minter** dilute. Those are rarely called "owner" and they are the rows a reader acts on.

Record for each dependency: what it is, its layer, hop distance, which positions it touches, and its address.

### A4. Borrower concentration

For a lending vault this is often the **primary loss path**, so do not skip it.

**Call `get_concentration` first, with `groupBy: "owner"`.** It applies the server's own owner grouping, ships the denominator alongside every share, and — the reason to prefer it — **refuses** to publish a share, ranking or HHI over a population it could not page in full, reporting the rows and dollars it did not examine instead. A hand-rolled aggregate returns a confident number in exactly that situation, which is the failure this skill exists to avoid. Read `groupingFormed` as three-state: `false` is never a finding that exposure is diversified, and `null` means unknown.

Three rules on the result:

- **Never publish a statistic the tool declined to compute.** If it refuses, report the refusal and its exact `legsNotExamined` / `usdNotExamined`, then narrow the anchor and try again. "Concentration not computable over 54,451 borrower legs" is a finding; a share derived from a truncated page is not.
- **Never sum the holder and borrower dimensions.** Different units over different edges; a combined share is a category error.
- **A group flagged `is_aggregate_node` is not one concentrated owner.** It is a custody hub already containing its spokes.

Fall back to a direct `LENDING_BORROW` aggregate only where the tool refuses and you have narrowed the population to something that pages completely. Then state which route produced the figure.

**Owner grouping has two correct forms, and which one applies depends on what your query returns.** They are not alternatives to choose between:

- **A query returning borrower rows** gets each row stamped by the server with `canonical_owner_id` and `canonical_owner_id_basis`, plus a `canonicalBorrowerOwners` block carrying the basis and the count stamped. **Read the stamp; do not re-derive it.**
- **A query grouping inside Cypher** cannot see that stamp — it is served on the response, not exposed to the query engine — so group on the server's stated equivalent expression instead. The server names it in the same block, so take it from there rather than from this file.

Ranking on the raw borrower id is the error both forms exist to prevent: one owner can hold many sub-accounts differing only in the last byte, each emitting its own edge, so raw grouping splits that owner and under-reports its share. `reserve_id` is a token address, not a market id.

A single borrower large enough to matter, liquidated in a fast move, is the loss path for every vault supplying that reserve. Say it in those terms.

### A5. Size it

Impact of a dependency = the entity's value exposed if that dependency fails.

1. **Aggregate by union, never by sum.** A token, its admin key, its custodian and its oracle all point at the same underlying position, so adding their impacts multi-counts the same dollars and can exceed total assets several times over. Track each dependency as a **set of positions**, and compute dollars at the end over the union. Label group subtotals as unions so nobody tries to reconcile the arithmetic.
2. **This is exposure sizing, not expected loss.** Full compromise, no probability weighting, no recovery. State it.
3. **Do not assume zero recovery on a levered entity.** See A6.

Use **total assets** as the primary denominator, not deployed capital, or entity-wide dependencies read above 100% and look like errors. Report both totals in the header so a reader can rescale.

### A6. Encumbrance, on any entity that has borrowed

Gross exposure is right for an unencumbered holding and **wrong** for pledged collateral. If the entity borrowed against the failing asset, the debt is non-recourse: the collateral is liquidated, the liability is extinguished, and the loss is capped at equity.

```
overstatement = max(0, debt - seizable collateral remaining after the failure)
              = the bad debt the lending protocol absorbs
```

What the entity escapes is what the protocol eats. It is a transfer, never a disappearance, so **name the counterparty who picks it up** rather than netting it away.

**The debt figure comes from the subject's own `LENDING_BORROW` edges, one per account per reserve, summed per account.** Never from a debt-token balance, for the two reasons A2 gives. Escrowed collateral behind those same accounts is outside on-chain scope, so a loan-to-value built on visible collateral is an upper bound, not a measurement: say so in the row, and where the shortfall is large, state what collateral the invisible side would have to hold for the book to reconcile rather than leaving the gap unexplained.

1. **Split every position into encumbered and unencumbered.** The same asset sitting loose in the wallet takes a 100% loss with no offset, because there is no loan to walk away from. These two legs behave completely differently and must not be added and then adjusted as one.
2. **`liquidation_threshold` on the edge is the RESERVE-level parameter, not the account-effective one.** Aave's e-mode raises it per category for correlated collateral and is not represented on the edge, so a health factor computed from the reserve figure can read as liquidatable on an account that is comfortable. The gap is decisive rather than marginal: on a real position the reserve threshold implies a health factor around **0.82** and a large shortfall, while the applicable e-mode threshold implies about **1.005** — solvent, barely. This is one of the few traps that errs in the **alarming** direction: it invents a crisis rather than hiding one, and a false insolvency claim about a named counterparty is more damaging in a report than a conservative one. Never publish a health factor derived from the reserve threshold as a finding about an account. Two companions on the same edge: **`ltv: 0` alongside a non-zero `liquidation_threshold` is an offboarding configuration, not distress** — no new borrowing power, existing positions survive — and **check block skew before dividing**, because debt and collateral legs refresh independently and an LTV built from readings days apart is not a health factor.
3. **Establish the seizable base.** Read `usage_as_collateral_enabled` per reserve on `LENDING_COLLATERAL`. An asset supplied with that flag false earns yield but was never pledged, so it belongs in neither the collateral base nor the loss.
4. **Recompute after the failure.** Zero the failing asset, sum the remaining collateral-enabled positions, compare against the debt. If remaining collateral still covers the debt the account stays solvent and there is **no adjustment**: charge the full gross loss.
5. **Route the excess.** Where debt exceeds remaining seizable collateral, the difference is the protocol's bad debt. Report both sides.
6. **Apply the liquidation bonus.** Seized collateral retires less debt than its face value, because liquidators are paid a premium. `liquidation_bonus` is 1e4-scaled, so 10800 means a 1.08 multiplier, an 8% premium, and debt retired is `seized / 1.08`. The counterparty's shortfall is therefore larger than the plain subtraction; the entity's relief is unchanged. Give both figures.

Three things to say whenever you apply it:

- **The relief is tail-only.** In an orderly decline liquidators unwind at the threshold and the entity pays the bonus, so it does *worse* than mark-to-market. The floor only pays in a gap-down fast enough to outrun liquidation. Presented without this, an adjusted number reads as "leverage makes this safer", which is false at every price move except the one where the floor binds.
- **Collateral flags are reserve-level, not per-account.** Aave-style protocols let each user toggle collateral per asset, so the seizable base you compute is an **upper bound** on what can be taken from that account. Label it as one.
- **A worse debt figure produces a flattering adjustment**, since the floor is worth `debt - seizable collateral`. If the debt is uncertain, say which way that pushes the adjusted number, and never headline the most-levered reading without noting it is also the most favourable.

**Cross-collateralisation cuts the other way.** On a pooled account (Aave V3 and its forks) the debt is secured by the whole basket, so a failure also consumes the *other* collateral. Give that its own top-level row spanning every pledged position, worded symmetrically. Do not name it after one asset's failure: any pledged asset's loss, if large enough, forces liquidation of the others, and the pledged assets have their own independent dependencies. Every row in a dependency table must name a **thing that can fail**, never a scenario in which something fails.

**Render the adjustment in the table, never only in prose.** A grouped table sizes each family by the union of positions it touches, which is a gross figure and reads to any normal person as a loss. Put the adjustment in the `encumbrance` block of the findings JSON so it renders as a bridge table, and name any group whose figure is gross "shown gross" in the group name itself. A number in an impact column with a note two lines below is a number that will be quoted without the note.

**Where the adjustment bites is itself a concentration measure.** Run step 4 for every collateral asset. Usually only the largest is big enough that its failure makes the account insolvent. If exactly one asset triggers the floor, that is another way of saying the book is a single bet.

### A7. Group

Group by **dependency family** (the asset or component: WBTC, USDC, wstETH, the protocol, the entity itself), not by abstract layer. Families match how people ask: "what is my cbBTC exposure" is a real question, "what is my collateral-admin-layer risk" is not.

A dependency shared across families (a feed serving two collateral types, a base asset backing two wrappers) goes at **top level**, not nested inside one family. Nesting hides that it is shared and understates it, sometimes by an order of magnitude.

### A8. Cross-check against the protocol before delivering

**Required on any vault, market or protocol subject.** A1 already says to verify a material position externally. That is too narrow: a vault report rests on the graph's model of a protocol's *mechanics*, and where that model is incomplete the graph cannot tell you so. Every finding stays internally consistent and some are wrong.

Some of what a vault report depends on is protocol configuration rather than graph structure: redemption routing, timelock schedules, caps, the full role set, queued governance. Those are read from the protocol, and a graph-only report stays internally consistent without them, which is what makes the omission easy to miss. This step costs about five queries and settles them. **The graph is authoritative for dependency structure. The protocol is authoritative for its own state and mechanics.** Where they differ on state, prefer the protocol and note the difference.

**Morpho** (Blue, MetaMorpho, Vault V2): `https://blue-api.morpho.org/graphql`, no auth. Do **not** fetch `app.morpho.org` — it is a JS shell with no data in the HTML; this API is what renders it. `vaultV2ByAddress` for `vault_kind: morpho_v2`, `vaultByAddress` for MetaMorpho V1, both `(address, chainId)`. Write the query to a file rather than inlining it in `curl -d`; shell quoting mangles the nested quotes.

```
vaultV2ByAddress(address:"<VAULT>", chainId:1){
  totalAssets totalAssetsUsd totalSupply sharePrice idleAssets
  liquidity liquidityUsd forceDeallocatableLiquidity
  owner{address} curator{address} allocators{allocator{address}} sentinels{sentinel{address}}
  timelocks{ functionName duration abdicatedAt }
  caps{ items{ type absoluteCap relativeCap allocation } }
  pendingConfigs{ items{ validAt functionName } }
  adapters{ items{ address type assets forceDeallocatePenalty } }
  liquidityData{ __typename ... on MarketV1LiquidityData { market{ marketId collateralAsset{symbol} } } }
  positions(first:120){ items{ user{address} assetsUsd } pageInfo{countTotal} }
  warnings{ type level } performanceFee managementFee }
```

Markets and one position, for validating collateral, LLTV, bad debt and any LTV you published:

```
markets(where:{uniqueKey_in:[...], chainId_in:[1]}){ items{ marketId lltv badDebt{usd}
  collateralAsset{symbol} state{ supplyAssetsUsd borrowAssetsUsd liquidityAssetsUsd utilization }
  supplyingVaultV2s{ address name } } }
marketPosition(userAddress:"<borrower>", marketUniqueKey:"<key>", chainId:1){
  state{ collateralUsd borrowAssetsUsd } healthFactor priceVariationToLiquidationPrice }
```

Schema traps that cost calls: the filter is `uniqueKey_in`, not `marketId_in`, and `Market` has no `uniqueKey` field to *select* — select `marketId`. `positions` takes no `orderBy`; fetch and sort locally. `absoluteCap` at `2^128-1` means unlimited. `relativeCap` and `forceDeallocatePenalty` are WAD, so `/1e16` for percent. `liquidityData` is a union and needs an `... on` fragment. A non-null `abdicatedAt` means that timelock was permanently renounced, which is a *positive* signal the graph cannot express. The adapter, not the vault, is the supplier of record in `marketPosition`.

| Check | Against | Fail action |
|---|---|---|
| NAV | `totalAssets`, `totalAssetsUsd` | ~0.1% is price and block skew. Beyond that, stop and reconcile |
| **Per-market allocation** | `caps.items[].allocation`, and `marketPosition` on the **adapter** | Reconcile row by row. The ratio test in A1 will not catch a wrong row |
| Market count | number of capped markets | An edge with no cap is dead, not idle capacity |
| Collateral asset and LLTV | `collateralAsset`, `lltv` | Any mismatch invalidates the family grouping |
| **Withdrawal capacity** | `liquidity`, `liquidityData.market` | Never derive it. Take it or omit it |
| **Timelocks** | `timelocks[]` | Replace the scalar with the per-selector schedule |
| **Caps** | `absoluteCap`, `relativeCap` | `uint128` max means reallocation is unconstrained. Say so |
| **Roles** | `owner`, `curator`, `allocators`, `sentinels` | The graph has the first two. Add the rest; drop any row with no role |
| Pending governance | `pendingConfigs` | A queued change with a `validAt` is a live finding the graph cannot show |
| Depositor concentration | `positions`, `pageInfo.countTotal` | Recompute cumulative shares from the protocol's list. Holder coverage can lag, and a partial holder set understates concentration at every rank below the first |
| Any borrower LTV published | `healthFactor`, `priceVariationToLiquidationPrice` | Within ~1pt is fine |
| Bad debt | `badDebt.usd` | The graph has no bad-debt field at all |
| Shared suppliers | `supplyingVaultV2s` | Names every other V2 vault in the same market. No graph equivalent, and a cleaner contagion input than `VAULT_ALLOCATION` fan-in |
| The protocol's own warnings | `warnings` | An empty array is not an all-clear; a non-empty one you omitted is a miss |

**Aave v3 and Spark: `getUserAccountData(address)` on the pool, one call per account.** Selector `0xbf92857c`, Aave v3 pool `0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2`, Spark pool `0xc13e21b648a5ee794902342038ff3adab66be987`, over any public RPC (`eth_call`, and public nodes reject a request with no user-agent). It returns total collateral, total debt, the **account-effective** liquidation threshold and LTV, and the health factor, all in 8-decimal base currency. This is the authoritative answer to three separate traps at once: it prices the collateral at the protocol's own oracle, it counts positions that emit no token, and its threshold is the e-mode figure, so the health factor needs no reconstruction from reserve parameters. On a measured account it returned a 95% threshold where the reserve-level figure is far lower. Sweep `getReservesList()` (`0xd1946dbc`) plus `getReserveData(asset)` (`0x35ea6a75`, aToken at word 8, variable debt token at word 10) and `balanceOf` each to learn *which* asset a levered account actually holds: on a measured vault that is how a $292.19M rsETH position turned up in a book everything else described as wstETH.

**Morpho Blue: `userByAddress(address, chainId){ marketPositions { ... } }`** on the same GraphQL endpoint, which returns collateral, borrow and health factor for escrowed positions that have no on-chain receipt token and are therefore invisible to any balance-based read. An empty `marketPositions` is a real absence and is worth having.

**No route is established yet for Euler, Pendle or Maple.** On those, either find the protocol's own API or subgraph and record what you used, or state in the caveats that no external cross-check was run and which figures are therefore graph-only. **Do not silently skip this phase** — an unvalidated report that does not say so is the failure mode it exists to prevent. Add any route you establish here.

---

## Mode B: protocol control closure

**The question:** every contract and every key that can, directly or transitively, cause a user of this protocol to lose money, each sized by the value beneath it, each traced until it terminates.

A protocol has no NAV to split, so mode A's frame produces a report about the wrong thing. The output answers questions the vault frame cannot ask: how many distinct private keys sit above deposits and what the largest single one reaches; which contracts are the protocol's own versus merely called; and where the closure collapses onto one node.

**Stopping at one or two hops is the characteristic failure.** USDC is not a leaf: beneath it are minters, a blacklister that can freeze a vault's balance, a pauser, a proxy admin that can replace the implementation, and a master minter whose own owner is a further hop down. A report listing "USDC" as a dependency row has not done the work.

### B1. Fix the denominator

```cypher
MATCH (n:Entity {graph_id:'risk-graph-rt-v3', lending_protocol:$proto})
WHERE n.subcategory = 'lending_market' AND n.total_supplied_usd IS NOT NULL
RETURN count(*) AS mkts, round(sum(toFloat(n.total_supplied_usd))) AS supplied,
       round(sum(toFloat(coalesce(n.total_borrowed_usd,0)))) AS borrowed
```

Scope on `lending_protocol`. Then set the expansion floor: everything above it gets recursed to terminal, everything below is carried as **one named aggregate row with its dollar figure**, never dropped silently. $1M is a good default on a $100M+ protocol.

### B2. Separate internal from external

```cypher
MATCH (n:Entity {graph_id:'risk-graph-rt-v3', project:$brand})
WHERE n.subcategory IN ['contract','admin','vault','protocol']
RETURN n.id, coalesce(n.primary_label,n.label,n.blockscout_name,n.nametag) AS label,
       n.subcategory, n.is_proxy, n.at_risk_admin_at_stake_usd, n.max_key_value_usd
LIMIT 60
```

Here `project` **is** the right field, because the question is branding rather than value. Classify each internal contract by the supply it reaches: entity-wide (a factory beacon, an EVC), partial (one governor's markets), or negligible. Then list the external contracts the protocol calls: reserve assets, oracles, bridges, wrappers. Different failure modes and different owners. The protocol can fix the first list and can only monitor the second.

### B3. Walk the closure

Breadth-first up `ADMIN_CTRL` from every market, asset, oracle and internal contract above the floor. Use a bounded variable-length path rather than hand-rolling frontiers:

```cypher
UNWIND $roots AS rootId
MATCH (root:Entity {id:rootId, graph_id:'risk-graph-rt-v3'})
WITH root
MATCH p = (root)<-[:ADMIN_CTRL*1..4]-(ctrl:Entity {graph_id:'risk-graph-rt-v3'})
RETURN length(p) AS hops, [n IN nodes(p) | n.id] AS path,
       ctrl.subcategory AS sub, coalesce(ctrl.primary_label,ctrl.label,ctrl.blockscout_name) AS lbl,
       coalesce(ctrl.safe_threshold, ctrl.multisig_threshold) AS thr, ctrl.is_contract AS isC
ORDER BY hops LIMIT 70
```

Note the arrow: `<-[:ADMIN_CTRL]-` walks **up** the control chain. Two or three roots per call stays inside the guard. Depth 4 is safe; 5 starts timing out on dense roots. Filter a known hub out with `NOT $hub IN [n IN nodes(p) | n.id]`. Keep a visited set: the graph has cycles, since a token can be an admin of its own supply-control contract.

**Terminate a branch on one of three conditions and record which:**

| Condition | Test | Meaning |
|---|---|---|
| EOA | `is_contract = false` and `safe_probe_status = 'not_safe'` | A single private key. Terminal and reportable. |
| Multisig | `safe_probe_status = 'ok'` and a threshold present | Terminal at the Safe; its owners are the keys. |
| Cycle | Already visited | Stop. Do not report as new. |

Depth 4 is normal and 5 not unusual: markets to governor to an unlabelled `DEFAULT_ADMIN_ROLE` holder to a Safe to one EOA is a real shape, and it is invisible to anything that stops at hop 1.

### B4. The fan-out guard

**Check inbound admin degree before expanding any node.** Some contracts are role *registries*, and the graph attaches every role holder across a whole protocol family to them. Expanding one injects dozens of unrelated parties into the closure as though they controlled the asset.

```cypher
UNWIND $frontier AS bId
MATCH (b:Entity {id:bId, graph_id:'risk-graph-rt-v3'})
WITH b
MATCH (b)<-[:ADMIN_CTRL]-(a:Entity {graph_id:'risk-graph-rt-v3'})
RETURN b.id, count(DISTINCT a.id) AS inboundAdmins,
       count(DISTINCT a.subcategory) AS distinctSubcats, collect(DISTINCT a.subcategory) AS subcats
ORDER BY inboundAdmins DESC
```

**More than about 50 inbound admins spanning 4 or more subcategories is a hub. Do not expand it.** Emit one row recording the degree and mark the branch unresolved. Real control sets cluster at 15 to 20 inbound admins across 1 to 3 subcategories, so the threshold is well separated and not a judgement call. Skipping this check can add dozens of spurious multisigs from unrelated protocols, every one reading as a controller of the asset.

### B5. Expand each external asset's own control plane

For every reserve or collateral asset above the floor, the asset **is** a subgraph. Pull its roles two ways, because they differ in coverage:

```cypher
MATCH (n:Entity {graph_id:'risk-graph-rt-v3', id:$asset})
RETURN n.admin_roles AS rolesJson, n.max_key_value_usd AS maxKey,
       n.is_proxy, n.proxy_type, n.owner_discovery_status, n.role_member_discovery_status
```

`admin_roles` is a JSON **string** mapping address to role names. Parse it, then run the edge query on the same node and reconcile: the edge carries `r.role`, which the property does not always match in case or naming.

**Rank roles by what they can do to a depositor, not by name.** The ones that matter are rarely called "owner":

- **blacklist, freeze, clawback, asset protection** can strand a vault's entire balance with no exploit and no protocol involvement. This is the most under-reported loss path in DeFi risk analysis.
- **pause** halts transfers, so it halts withdrawals and liquidations.
- **proxy admin, upgrader** can replace the implementation entirely.
- **minter, master minter** can dilute, or with a bad price mint against the protocol.

### B6. Classify every empty admin set

An empty set means two opposite things, and the node tells you which:

```cypher
UNWIND $ids AS nId
MATCH (n:Entity {id:nId, graph_id:'risk-graph-rt-v3'})
RETURN n.id, n.admin_roles, n.is_contract, n.is_proxy,
       n.owner_discovery_status, n.role_member_discovery_status,
       n.safe_probe_status, n.safe_probe_not_safe_reason
```

- `admin_roles = '{}'` **with** a discovery status of `ok` or `success` is a **verified absence of admin control**. Report it as a genuine strength: an immutable contract with no admin is a real finding and worth stating plainly.
- `admin_roles = '{}'` **with** a null discovery status is **not confirmed either way**. Say the control set is not confirmed on-chain, and never say "no admin".

Getting this backwards makes something look safer than it is, which is the direction that costs money. When it matters to the conclusion, resolve it on-chain rather than leaving it unresolved.

### B7. Choke points and cross-layer overlap

**Size by union, computed bottom-up.** A node's exposure is the union of its own positions and every descendant's, so a parent is never the sum of its children. Assert the invariant before shipping: `parent.usd >= max(child.usd)`. A key on two Safes covering the same market is one exposure.

**Rank keys, not just contracts.** The interesting output is a table of EOAs ordered by supply reachable.

**Then look across layers for shared identities.** This is where the highest-value findings live and no single query produces them:

- Compare the signer sets of curator or governance Safes against the admin sets of the assets those vaults are denominated in. Curator independence from an issuer is the entire premise of an isolated-vault model, and overlapping signers mean it does not hold.
- **One key controlling both an asset and its price feed.** Intersect the admin sets of every asset with those of every oracle. The same party setting the asset and what the asset is worth collapses two supposedly independent risks into one.
- **One Safe holding several roles over the same asset.** Count distinct roles per admin address, not just admins per asset. Counting admins can make a setup look diversified when one Safe is default admin, upgrade admin, keeper admin and curator.
- Group borrower rows by owner prefix on EVC protocols before ranking concentration.

**Beware the floor artifact.** If you exclude positions below a floor, entity-wide contracts read as 97% rather than 100% and look like they miss something. Carry the sub-floor remainder as one named aggregate and include it in entity-wide rows. Where a figure still differs from a direct query because of truncation, state both and explain the gap.

---

## Mode C: blast radius

**The question:** if this thing is compromised, who loses money and how much?

### C1. Pick the sub-mode first. Picking wrong fails silently

| The user gives you | Sub-mode | Question answered |
|---|---|---|
| A dependency only | **Ecosystem-wide** | Rank everyone who loses. "What breaks if this asset fails?" |
| A dependency **and** a specific vault, wallet, Safe, protocol or address | **Targeted** | One number for that one party, plus the chain that carries it. "What does this asset's failure cost *this* vault?" |

**Targeted is not ecosystem-wide filtered to one row.** Two failure modes, both silent:

- **The floor is anchored to the wrong denominator.** Ecosystem-wide prunes at a fraction of the *subject's* size. A $2M path is noise against a multi-billion asset and gets pruned at hop 2 — and it can be 100% of the target's portfolio. The answer is discarded before the traversal reaches the target, and the output looks complete.
- **The frontier explodes before it arrives.** A major asset has hundreds of markets, thousands of pools and thousands of borrowers at hop 1. Expanding that outward to depth 4 will not survive the cost guard, so you never get deep enough to find a target six hops away.

So targeted mode traverses **from the target inward toward the dependency** — the direction most dependency edges already point — with the frontier bounded by the target's own portfolio. C2 applies to both; then take C3 or C4.

### C2. Direction, and why the obvious fields cannot answer this

**`AT_RISK` cannot answer this question,** even though `at_stake_usd` looks exactly like the metric you want. Those edges run **failure surface to focus token** and are in practice inbound only on any asset worth analysing: thousands inbound, often a single outbound carrying a trivial sum. The type answers "what can hurt this token", which is Mode A. There is no outbound `at_stake_usd` to read.

`at_stake_usd` on **inbound holder edges** is still useful: for a holder, exposure and amount at stake are numerically the same, and it cross-checks `HOLDS.usd_value` to the dollar.

**Precomputed impact and cascade node fields are neighbourhood aggregates, not directional ones.** They span both directions and overlapping paths, so their sum runs above the subject's own size and includes nodes the subject cannot damage. Orientation only; traverse structural edges for any figure that goes in a report.

**Take canonical direction from `get_schema`, every time.** This table deliberately does not repeat it. What it carries is the part the schema cannot tell you: for *this* question, which way to walk relative to canonical, and what fraction to size the hop with. Walking **from the dependent party toward the thing it depends on**, almost everything runs with the canonical direction and the exceptions are what bite:

| Edge | Toward the dependency | Fraction |
|---|---|---|
| `HOLDS` | **against canonical**, match on the holder | `usd_value` / target NAV |
| `LENDING_COLLATERAL` | **against canonical**, from a market to its collateral | see C4 |
| `CURATES` | **against canonical**, to find your curator | 1.0 |
| `VAULT_ALLOCATION` | with canonical | `share_pct`, `allocated_usd` |
| `VAULT_ASSET` | with canonical | denomination, 1.0 |
| `ORACLE_DEP` | with canonical | 1.0 where `value_defining` is set. On a **token** node that property does not exist: use `oracle_pricing_type` (server instructions, rule 8) |
| `RECEIPT_FOR` | with canonical | 1.0 |
| `WRAP_UNWRAP` | with canonical | 1.0. A backing edge despite its category; check its population live rather than assuming |
| `BACKED_BY` | with canonical | impaired backing / total backing |
| `RESERVE_BACKING` | with canonical, and it lands on a **treasury address**, not a backing asset | not a fraction |
| `BRIDGE_BACKED_BY` | with canonical | 1.0 on the wrapped supply |
| `DEBT_FOR` | with canonical | 1.0 |
| `SUBORDINATE_TO` | with canonical | first-loss, junior absorbs first |
| `POOL_ASSET` | with canonical | 1.0 for an LP, **not** the token's weight |
| `ADMIN_OF` | with canonical | 1.0, or `extractable_usd` |
| `CUSTODY_VIA` | with canonical | 1.0. The hub is a super-node |
| `LENDING_BORROW` | with canonical, debt side | see C4, the sign flips |

Three traps in that table specifically:

- **`LENDING_COLLATERAL` walked the wrong way does not error, it answers a different question.** For a supplier vault, the collateral standing behind its loans is reached against canonical, from the market. With canonical from a token you get which markets accept it as collateral, which is who-depends-on-me, not what-I-depend-on. Both return rows; only one is your question.
- **`RESERVE_BACKING` does not decompose backing.** It lands on a treasury that holds some. A treasury holds many unrelated assets, so reading its holdings as the token's backing manufactures dependencies that do not exist.
- **`BACKED_BY` needs its `source` filtered before it is a decomposition** — see the type's own `meaning` in `get_schema`, which names the case that lands on an aggregator rather than on a backing asset.

**The two path helpers do not answer this question.** Both are tempting and both return confident false positives. `find_path` is semantically blind: it returns a shortest chain over edges regardless of meaning, and will happily stitch `HOLDS` walked as "things that hold the subject" onto an `AT_RISK` edge — two true edges and no transmission path. `dependency_closure` follows `LENDING_COLLATERAL` outbound only, so on a lending vault it cannot see the collateral behind its own positions, and it can report a base asset as reached via an unrelated treasury's holdings. Check its truncation too: it can record nodes as leaves well inside `maxHops` when an internal row budget binds. **Use both for candidates. Never to size, and never to conclude absence.**

**Turn a zero into a measured zero with `available_traversals`.** An empty `read_cypher` result means unknown; `absentTypes` means counted absent. That is the difference between "this market has no borrowers" and "I cannot see this market's borrowers", and only one is a finding.

### C3. Ecosystem-wide

**Denominator first, and it is where this goes wrong most expensively.** Resolve as in A1, then look at holder composition before dividing by anything:

```cypher
MATCH (n:Entity {id:'<subject>', graph_id:'risk-graph-rt-v3'})-[r:HOLDS]->(m:Entity {graph_id:'risk-graph-rt-v3'})
WHERE r.usd_value IS NOT NULL
RETURN m.id AS id, coalesce(m.primary_label,m.label,m.name,m.blockscout_name) AS label,
       m.subcategory AS sub, m.project AS proj, round(toFloat(r.usd_value)) AS usd
ORDER BY usd DESC LIMIT 30
```

If controllers, peg keepers, flash lenders or factories dominate, `total_supply_raw` is not your denominator: for a CDP stablecoin use **collateral-backed debt**, summed across every controller. Getting this wrong can turn a real 47% concentration into an apparent sub-1% non-event. For an ordinary token, market cap is fine.

**Hop 1 is everything holding the subject**, pro rata: the query above, outbound, because `HOLDS` runs token to holder. Then walk outward along the types the subject actually has, read from the C2 table backwards — outbound `HOLDS` for holders, inbound `LENDING_COLLATERAL` for markets where it is collateral, inbound `POOL_ASSET` for pools, inbound `RECEIPT_FOR` for the claim layer, `ADMIN_CTRL` for an admin subject.

Recurse until impact falls below the floor: **0.1% of the denominator or $1M, whichever is smaller**, and state the floor. No fixed hop limit — depth is not a proxy for importance, and the largest single victim often sits at hop 2 while a small one sits at hop 3. Depth 4 to 5 is normal. Loops terminate on their own once you attenuate, because each pass multiplies by a fraction below 1; report a cycle as a named loop rather than unrolling it.

**Sizing. Four rules, each an order of magnitude if you get it wrong.**

**1. Attenuate. Never propagate a downstream node at full value.**

```
impact at hop n+1 = downstream node value x (impaired backing / total backing at that node)
```

Without this, transitive closure eats the graph: an asset that is a fraction of a percent of a large stablecoin's backing propagates as the whole stablecoin, and the total exceeds the subject's own market cap several times over. Low-fraction paths still belong in the output, with the percentage and an explicit "this does not break the peg" — "exposed at 28bps" is a useful answer, not a row to suppress. High-fraction paths must not be attenuated away either: a subject that is 47% of something's backing propagates at close to full value.

**2. Separate additive dollars from re-attributed dollars.** Different tables, never summed across.

- **Additive**: bad debt in a loan asset, AMM counter-legs, anything denominated in something other than the subject. New dollars, legitimately pushing the total above the subject's market cap.
- **Re-attribution**: receipt-token holders, bridged supply. The same dollars with better-named victims. Adding these double-counts the largest position in the analysis.

**3. Union within a family, never sum.** A token, its admin, its custodian and its oracle all point at the same position. Label family subtotals as unions.

**4. Mechanism-specific sizing.**

- **AMM pools**: LP loss is the *whole pool*, not the subject's leg, because a worthless asset gets arbitraged against the paired asset. Counter-leg from inbound `HOLDS` on the pool. Amplification runs about 1.0x to 3.3x. **AMMs only** — a vault has no arb drain, so its counter-leg is not lost.
- **Isolated lending markets** (single collateral): bad debt = amount borrowed.
- **Multi-collateral markets**: pro-rate by the subject's share of the market's collateral, from inbound `HOLDS` on the market node.
- **`max_borrow_capacity_usd` is a ceiling, not a measurement.** Some protocols expose it without exposing how much is actually drawn against this specific collateral. Label it a ceiling every time.
- **Borrowers flip sign.** Inbound `LENDING_BORROW` is debt denominated in the subject. If the subject becomes worthless those borrowers *gain* and the suppliers lose. Never add debt-side rows to an ecosystem-wide blast radius.
- **Minted-against is not lent-against.** Collateral backing issuance means peg risk. Collateral in a market where a stablecoin is *lent* means the stablecoin stays fully backed and the loss falls on its suppliers. Collapsing these is wrong in both directions.

### C4. Targeted

**Resolve both ends, then classify the target**, because its denominator and its inward vocabulary both depend on the class:

| Target class | Denominator | Where value sits |
|---|---|---|
| Vault | Σ `allocated_usd` plus idle, with the A1 ratio caveat | Outbound `VAULT_ALLOCATION`, `VAULT_ASSET` |
| Wallet or Safe | Σ inbound `HOLDS.usd_value`, and nothing else. Cross-check anything material externally | Inbound `HOLDS` |
| Protocol | Σ over its markets and vaults, deduped, scoped on `lending_protocol` | Its own `lending_protocol` set |
| Token or receipt token | Market cap, subject to the CDP warning in C3 | Outbound `HOLDS` for holders |

**Every percentage in targeted mode is a share of the target's assets, never the subject's.** The mode exists to answer "what does this cost *me*"; a percentage of the subject's market cap answers a question nobody asked.

**Expand inward from the target**, one query per (frontier, edge type). **Do not use a variable-length pattern** (`*1..6`): the cost guard refuses it, and you lose the per-hop fractions you need for sizing.

```cypher
UNWIND ['<frontier ids>'] AS aId
MATCH (a:Entity {id:aId, graph_id:'risk-graph-rt-v3'})
WITH a
MATCH (a)-[r:VAULT_ALLOCATION]->(b:Entity {graph_id:'risk-graph-rt-v3'})
RETURN a.id AS src, b.id AS dst, r.allocated_usd AS usd, r.share_pct AS frac,
       r.adapter_address AS adapter, b.category AS cat, b.subcategory AS sub,
       coalesce(b.primary_label, b.label, b.name, b.blockscout_name) AS label
ORDER BY usd DESC LIMIT 200
```

- **Keep a visited set.** A node reached twice on one branch is a cycle: stop, name the loop, do not unroll.
- **Carry the running product.** Each frontier entry is `(node, path so far, cumulative fraction, position value at the top)`. Never prune on depth.
- **Prune on the TARGET's floor:** 0.5% of the target's NAV or $10,000, whichever is smaller. State it.
- **Project `primary_label`.** On whole clusters of intermediaries `label`, `name` and `blockscout_name` are null or generic while `primary_label` carries the real attribution. An unnamed intermediary is a path the reader cannot check.
- Batch frontier ids in groups of about 10; wider lists trip the guard.

**Then expand outward from the subject 2 to 3 hops and intersect.** A node in the intersection is a confirmed junction, and meeting in the middle is what lets you reach 7 or 8 hops total without either side exceeding the guard.

**When the two sides do not meet, that is a finding, not a null result.** The usual cause is allocations pointing at a protocol singleton rather than market ids, so the inward walk arrives at a node with thousands of markets beneath it and no way to tell which. Report the resolved part and the unresolved allocation as an explicit upper bound: "$X resolves to named markets, of which the subject reaches $Y; a further $Z routes through the singleton where the breakdown is unavailable, so $Z is an upper bound on additional exposure."

**Size each path** as position value at the top × the product of the transmission fractions along it.

- **Direct holding** (or a receipt token or wrapper for it): loss is the position value, fraction 1.0. Count the receipt route or the underlying route, never both.
- **LP position in a pool containing the subject**: loss is the **whole LP position**, fraction 1.0 capped at the position value.
- **Supplying a lending market where the subject is collateral** — the target does not hold the subject at all, and this is the most common way naive sizing overstates. Under full compromise the borrowers walk and the suppliers eat the debt the subject was backing:

  ```
  loss = (target's allocation / market total_supplied_usd) x (debt backed by the subject)
  ```

  Isolated market with the subject as sole collateral: debt backed = the market's `total_borrowed_usd`, so this reduces to `allocation x utilisation`. Utilisation is typically 0.80 to 0.95, so propagating the allocation whole overstates by whatever share sits idle. Multi-collateral: multiply again by the subject's share of the market's collateral, taken from the market's inbound `HOLDS` rather than assumed.

  Report **exposure** (allocation sitting behind the subject) and **loss under compromise** as two separate columns. They answer different questions and the gap is real.

  Four things that change these numbers:

  - **Cap the supply share at 1.0.** A vault's `allocated_usd` can exceed the market's own `total_supplied_usd` — an impossible state, usually refresh skew between two writers. Cap it, and **report the excess rather than hiding it**: a large discrepancy means the two figures disagree and the analysis does not establish which is wrong.
  - **`total_collateral_usd` is effectively unpopulated on several protocols, and absence is not zero.** On those, the loan side is the only basis available. Say that collateralisation was not independently checked.
  - **Check `LENDING_BORROW` with `available_traversals` before promising a cross-pipeline check.** Comparing `total_supplied_usd - available_liquidity_usd` against the sum of per-borrower debt is the strongest free corroboration available, but it is a measured zero on some markets, and then the borrowed figure is simply uncorroborated. Say so rather than implying it was verified.
  - **Check whether the target is the market's only supplier.** If it is, the share is 1.0 and the loss reduces to the market's whole debt — simpler and larger than a pro-rated guess. Read the market's inbound `VAULT_ALLOCATION` and ignore the $0 sibling edges.

- **The base asset is often unreachable, and that is the finding.** Where the subject is a base asset, exposure usually arrives through a wrapper or staking derivative, and the last hop may not exist in the graph: a wrapped LST resolves to its liquid form and stops, with no backing edge onward to the native asset. A traversal trusting only graph edges then concludes a vault with tens of millions of staking collateral has **zero** base-asset exposure. Do not report that. State the derivative chain you measured, then assert the final hop from outside the graph and **label it asserted**, with a fraction of 1.0 where the derivative is a pure claim. A staking derivative is not partially backed: under base-asset-to-zero it goes to zero too, so there is nothing to attenuate.
- **The mirror trap is a hedged derivative.** A delta-neutral synthetic is base-asset-linked but not base-asset-exposed, and the graph models neither leg. Report it unresolved with an upper bound, named as "loss if this token were fully impaired", never as an exposure estimate.
- **Borrowing against the subject as collateral**: the sign flips, but not to zero. The target posted `C` and drew `D`; if the subject goes to zero it abandons the position, keeping `D` and losing `C`. Loss is its **equity**, `max(0, C - D)`. Never put gross collateral in a loss column, and never drop the row because borrowers gain.
- **Oracle dependency**: fraction 1.0 over the positions the feed actually prices, but only where `value_defining = true`. Thin coverage, so a zero-row result means the price path is **unresolved**, not absent. Name every position it failed to resolve, with its dollar figure.
- **Admin key or upgrade control**: fraction 1.0 over the value beneath it, or `extractable_usd` where present. Prefer node-level at-stake stamps for the headline and edges for attribution. Check `safe_probe_status` before writing that governance is unmodelled — a definitive `not_safe` is an EOA, which is a finding. Report `timelock_delay_seconds` on the path.
- **Stablecoin or derivative backed partly by the subject**: fraction = the subject's share of that token's backing. Attenuate, never propagate whole.

**Dedupe, then sum. Key every path on its terminal position** — the leaf where the target's dollars actually sit: a market id, a pool id, or a token for a direct holding.

- **Within a position key, take the maximum across channels. Never sum.** One allocation can depend on the subject as collateral, through its oracle, and through its admin key. That is one set of dollars with three reasons, and summing triple-counts the largest position.
- **Across distinct position keys, sum.** Two allocations to two markets are different money.

**The hard invariant: a targeted total can never exceed the target's own NAV.** If it does you have double-counted, so go back to the keys. This is the **opposite** of the ecosystem-wide rule, where the total legitimately exceeds the subject's market cap because counter-legs and loan assets are new dollars — carrying the wide-mode intuition into targeted mode means accepting an impossible number. The one real exception is a leveraged target, where loss can exceed equity; say so explicitly if you invoke it.

**Name the vector, and never max across vectors.** "The subject is compromised" is not one event. Value-to-zero, a wrong price, and a drained admin key reach the target through different channels and produce different numbers. Pick the vector from the subject's class: an asset defaults to value-to-zero, an oracle to a wrong price, an admin key or upgrade proxy to a full drain, a custodian to loss of the custodied balance, a bridge to wrapped supply becoming unbacked. Take the max across channels **within** a vector; report vectors separately.

Mechanism and vector must agree, and it is mechanical enough to check:

| Mechanism | Transmits |
|---|---|
| Direct holding, receipt, LP, backing share | value to zero, custody loss, unbacked supply |
| Market collateral, isolated or multi | value to zero, wrong price, unbacked supply |
| Borrower equity | value to zero, unbacked supply |
| Oracle | wrong price **only** |
| Admin or upgrade control | drain **only** |

So under value-to-zero the oracle channel is not part of the answer. It is a real path and it belongs in the output, **held out and labelled, never dropped** — a path silently missing reads as a path never found. `scripts/build_blast_report.py` enforces this from `subject.vector_class` and prints what it held out.

Where the subject shares an admin, issuer or custodian with other assets, the common-cause version is a genuinely different and often much larger answer. Its own labelled section, never mixed into the path table.

### C5. Group and rank

Ecosystem-wide: group by **family** — the protocol or asset the impacted nodes belong to — not by abstract layer. A node shared across families goes at top level.

Targeted: the deliverable is the ranked path table, an intermediary register ranked by how many paths run through each node, positions with **no** resolved path to the subject, and whatever remained unresolved or bounded. Build both with `scripts/build_blast_report.py`, which takes `mode: "wide"` or `mode: "targeted"`.

---

## Delivering the result

**A quick question gets a chat answer.** Lead with the number, name the dependency or the victim, give the evidence. No file.

**A report request gets the HTML build.** `scripts/build_report.py` renders mode A from a findings JSON, `scripts/build_blast_report.py` renders mode C (`mode: "wide"` or `mode: "targeted"`); `scripts/build_closure_table.py` renders mode B as a single interactive table. Run either with `--schema` for the field list, and see `assets/` for a worked example of each. Extend the scripts rather than hand-building HTML: they own the union arithmetic, the ranking and the banding, so the tables cannot drift from the figures.

```bash
python3 scripts/build_report.py findings.json -o report.html
python3 scripts/build_report.py findings.json --check    # arithmetic only, no HTML
python3 scripts/build_closure_table.py tree.json -o closure.html
```

For mode B, an admin node carries **the positions of whatever it controls**, since exposure rolls up as the union of a node and its descendants. A key with an empty position set and no descendants reads as reaching nothing, which is wrong if it sits above a market. Assert the invariant before shipping: a parent is never below any of its children.

For mode B, deliver **one table, not many sections**. A protocol closure has 100+ nodes across 5 hops, and splitting it per layer forces the reader to hold the cross-references in their head, which they will not do. One expandable table, indented by hop depth, collapsible back to a one-screen overview. Required columns: dependency (with an inline note), address, type, category, control, failure mechanism, exposure USD, share, markets.

**State the failure mechanism per row**, because "compromised" means different things: `bug in code`, `key compromise`, `implementation swap`, `freeze`, `clawback`, `mint`, `stale or manipulated price`, `liquidation shortfall`, `custodial failure`. A reader stress-testing a freeze row with a price shock gets the wrong answer.

On a wallet, mark each bucket as held directly, reached by look-through, or mixed, and as encumbered or free, **in the bucket name**. The rollup has no field for it, and without it a look-through bucket reads as a direct holding, which inverts the risk.

Save the report where the user can reach it, then present it with the file-presenting tool rather than pasting its contents into chat.

---

## What the output is

**The report is a data product. Tables, and as little else as possible.**

**No prose sections at all.** No bottom line, no executive summary, no recommendations, no caveats, no key-findings, no takeaways, no method essay, no closing commentary. If a sentence is not qualifying a specific number in a specific row, it does not go in. The renderers no longer emit any of these, and a report that reintroduces them by hand is wrong.

What a report contains, and nothing more:

| Element | Content |
|---|---|
| Header | Subject, denominator, chain and snapshot. One line each |
| Ranked table(s) | The data. Every row a thing that can fail, with its dollars and its share |
| Row-level qualifier | Where a number is unsettled, the qualification sits **in the cell** |
| Footer | Source, snapshot, floor, and the sizing basis in one compact line |

**A lead-in is at most one line, and only where a table needs an anchor.** "Eight markets, effectively one bet" earns its place ahead of a table that shows exactly that. A paragraph restating the table does not.

**Uncertainty goes into the cell, never into a section.** With no caveats section, an unqualified row implies the figure is settled, so anything material and unsettled carries its qualification in the row:

| Situation | How the row reads |
|---|---|
| Price authority not resolved to one feed | "Oracle candidates listed; not confirmed to a single feed" |
| A Safe's signer set not readable on-chain | "Signer set not confirmed on-chain" |
| A custodian, fiat reserve or governance process | Name it as an off-chain arrangement, outside on-chain scope. A property of the thing, not a limitation to apologise for |
| A balance older than the snapshot | Append the as-of date to the row name |
| A figure with a genuine range | Give the range in the cell |
| An upper bound rather than a measurement | Label it a bound, and keep it out of the total |

**State the chain and the snapshot in the header.** "Ethereum mainnet, `<date>`". Where the subject has deployments elsewhere, name them as out of scope on the same line — an analysis boundary, not a caveat.

**Keep out entirely:** discussion of how the underlying data is assembled; tool names, query text and identifiers that mean nothing to the reader; and any recommendation about tooling rather than the subject.

**That rule is easy to agree with and easy to break, so here is the checkable version.** Before shipping, search the report for schema identifiers and pipeline vocabulary. Any hit outside the footer is a defect. The uncertainty itself always survives the rewrite: what changes is whose problem the sentence sounds like. A caveat phrased as an admission about the data reads as a warning about the product; the same fact phrased as a property of the position reads as analysis, and it is the second one that is true.

| Do not write | Write instead |
|---|---|
| "the graph wrote these 60,779 blocks apart" | "the collateral is dated 11 Aug and the debt 3 Aug, an eight-day spread" |
| a raw block number in the body | the date it corresponds to. Block numbers belong in the footer, where reproducibility lives |
| "no `value_defining` oracle on this market" | "no feed on this market is identifiable as the one that sets its price" |
| "roles read empty with no discovery status" | "the role set could not be confirmed on-chain, which is unknown rather than absent" |
| "the only `BRIDGE_BACKED_BY` edge is a self-loop" | "the only backing reference points back at the token itself, so it resolves nothing" |
| "`BACKED_BY` points at a PoR aggregator, so rejected" | "the only backing reference points at a proof-of-reserve feed rather than a reserve asset, so it establishes nothing" |
| "`oracle_pricing_type` is unset on the token" | "nothing resolves the asset's own backing" |
| "`total_collateral_usd` is unpopulated on this protocol" | "collateralisation could not be checked independently here" |
| "not modelled in the graph" | name the thing as an off-chain arrangement, outside on-chain scope |
| a relationship type name on a diagram arrow | what the link is: holds, is a claim on, accepted as collateral by |

The last row is handled for you: `build_blast_report.py` translates relationship type names to plain phrases at the render boundary, and passes anything unrecognised through, so a plain phrase can always be authored directly.

**A gap is a property of the subject, not a confession.** A custodian, a fiat reserve, an off-chain credit book and a governance process are all real parts of how the thing works, and none of them is on-chain. Say what stands behind the asset and that it sits outside on-chain scope. Never apologise for it, and never imply the analysis is weaker for naming it: an unnamed off-chain dependency is the weaker report.

**Two rules that survive the cull, because they are about numbers rather than narrative.** Never upgrade something unresolved into a verified absence — "no admin control found" and "no admin control exists" are different claims and only one is checkable. And a verified absence *is* a finding, so state it plainly in a row rather than omitting it.

**If the user asks directly what could not be resolved, answer plainly** — which positions, which dollars, what would settle each. That is a reply, not a report section. The rule is about what you volunteer.

## Presentation

- **Tables. Prose is the exception, never the frame.**
- **One line of lead-in maximum**, and only where a table needs an anchor.
- **State the denominator wherever a percentage appears.** A reader cannot check the work otherwise.
- **No composite risk indices** and **no raw node risk scores.** Dollars and shares are checkable; indices are not.
- **Exposure sizing, not expected loss**: full compromise, no probability weighting, no recovery. Once, in the footer.
- Truncate addresses consistently and give enough to verify.
- Avoid em dashes. Use commas, colons, or restructure.

**Name things the way a reader would.** The header is where a report either invites someone in or tells them this was written for somebody else. "Subject", "target" and "vector" are internal vocabulary for the three roles in the analysis, and they belong in the method, not on the page. Say what each one is instead:

| Internal term | What the header says |
|---|---|
| target | **Portfolio analysed** |
| subject | **What could fail** |
| vector / vector_class | **Failure assumed**, written as a plain sentence: "LBTC becomes worthless" |

**Provenance goes in the footer, not the header.** Partition id, path count, hop depth and the floor formula are all reproducibility apparatus. They must appear, because a figure that cannot be traced to a graph version is not reproducible, but they are the last thing a reader needs and they should not be the third line of the report.

**Do not narrate how the denominator was measured.** The figure itself belongs in the header, because every percentage in the report is a share of it and a reader cannot check the work without it. How it was derived is a different thing: it is the analyst's working, it runs to several sentences of reconciliation, and it is the first paragraph a reader skips. Record it in `denominator_basis`, which `--check` prints and the renderer deliberately does not, so the derivation stays verifiable without occupying the page. If the user asks how the number was reached, that is a reply, not a report section.

**The targeted blast report has a fixed shape. Use these headings, in this order, so two reports on different subjects read the same way:**

| | Heading | Holds |
|---|---|---|
| Header | `Impact of <asset> on <portfolio>` | Portfolio analysed, What could fail, Failure assumed. Three lines |
| Hero | two figures, no third | **Current estimated loss** and **Maximum loss**. Never a separate exposure card: on most paths it equals the maximum loss and a reader cannot tell three near-identical figures apart |
| 1 | **Impact and contagion paths** | The diagram, then `Path detail`: the same paths as a ranked table |
| 2 | **Defensive borrowing: what the loss becomes in a rush** | The four constraints per market, binding one marked |
| Close | one reconciliation line | Paths plus unconnected against portfolio total, with the drift |
| Footer | one line | Chain, snapshot, graph partition, reporting floor, and the sizing basis |

Anything that does not fit those rows is not a section, it is a reply to a question the user has not asked yet.

**Number rows, do not expose path ids.** `P1` and `P2` are internal keys for linking a path to its intermediaries, and on the page they read as priority labels. Rank the rows and number them `1..n`; where one row is superseded by another, say "same dollars as row 2" rather than naming an id.

**Name a path for where the money sits, not for the edges it traverses.** A cell reading `-VAULT_ALLOCATION (?)-> MorphoMarketV1Adapter ...` asks the reader to parse the graph's vocabulary to learn something the diagram already showed them. Use the position label, "Morpho LBTC / PYUSD market", and let the diagram carry the route. Never print a bare `(?)` or a placeholder glyph in a cell: if a value is unknown, the row says what is unknown in words.

**A reconciliation line must state both of its addends.** A check that reads "the paths above plus $54.86M account for $154.85M of $154.85M" states the total twice and never states what the paths themselves contributed, so the reader cannot do the arithmetic that the line exists to demonstrate. Give both parts and what they sum to, then say in one clause what the check is for: a traversal that quietly missed something must not be able to look like one that found everything.

**Do not give a column to a percentage derived from two others on the same row.** A table carrying market LTV and LLTV already tells the reader the headroom between them; adding the subtraction as a third percentage makes all three look like independent measurements and forces the reader to work out which are inputs. Derive it for your own reading, quote it in prose where it carries a finding, and leave the column out. The same applies to any ratio whose numerator and denominator are both already columns.

**Qualifying text spans the table, not a column.** A sentence explaining a number, squeezed into one narrow column, wraps to a dozen lines and pushes every figure out of alignment. Put it in its own row underneath with a `colspan` across the remaining columns. And a totals row is labelled **Total**: "deduped by position key, max per key" is the implementation of the total, not its name, and belongs in the method rather than the footer of a table.

**A targeted report opens with a diagram, not a table.** The question "how does this asset reach my portfolio" is a shape, and a chain of boxes answers it in one glance where a table of edge types does not. Draw the spine left to right, portfolio to failing asset, with every intermediate contract on it as its own box: the adapter that routes the money, the market, the asset. Hang the things that sit beside the path, oracles, bridge lockboxes, admin role sets, underneath the node they attach to. The diagram replaces a separate intermediary register, so do not also table what it already shows. `build_blast_report.py` draws this from `paths[].chain` plus any `intermediaries` carrying an `attach` label, as inline SVG with no external dependency.

**Keep the labels short and let the layout do the rest.** SVG has no text metrics, so the script breaks lines against an estimated advance width, grows each box to fit what it holds, and sizes the canvas from the rightmost and lowest element drawn, branch boxes included. Two failure modes are worth knowing because both look like a rendering bug rather than a data problem: measuring text at one font size while the CSS renders it at another puts the text outside its box, so `BR_LBL_PX` and `BR_SUB_PX` must track the `.nb` rules; and sizing the canvas from the spine alone clips the branches hanging off the last column. A node label over about 40 characters or a sub-label over about 30 will still wrap to a second line, which is fine, but prefer a short contract name with the detail in the sub-label.

## Verify before delivering

### Coverage gate: run this before the arithmetic gate

**Every market where the subject is collateral carries a defensive-borrowing line, or a stated reason it does not.** A loss figure equal to current borrowings, with the unborrowed remainder called recoverable, is the signature of having skipped it. Check that `borrow_cap_usd` was read and not coalesced: a null cap silently zeroes the extra borrowing and deletes the effect.

**`meta` read on every response a figure came from.** `page` for truncation, `declared` for which guarantees that response actually carried, `asOf.block` for what it is true at. A figure quoted from a response whose `page.truncated` was true is a lower bound and must say so.

**Report searched for schema identifiers and pipeline vocabulary.** Relationship type names, property names, block numbers outside the footer, "the graph", "the partition", "writer", "edge". Any hit in the body is a defect, and the fix is the substitution table in Presentation, never deletion of the underlying caveat.

**Escrow stated on any levered subject.** Escrowed collateral is absent from the graph, so a levered entity is understated. Say it in the report, not only where a tool returns `unresolved`.

The arithmetic checks below pass on a report that is 40% complete, so they cannot catch a shallow walk. **Answer every applicable line with a number, not a feeling.** Where the answer is "not checked", either check it or put an explicit unresolved row in the table. Never ship a blank.

**Any mode**

- Every position above the floor: does it appear in at least one dependency row?
- Every branch you stopped walking: which of the three termination conditions ended it, recorded per branch?
- Every empty admin set: classified as verified absence or unconfirmed, from the discovery status?

**Mode A, per funded market**

- `value_defining` oracle identified, or the price path explicitly recorded unresolved?
- Collateral asset's role set pulled, and each privileged role named (blacklist, pause, upgrade, mint) rather than summarised as "admins"?
- Backing chased, and self-loop and PoR-aggregator edges rejected rather than counted as coverage?
- Collateral name checked for a maturity date against the snapshot?
- Borrower concentration row present? A4 calls this the primary loss path; a lending-vault report without it is incomplete.

**Mode B**

- Every external reserve and collateral asset above the floor expanded per B5, or listed with its dollars and marked unexpanded?
- Fan-out guard run before expanding each node?

**Mode C**

- Every target position either carries a path or appears in `unconnected`?
- Every path's mechanism able to transmit the declared vector, with non-transmitting paths held out and labelled?

**A smell test that costs nothing.** On a lending vault, each funded market normally yields five to eight dependency rows once collateral, its roles, its backing and the oracle chain are in. A report whose total row count is close to its market count has skipped layers, whatever its arithmetic says.

### Arithmetic gate

- **Every derived figure built from one snapshot.** For each number combining two reads — a pro-rata share, a ratio, a difference — check the `asOf.block` and `last_stat_block` behind both sides. If they differ, requery; do not ship the figure with the gap disclosed in the row. A share computed from a numerator and denominator read days apart is not a measurement of anything.
- **Every debt figure traceable to a debt-position edge, with its account's protocol named.** A debt total assembled from token balances fails this check, and it fails silently: it is short by every protocol that mints no debt token.
- Recompute every union and share in a script. Components must sum to their stated totals, and every percentage must reconstruct from its dollar figure and the stated denominator.
- No percentage above 100%. Group impact at or below the sum of its rows, and equal to the union of their positions.
- Every headline figure traceable to specific named positions.
- If you correct a figure, restate it **everywhere** it appears, including summary cards and the opening paragraph. Stale headline numbers beside corrected tables destroy trust in the whole document.
- Rounding drift of a dollar or two across independently rounded subtotals is fine. A figure that cannot be reconstructed at all is not.
