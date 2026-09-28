---
name: forta-risk-analysis
description: "Analyse on-chain dependency and control risk with the Forta Risk Graph. Answers three questions: what could make a vault, wallet, Safe or token lose money (dependency concentration); every contract and admin key that can cause user loss across a whole protocol, down to terminal keys (control closure); and who loses money if a given asset, oracle, admin key, custodian or bridge is compromised (blast radius). Use this whenever the user asks about risk, exposure, dependencies, single points of failure, concentration, contagion, admin keys, upgradeability, governance, choke points, who can take user funds, or what breaks if something fails, and whenever they paste a contract address in any risk context. Also use it to compare risk across several vaults or protocols, to refresh a previous analysis, and for follow-up questions about one dependency or one impacted party."
---

# Forta Risk Graph: dependency and control risk analysis

Every analysis answers one of three questions. Pick the mode first: the same graph read in the wrong direction gives a confident wrong answer.

| The question | Mode | Subject | Read before the first query |
|---|---|---|---|
| What could make this lose money? Where is the concentration? What does it depend on? | A. Dependency concentration | Something that holds value: a vault, wallet, Safe, token or market | `references/mode-a-dependency.md` |
| Who can take user funds? How deep do control, upgradeability and governance go? | B. Control closure | A whole protocol (Aave, Morpho, Euler, Silo, Compound, Spark, Maker) | `references/mode-b-closure.md` |
| What breaks if this fails? Who is exposed, and how much? | C. Blast radius | Something others depend on: an asset, oracle, admin key, custodian, bridge or market | `references/mode-c-blast-radius.md` |

A and C are inverses. A starts from what holds value and finds what can hurt it; C starts from what can fail and finds whom it hurts. If the subject holds value it is A; if other people depend on it, it is C. Two common questions are narrower cases: who controls one token or contract is Mode B's walk (B3 to B7) run on that one asset and sized by its supply, and how big a protocol is is Mode B's B1 on its own.

Also read, when it applies:

- `references/defensive-borrowing.md`: whenever a lending market sits on the loss path, which is most A and C runs.
- `references/protocol-crosschecks.md`: every vault, market or protocol subject (step A8), any levered account, and any control set the graph leaves unconfirmed.
- `references/mode-b-closure.md`, steps B3 and B6: in any mode, whenever you walk a control chain or meet an empty admin set.
- `references/mode-a-dependency.md`, steps A1 and A6: in any mode, whenever you need a vault's denominator or an encumbrance adjustment.
- `references/query-patterns.md`: before writing any `read_cypher`.
- `references/report-style.md`: before writing a report (not needed for a chat answer).

## 1. Before the first query

1. **Connector.** This skill drives the Forta Risk Graph MCP and does nothing without it. If its tools are unavailable, say so and stop. Do not substitute a block explorer or a web search: the analysis depends on the graph's own edge model, and an answer assembled elsewhere is not the same answer. The report scripts need Python 3 and nothing else.
2. **Server rules.** The server's `instructions` arrive before your first call and own the semantics: the partition, id casing, what zero rows, a USD 0 and a null each mean, the `meta` block, and query limits. Follow them; this skill does not restate them. Cite them by topic, never by number, because they get renumbered.
3. **Schema.** Call `get_schema({includeCensus: true})` once. It owns edge vocabulary and canonical direction, property units and caveats, chain scope, the list of structural gaps (`depth`) and the exact population of every edge type. A type the census marks sparse or unpopulated cannot support an absence claim. `read_cypher` rejects a relationship type the vocabulary does not carry (`UPGRADE_CTRL`, or `DEPENDS_ON`, which is a category), but a tool's relationship-type filter does not validate, so there a wrong name returns `[]` silently.
4. **Scope.** The partition indexes Ethereum mainnet only, and every read is scoped to the caller's controller. Before sizing anything, establish whether the subject is deployed on other chains. If it is, say which chain the figures cover and name the excluded deployments in the report header, not a footnote: a levered counterparty whose collateral sits elsewhere reads as thinly collateralised, a protocol's largest market may simply be absent, and a bridged asset's far side is invisible.
5. **Snapshot.** Record `meta.asOf.block` and the partition id the server instructions name; both go in the footer, because a figure that cannot be traced to a graph version is not reproducible. The graph refreshes continuously, so two reads minutes apart can differ slightly: do not reconcile separate reads to the dollar, and give one snapshot date for the whole analysis.

## 2. Typed tools first

Typed tools carry server-resolved semantics that a hand-written query silently loses: owner grouping, aggregate-node flags, admin-edge direction, and refusal when a population could not be read in full. Use `read_cypher` for what no tool answers, with `references/query-patterns.md`.

**Sizing and balance sheet**

| Need | Call | Read | Trap |
|---|---|---|---|
| Vault NAV | `get_denominator({nodeId})` | `nav.measures[]`, `idle.usd`, `denominator_verified`, `spreadPct`, `basisDetail[].pricedPct` | The allocation basis excludes idle: add `idle.usd` before comparing it with shares x price. `false` means the bases disagree; `null` means fewer than two resolved. Neither is a verdict on the vault. |
| What depositors can redeem from a Morpho Vault V2 | the vault node's `instant_liquidity_usd` and `instant_liquidity_basis` (query-patterns), then the protocol's `forceDeallocatableLiquidity` (A8) | instant and penalty-free: idle plus the one designated market. Any holder can also force-deallocate the other markets' undrawn liquidity, at a penalty | Report the two separately (A2). Absent is not zero. Not additive across vaults that route through the same market. Other vault kinds: take the protocol's figure (A8). |
| What a holder can withdraw from its own positions | `get_withdrawable_capacity({nodeId})` with the holder's id | `total.capacityUsd`, `total.coveragePct`, `total.status`, `complete`, `reason` | On a vault's own id it measures the vault's receipt and share holdings, usually an empty set, and returns a $0 that says nothing about redemptions. |
| A levered entity's debt, collateral basis and equity | `get_levered_position({nodeId})` | `debt[]` (morpho_blue legs carry `healthFactor`, `positionLtv`), `collateral.status`, `collateral.netEquityUsd`, `aggregateOnlyDebtProtocols` | `not_levered` means no per-borrower debt edge was found, not that there is no debt. Never quote equity without `collateral.status`. |
| Holder or borrower concentration | `get_concentration({nodeId, groupBy: "owner"})` | per dimension: `concentrationComputable`, `top1SharePctOfUsd`, `hhi`, `legsNotExamined`, `usdNotExamined`; `groupingFormed` | On a vault the holder dimension is depositor concentration; borrower concentration is read on each market node (on a Morpho market the tool reads the borrowers through the loan token for you). Owner grouping merges EVC sub-accounts only (A4). Never publish a share it refused. |
| Competing figures for one quantity | `get_measures({nodeId})` | the declared winner, or UNRANKED | Never pick one of several served figures yourself. |
| Holder-side blast radius of one subject | `blast_radius({subjectId, floorUsd})` | `additive`, `reattributed`, `unattributed`, `mechanisms[].sized`, `totals.attenuatedUsd`, `valuation` | Never add `reattributed` to `additive`. It does not size lending bad debt or AMM counter-legs (Mode C, C3). |

**Structure and control**

| Need | Call | Read | Trap |
|---|---|---|---|
| Which edge types a node has | `available_traversals({nodeId})` | exact per-type counts, `absentTypes` | The only way an empty result becomes a measured absence. |
| Dependency candidates | `dependency_closure({nodeId, maxHops})` | `nodes[]` (`hop`, `viaType`, `viaDirection`), `truncated`, `truncationReason`, `baseAssetReached` | Reaches market collateral and the base asset, but a reached base asset can arrive through an unrelated holding (a treasury or a dust balance): read the path before quoting it, and `baseAssetReached: false` is not absence. Carries no admin edges. Never sizes, and never proves absence, truncated or not. |
| Backing | `get_backing({nodeId})` | `backing_modelled`, `layers[]`, `absentLayers` | `false` means no backing model exists, never that the token is unbacked. No cross-layer total: the layers overlap. |
| Who controls one node | `get_governance({nodeId})` | `governance_described`, `facets[]` (with `absenceProven`), `signers`, `threshold.status`, `timelock.status`, `safeProbe.status` | `false` or `never_probed` is never a finding of no governance risk. A Morpho Vault V2 timelock is `shape_mismatch`: read it from the protocol. |
| Admin keys over a token | `get_admin_risk({tokenId})`, then `get_admin_risk({tokenId, keys: true})` | `maxAdminKeyExposureUsd`, `maxExtractableUsd`, `adminKeyCount`, `totalSupplyUsd` | Maximum per key, never a sum over keys. A key's `max_key_value_usd` is weighted by its strongest role's `role_severity_weight` (PROXY_ADMIN is 0.7 although it can replace the implementation), so it is not what the key reaches: size reach from the value beneath it. What a key controls is not what it can extract: the extractable figure is the token's exit ceiling, the same for every key, and its lending leg is not the defensive-borrowing draw. Attribute it to venues from the token's outgoing `EXIT_VIA` edges (`exit_usd`), not from `exitLiquidity.breakdown`, which can split the same total differently; where current venue depth differs, report it beside the ceiling. |
| How two nodes are related | `find_connections({fromNodeId, toNodeId})` | intermediaries per relationship class | Candidates only, like `find_path`. Neither sizes, and neither proves absence. |
| A ticker to a node | `resolve_entities({query, subcategories: ["token"]})` | candidates | Impersonators come back beside the real asset: disambiguate on label, a non-null `usd_price` and category, and say which you analysed. |
| Signers shared across Safes | `read_cypher({template: "signer_overlap_for_safes", params: {safeIds: [...]}})` | one row per shared signer | A Safe in no row either shares no signer or has no `OWNS` edges; tell them apart before calling it independent. |

## 3. Rules for every mode

1. **Unknown is not zero.** A filtered query returning nothing means UNKNOWN. Name what the filter dropped, in dollars. A number without its denominator, its coverage and its as-of block is not an answer.
2. **Read `meta` on every response a figure comes from.** A truncated page makes the figure a lower bound. A `declared` guarantee that is false is a gap to state. An absent section is UNKNOWN, never zero.
3. **One snapshot per derived figure.** Before combining two reads into a share, ratio or difference, compare their `meta.asOf.block` and the rows' freshness markers (which property is the marker depends on the edge type; `get_schema` names it). If they differ, re-query both. Disclosing the gap is not a substitute for a consistent snapshot. When a re-query cannot converge, because the stamps come from writers on different cadences, build the ratio from figures that share one stamp (one edge, one market) or from the protocol's own single-snapshot read, and state the block spread. A tool that returns no `asOf.block` is dated by the reads around it; say so.
4. **An old stamp is not automatically stale, except on anything that accrues.** Balance stamps are event-driven, so a quiescent position keeps an old block and is still right. That fails on an aToken, a rebasing share or accruing debt: no event fires, so the recorded quantity drifts below the real one for as long as the stamp is old. Before differencing two such legs, check each one's age and say which is older: the staler leg carries the larger unrecorded accrual and decides the direction of the bias. A `reconciled_at` missing from the edge's `keys()` means the token is outside reconciler coverage, which is not the same as a null on a covered token.
5. **Union, never sum.** A token, its admin key, its custodian and its oracle all reach the same positions. Track each dependency as a set of positions, compute dollars over the union, and label subtotals as unions. A parent is never below its largest child.
6. **Exposure sizing, not expected loss.** Full compromise, no probability weighting, no recovery except the encumbrance adjustment in A6. Say it once, in the footer.
7. **Debt comes from debt positions.** Take debt from the per-account `LENDING_BORROW` legs (`get_levered_position`), never from debt-token balances. Balances miss every protocol that mints no debt token (Morpho Blue and Euler among them), and where both exist they can disagree; then report both and say which the figures use. Which node a borrow edge points at depends on the protocol, and several protocols have no per-borrower writer (`get_schema`, `LENDING_BORROW`), so an empty borrower read is never "no debt".
8. **Levered entities are understated.** Escrowed collateral is not in the graph. Say so in every report on a levered entity, not only where a tool returns `unresolved`.
9. **The base asset arrives through `HOLDS` only**, never a dependency-typed edge. A manual walk restricted to dependency types misses it: include incoming `HOLDS`, or use `dependency_closure`, which does.
10. **Rank roles by what they can do to a depositor**, not by name. Blacklist, freeze, clawback and asset protection strand a balance with no exploit and no protocol involvement. Pause halts withdrawals and liquidations. Proxy admin and upgrader replace the implementation. Minter and master minter dilute, or mint collateral to borrow against. These are rarely called "owner", and they are the rows a reader acts on.
11. **The absence gate.** No sentence asserting a gap ("not modelled", "no admin", "not computable", "no per-X edge", "the graph cannot answer this") ships until it is verified in this run: `keys()` on the node or edge for a stored property (edges gain properties without changing any count, so a census cannot show them), the actual tool response for a field the server computes (`keys()` cannot see those), `available_traversals` for an edge type on a node, and the census for a type. Look on the right element: a property can live on the node, the edge, or the underlying asset's node rather than the market's. Run `available_traversals` on every node you describe as a leaf and on every node whose position set you report as complete: a debt position is an edge type, not a balance. Never upgrade "not found" into "does not exist". `get_schema`'s text and the typed tools can lag a property the writers now stamp; where they disagree with `keys()` on the live element, the live element wins.
12. **Structural gaps are stated, never filled.** `get_schema`'s `depth` list names them: escrowed collateral, backing and custody one hop past the last on-chain token, the base asset via `HOLDS`, withdrawable capacity versus NAV, depositor concentration. If an answer depends on one, say so; never complete it from world knowledge about an issuer.
13. **Figures in this skill are illustrative.** Populations move as coverage grows and retired edges are pruned. Re-measure anything you report; a stale claim that something is unavailable is worse than a stale count.

## 4. Figures that need a second look

Each of these reads as settled and is not. One query each.

| What you are looking at | The tell | What to do |
|---|---|---|
| A market's supply figure | the phantom signature in `get_schema`'s `total_supplied_usd` caveat | Size it from what actually holds the loan asset: the protocol singleton's `HOLDS` (Morpho), or the underlying each aToken holds (Aave) |
| A singleton beside its own markets | `is_aggregate_node` | Keep one level, drop the other, and say which |
| Oracle at-stake figures | many oracles on one market with an identical value equal to its whole liquidity | Never rank or aggregate on oracle at-stake; rank oracle admins over the feed set |
| A CDP token's supply | holders dominated by controllers, peg keepers, flash lenders or factories | Supply includes tokens minted to controllers and never borrowed: use collateral-backed debt as the denominator |
| A debt total implausible for the protocol's size | a large edge count against a trivial dollar total, or the reverse | Confirm against the protocol's own interface before reporting it |
| `max_borrow_capacity_usd` | any use as a loss, borrowed or bad-debt figure | It is a parameter times a balance (`get_schema`): never a loss figure |
| A concentration share, ranking or HHI | whether `get_concentration` would have refused it | Never publish a statistic the tool declines to compute |

## 5. Deliver

**A quick question gets a chat answer.** Lead with the number, name the dependency or the victim, give the evidence. No file. Shape:

```
<Finding, one sentence: the dollar figure and what it is a share of>
<Mechanism: the path, role or market that carries it>
<Basis: as of <date> (block N) · denominator · coverage, or "lower bound">
<Not covered: the gaps that apply, with their dollars>
```

That is the minimum. A question with several parts gets one finding line per part under a shared Basis and Not covered. Where the answer turns on several keys or constraints (a control chain, the defensive-borrowing minimum), add one compact table.

**A report request gets the HTML build.** Read `references/report-style.md` first. The scripts own the union arithmetic, ranking and banding, so extend them rather than hand-building HTML:

```bash
python3 scripts/build_report.py findings.json -o report.html          # Mode A
python3 scripts/build_report.py findings.json --check                 # arithmetic only
python3 scripts/build_closure_table.py tree.json -o closure.html      # Mode B
python3 scripts/build_blast_report.py blast.json -o blast.html        # Mode C, mode "wide" or "targeted"
```

Each script prints its input format with `--schema`; `assets/` holds a worked example of each. Save the report where the user can open it and hand it over with whatever the environment provides for files (a path in a terminal, the file or artifact tool in a chat app). Do not paste the HTML into the conversation.

If the user asks what could not be resolved, answer plainly: which positions, which dollars, what would settle each. That is a reply, not a report section.

## 6. Refuse rather than guess

- **No scores.** No grades, composite risk indices or raw node risk scores (`node_risk_score`, impact or cascade fields). Dollars and shares are checkable; indices launder uncertainty. Do not answer "is it safe" with yes or no: answer what can be taken, by whom, and what the analysis could not see.
- **No statistic a tool refused.** Report the refusal and what it did not examine instead.
- **No "no admin" from an empty role set** unless the discovery status shows the probe completed (Mode B, B6). **No "not upgradeable" from `is_proxy = false`**: the probe reads only the EIP-1967 slots, and a contract can read false while holding a `PROXY_ADMIN` role.
- **No health factor from a reserve-level liquidation threshold** (A6).
- **No oracle pair inferred from a feed's name.** Read `oracle_pair`; `oracle_pair_identified: false` means no declarable pair, and absent means the feed was never consulted.
- **No control from provenance.** `DEPLOYED_BY` names a deployer, not an admin.
- **A failure is not an absence.** A 403 is an authorization boundary on the caller's identity, a timeout is unknown coverage, and a 502 from `read_cypher` is usually a rejected query. Report the failure; never fold it into the answer as "no data".

## 7. Before delivering

The arithmetic gate passes on a report that is 40% complete, so run the coverage gate first. Answer every applicable line with a number, not a feeling. Where the answer is "not checked", check it or put an explicit unresolved row in the table. Never ship a blank.

**Coverage gate**

A report answers every line. A chat answer scoped to part of the subject applies the gate to what it sizes, and lists the rest under Not covered with its dollars.

- `meta` read on every response a figure came from; any figure from a truncated page is labelled a lower bound.
- Every position above the floor appears in at least one dependency row.
- Every branch you stopped walking records its termination condition (Mode B, B3).
- Every empty admin set is classified as a verified absence or unconfirmed (B6).
- Every lending market on the loss path (each funded market in Mode A, each market where the subject is collateral in Mode C or in Mode B run on one asset) carries a current and a maximum loss per `references/defensive-borrowing.md`, or a stated reason it does not, with its caps read rather than coalesced.
- Escrowed collateral stated on any levered subject.
- The report searched for schema identifiers and pipeline vocabulary (`references/report-style.md`).
- The mode's own checklist, at the end of its reference file, answered.
- Smell test: on a lending vault each funded market normally yields five to eight dependency rows once collateral, its roles, its backing and the oracle chain are in. A total row count close to the market count means layers were skipped.

**Arithmetic gate**

- Every derived figure comes from one snapshot (section 3, rule 3).
- Every debt figure traces to a debt-position edge, with its account's protocol named.
- Unions and shares recomputed in a script (`--check`): components sum to their totals, and every percentage reconstructs from its dollar figure and the stated denominator.
- No percentage above 100%. A group's impact is at most the sum of its rows and equal to the union of their positions.
- Every headline figure traces to specific named positions. A corrected figure is corrected everywhere it appears, including the headline cards.
- Rounding drift of a dollar or two across independently rounded subtotals is fine; a figure that cannot be reconstructed at all is not.
