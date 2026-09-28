# Mode A: dependency concentration

**The question:** if any single thing this entity depends on were compromised, how much of its value is exposed?

Run A1 to A8 in order. A8 is required on any vault, market or protocol subject. Queries for each step are in `query-patterns.md`.

## A1. Resolve the subject and fix the denominator

1. **Resolve.** For an address, project the node (query-patterns, Orientation). `resolve_address` pages both nodes and edges and returns a coverage block, so its first page is not the node's whole neighbourhood. For a ticker, `resolve_entities({query: "<symbol>", subcategories: ["token"]})`; impersonators come back beside the real asset, so disambiguate on label, a non-null `usd_price` and category, and state which node you analysed.
2. **Vault NAV.** Call `get_denominator({nodeId})`. It reports every basis the graph carries and crowns none.
   - Working denominator: the allocations basis plus `idle.usd`, when both are fully priced (`basisDetail[].pricedPct`). Judge pricing by value, not by leg count: an unpriced dust leg does not disqualify a basis, but name it. The allocations basis excludes idle by design, so an allocations total below the share side is not a gap until idle is added.
   - `denominator_verified: false` means two independent bases disagree beyond tolerance. Report both and settle it in A8 against the protocol's `totalAssets`. `null` means fewer than two independent bases resolved, which is not a finding that the NAV is fine. A basis carrying `unresolved` did not complete: UNKNOWN, not zero.
   - Shares x price is a cross-check only: `usd_price` on a vault share tends to track par rather than the actual share price.
   - The holder sum is a lower bound, because holder coverage lags, and it doubles as a staleness detector: if holders sum to more shares than exist, the supply snapshot is skewed and any share price derived from it is inflated. Report the position at `HOLDS.usd_value` instead.
3. **Agreement proves less than it seems.**
   - `share_pct` is normalised over whatever the writer enumerated, so a truncated allocation set still sums to 1.0000 and passes the ratio test (`allocated_usd / share_pct` identical on every row) while missing most of the book. Before trusting an allocation set, read each sub-account's balances and its own debt edges, and treat a $0 allocation as unmeasured rather than empty: confirm emptiness on the account itself.
   - `allocated_usd` and `share_pct` are written together, so a stale row carries a matching stale share and the ratio still agrees, and `VAULT_ALLOCATION` carries no per-edge freshness marker. Take the total from the ratio, then reconcile each row against the protocol in A8 before sizing any single market.
4. **A `share_pct` shortfall has two readings:** idle or unitemised deployment, or stale rows on a fully allocated vault. The shortfall alone does not distinguish them. Report the residual with both readings named, size nothing on it, and settle it in A8. A sum of exactly 1.0000 means the graph models zero idle.
5. **Do not derive a share price** as NAV divided by `total_supply_raw` and call a mismatch with `usd_price` a pricing error: both fields sit in that ratio, so a gap says something is off and nothing about which side. Verify a material position against the protocol's own interface, an on-chain `convertToAssets` read or a portfolio aggregator, and treat that as ground truth over anything the graph implies.
6. **Two invariants, one query each:** holders cannot hold more shares than exist, and `total_supply_raw x usd_price` cannot be less than `sum(allocated_usd)`, because a vault cannot lend out more than it owns. Either failing tells you which field to distrust.
7. **A wallet or Safe has one measure only:** the sum of its inbound `HOLDS.usd_value`. The graph cannot self-check it, so cross-check material holdings externally.
8. **Idle capital** is still exposed to the entity's own contract and its denomination asset, but not to the protocol or the markets, which is why those two dependencies outrank the protocol and every market.

## A2. Map where the value goes

1. **Edge types.** `available_traversals({nodeId: <subject>})` returns every type present with an exact count, plus `absentTypes`. For vaults look for `VAULT_ALLOCATION` and `VAULT_ASSET`; for tokens `HOLDS`, `AT_RISK` and `RESERVE_BACKING`; for markets `LENDING_COLLATERAL` and `ORACLE_DEP` (borrowers: A4); for wallets and Safes inbound `HOLDS` plus `APPROVES`, `OWNS`, `OWNS_ADMIN` and `DEPLOYED_BY`.
2. **Is the subject itself a borrower?** Call `get_levered_position({nodeId})` on every layer that holds value, sub-vaults included. A vault can be a supplier, a borrower or both, and the supplier frame will not tell you which. Its debt legs (per account, per reserve, from `LENDING_BORROW`) are the debt figure A6 needs.
3. **Allocations.** Pull them with `adapter_address` (query-patterns, Value deployment). On a Morpho Vault V2 one adapter can route 100% of assets into every market: a hop-1, entity-wide contract dependency that no other query surfaces. The adapter is metadata on the allocation edge; where it exists as a node it carries no control edges, so resolve its admins and upgradeability separately.
4. **A $0 allocation is not "enabled but unfunded".** It may be a dead edge for a market the vault can no longer allocate to: caps have three states (unlimited, deliberately zeroed, never enabled), and a zero-allocation edge looks identical in all three. Calling any of them latent capacity invents exposure that cannot legally be taken. Resolve it in A8.
5. **Redemption capacity is not NAV, and on a Morpho Vault V2 it has two tiers.** In each market either side can bind: the vault's position there, or the market's undrawn liquidity.
   - **Instant, penalty-free:** idle plus the one market bound to the designated liquidity adapter; this is what `withdraw` and `redeem` draw. Read the node's `instant_liquidity_usd` with `instant_liquidity_basis` (query-patterns); absent is not zero. `idle_only` means no adapter is designated.
   - **Force-deallocatable, at a penalty:** any holder can call `forceDeallocate` for its own shares (no role check; VaultV2.sol `forceDeallocate`) to pull a non-designated market's undrawn liquidity back to idle, paying the adapter's `forceDeallocatePenalty` (at most 2%) out of its shares, then withdraw. The allocator-only `deallocate` is a different function. The protocol serves this tier as `forceDeallocatableLiquidity` (A8); it matches Σ min(vault position, market undrawn liquidity) over the non-designated markets. The vault node carries no property for it, and `get_schema` and `get_withdrawable_capacity` describe those markets as reachable only by a curator, which the contract contradicts.
   - Report the two tiers separately, with the penalty. Never merge them into one "redeemable" figure, and never compute either from `allocated_usd` alone.
   - Neither tier is additive across vaults: vaults that draw on the same market each report that market's depth, and the first to act takes it. Group by market and take its depth once.
   - Confirm both in A8 (`liquidity`, `forceDeallocatableLiquidity`, `adapters[].forceDeallocatePenalty`). For other vault kinds take the protocol's own figure; if none exists, say withdrawal capacity is not computable rather than publishing a derived figure.
   - `get_withdrawable_capacity` answers a different question: what a holder can withdraw from its own positions, bounded by the instant tier only. Call it with a holder's id (a wallet holding the vault share), never the vault's own id, where it measures an empty position set and returns a $0 that says nothing about redemptions.
6. **Look through any holding that is itself a vault.** `HOLDS` shows where value sits, not where it is deployed. Size the wallet's slice of each downstream position as `holding_usd x share_pct`; this never needs a share price, which is what makes it safe.
   - A `share_pct` shortfall inside the held vault is its idle: carry the wallet's share of it as its own position.
   - Reconcile: look-through plus the share of idle must equal the holding's USD value. If not, there is a stale edge or the wrong `share_pct` set.
   - Label the access route in the row name. A look-through row is lending exposure to a market collateralised by an asset, not ownership of that asset. A holder of collateral loses when it falls; a lender against it is unharmed by an orderly decline and loses only when a fast drawdown outruns liquidation at the market LLTV, or when the asset depegs from what the oracle prices it against.

## A3. Trace dependencies outward

1. **Candidates.** `dependency_closure({nodeId: <subject>, maxHops: 4})`. It follows the server's edge set, including incoming `LENDING_COLLATERAL` into market nodes (a market's collateral) and incoming `HOLDS` (what a node custodies, the only arm that reaches the base asset). Much of its breadth on a lending subject is other markets accepting the same collateral: peers, not dependencies. It carries no admin edges, it never sizes, and truncated or not it never proves absence.
2. **Walk the layers yourself** for every position above the floor. "Keep going until nothing new appears" is not a method; this table is. Without it the walk stops at hop 2 and the report still reads as complete.

| Hop | From | Pull |
|---|---|---|
| 1 | the subject and its denomination asset (`VAULT_ASSET`) | `get_governance` (on a Safe subject too), `get_admin_risk`, `get_backing`: these two cover idle as well as deployed value (A1.8) |
| 2 | each funded market | collateral (incoming `LENDING_COLLATERAL`), oracles (outgoing `ORACLE_DEP`; the price authority is the edge marked `pricing_authority` on a market, `value_defining` on a token), borrowers (A4), custody (`CUSTODY_VIA`) |
| 3 | each collateral asset | `get_governance`, `get_admin_risk`, and its `admin_roles` reconciled against its admin edges (Mode B, B5); `get_backing`, then `WRAP_UNWRAP`, `RECEIPT_FOR` and `BRIDGE_BACKED_BY` where the backing result points; `EXIT_VIA` and incoming `HOLDS` always |
| 3 | each price-authority oracle | its own outgoing `ORACLE_DEP` (the feed behind the feed, with `oracle_pair`) and its admin roles. `DEPLOYED_BY` is provenance, not control |
| 4 | each asset role holder | contract or key? `get_governance` on every multisig reached (`safeProbe.status`, threshold, signers, modules) and its own admin roles |
| 4 | each underlying reached at hop 3 | repeat the hop-3 row on it |
| 5+ | each new node | repeat until a branch yields nothing new, then record which termination condition ended it (Mode B, B3) |

3. **Edge types.** Take the set from the census, not from a list, then add `HOLDS` by hand (SKILL.md §3 rule 9). Run each type as its own directed query.
4. **`OWNS_ADMIN` is not a role.** It is derived transitive reach: a multisig linked to what its own signers control elsewhere. Only `ADMIN_CTRL`, `ADMIN_OF` and `CURATES` carry an assigned role. A governance row with no `role` string is labelled derived reach or left out.
5. **Bridges.** `BRIDGE_BACKED_BY` reaches the lockbox or OFT adapter and stops there. The verifier set sits on incoming `DVN_VERIFIES` to the OFT, and its single-point-of-failure test is in that type's `get_schema` meaning. The node's `dvn_min_to_attack` is the number of verifiers an attacker must compromise; where it is absent, compute it as `dvn_required_count + dvn_optional_threshold` (every required DVN plus the optional quorum). The set is per inbound route and the node carries one (`dvn_src_eid`), so name that route and call the others unmeasured. Where an OFT carries no `DVN_VERIFIES` edge, report the adapter as present with severity unknown, with its count and dollars, and say that the required-DVN set is a `getConfig` read on the LayerZero endpoint; imply neither safety nor a single point of failure. Reject a bridge edge that is a self-loop (`startNode(r).id = endNode(r).id`): it counts as coverage and reaches nothing.
6. **Two things no risk field carries.**
   - Maturity: fixed-income collateral carries it in the token name (`PT-sUSDD-27AUG2026`). A matured PT still holding a balance, or one maturing within the month, is a finding.
   - What the collateral actually is: "AA tranche" is senior structured credit on one borrower's performance, and a PT is a claim redeemable at maturity. Either reframes lending risk as counterparty risk, and the token name is the only place it appears.
7. **Record** for each dependency: what it is, its layer, hop distance, which positions it touches, and its address.

## A4. Borrower and depositor concentration

For a lending vault this is often the primary loss path. Do not skip it.

1. **Borrowers, per funded market:** `get_concentration({nodeId: <market id>, groupBy: "owner"})`, borrower dimension. On the vault's own id the borrower dimension is the vault's own borrowers (usually none), not its markets'. Then aggregate across the vault's markets by owner: one owner borrowing in several markets the vault supplies is one exposure, and per-market tables hide it.
2. **Shared control among the top borrowers.** Owner grouping merges sub-accounts only, so two borrower contracts with one controller rank as two owners. For each top-ranked borrower that is a contract, read its admins (outgoing `ADMIN_OF`, reconciled with `admin_roles`); where two share a controller, report their combined share as a separate row, labelled inferred from shared control.
3. **Depositors:** `get_concentration({nodeId: <vault id>, groupBy: "owner"})`, holder dimension. A single dominant depositor is a solvency-of-exit event and appears in no position table. The graph's holder set can lag the protocol's, and a partial set understates concentration at every rank below the first: recompute from the protocol's position list in A8, label which list each share came from, and report both where they differ.
4. **Loops.** Compare the top depositors with the top borrowers of the funded markets: the same address, a shared controller, or a deposit that matches a debt to within rounding. A depositor funding its own borrowing makes its exit and its default one event. Unless an edge proves the link, label it inferred and say what matched.
5. **Reading the result.**
   - Never publish a share, ranking or HHI the tool declined (`concentrationComputable: false`). Report the refusal with its `legsNotExamined` and `usdNotExamined`, then narrow the anchor and retry. "Concentration not computable over N borrower legs" is a finding; a share derived from a truncated page is not.
   - `groupingFormed` is three-state: `false` is never a finding that exposure is diversified, and `null` means unknown.
   - Never sum the holder and borrower dimensions: different units over different edges.
   - A group flagged `is_aggregate_node` is a custody hub already containing its spokes, not one concentrated owner.
6. **Fallback.** Use a direct `LENDING_BORROW` aggregate only where the tool refuses and you have narrowed the population until it pages completely, and say which route produced the figure. Owner grouping has two correct forms: rows a query returns are stamped by the server with `canonical_owner_id` and its basis, so read the stamp; a query that groups inside Cypher cannot see that stamp, so group on the equivalent expression the server names in the response's `canonicalBorrowerOwners` block. Ranking on raw borrower ids splits one owner across its sub-accounts and understates its share. `reserve_id` is a token address, not a market id.
7. A single borrower large enough to matter, liquidated in a fast move, is the loss path for every vault supplying that market. Say it in those terms.

## A5. Size it

1. Impact of a dependency is the entity's value exposed if that dependency fails, aggregated by union over positions (SKILL.md §3 rule 5): adding a token's, its admin key's, its custodian's and its oracle's impacts multi-counts the same dollars and can exceed total assets several times over.
2. This is exposure sizing, not expected loss (SKILL.md §3 rule 6). Do not assume zero recovery on a levered entity: see A6.
3. Divide by total assets, not deployed capital, or entity-wide dependencies read above 100% and look like errors. Put both totals in the header so a reader can rescale.
4. **Per funded market, a loss pair, not one number.** A failing collateral asset costs the market's lenders what can still be borrowed, not only what has been. Size each funded market's Current estimated loss and Maximum loss, and name the binding constraint, per `defensive-borrowing.md`.

## A6. Encumbrance, on any entity that has borrowed

Gross exposure is right for an unencumbered holding and wrong for pledged collateral. If the entity borrowed against the failing asset, the debt is non-recourse: the collateral is liquidated, the liability is extinguished, and the loss is capped at equity.

```
overstatement = max(0, debt - seizable collateral remaining after the failure)
              = the bad debt the lending protocol absorbs
```

What the entity escapes, the protocol eats. It is a transfer, never a disappearance, so name the counterparty who picks it up rather than netting it away.

1. **Debt.** The subject's own `LENDING_BORROW` legs, one per account per reserve, summed per account (`get_levered_position`); never a debt-token balance (SKILL.md §3 rule 7). Escrowed collateral behind those accounts is out of scope, so a loan-to-value built on visible collateral is an upper bound, not a measurement: say so in the row, and where the shortfall is large, state what collateral the invisible side would have to hold for the book to reconcile.
2. **Split** every position into encumbered and unencumbered. The same asset sitting loose takes a 100% loss with no offset, because there is no loan to walk away from. Never add the two legs and then adjust them as one.
3. **Liquidation threshold.** `liquidation_threshold` on the edge is the reserve-level parameter. The edge's `emode_*` properties give each e-mode category's ceiling for that reserve, but not which category an account uses, so a health factor built from edge parameters can read as liquidatable on an account that is comfortable. This trap errs in the alarming direction: it invents a crisis rather than hiding one, and a false insolvency claim about a named counterparty is more damaging than a conservative one. Take the account-effective threshold and health factor from the protocol (`getUserAccountData`, `protocol-crosschecks.md`) or, on morpho_blue, from the per-leg `healthFactor` in `get_levered_position`. Never publish a health factor built from the reserve threshold as a finding about an account.
   - `ltv: 0` beside a non-zero `liquidation_threshold` is an offboarding configuration, not distress: no new borrowing power, existing positions survive.
   - Check block skew before dividing: debt and collateral legs refresh independently, and a loan-to-value built from readings days apart is not a health factor.
4. **Seizable base.** Read `usage_as_collateral_enabled` per reserve on `LENDING_COLLATERAL`. An asset supplied with that flag false earns yield but was never pledged, so it belongs in neither the collateral base nor the loss.
5. **Recompute after the failure.** Zero the failing asset, sum the remaining collateral-enabled positions, compare against the debt. If the remainder still covers the debt, the account stays solvent and there is no adjustment: charge the full gross loss.
6. **Route the excess.** Where debt exceeds the remaining seizable collateral, the difference is the protocol's bad debt. Report both sides.
7. **Apply the liquidation bonus.** `liquidation_bonus` is 1e4-scaled: 10800 is a 1.08 multiplier, an 8% premium, so debt retired is `seized / 1.08`. The counterparty's shortfall is larger than the plain subtraction; the entity's relief is unchanged. Give both figures.

Say three things whenever you apply it:

- **The relief is tail-only.** In an orderly decline liquidators unwind at the threshold and the entity pays the bonus, doing worse than mark-to-market. The floor pays only in a gap-down fast enough to outrun liquidation. Without this, an adjusted number reads as "leverage makes this safer", which is false at every price move except the one where the floor binds.
- **Collateral flags are reserve-level, not per account.** Aave-style protocols let each user toggle collateral per asset, so the seizable base is an upper bound on what can be taken from that account. Label it as one.
- **A worse debt figure produces a flattering adjustment**, since the floor is worth `debt - seizable collateral`. If the debt is uncertain, say which way that pushes the adjusted number, and never headline the most-levered reading without noting it is also the most favourable.

**Cross-collateralisation cuts the other way.** On a pooled account (Aave V3 and its forks) the whole basket secures the debt, so a failure also consumes the other collateral. Give that its own top-level row spanning every pledged position, worded symmetrically: any pledged asset's loss, if large enough, forces liquidation of the others, and the pledged assets have their own independent dependencies. Do not name the row after one asset's failure. Every row in a dependency table names a thing that can fail, never a scenario in which something fails.

**Render the adjustment in the table, never only in prose.** A grouped table sizes each family by the union of positions it touches, which is a gross figure and reads as a loss. Put the adjustment in the `encumbrance` block of the findings JSON so it renders as a bridge table, and name any gross group "shown gross" in the group name itself. A number in an impact column with a note two lines below is a number that will be quoted without the note.

**Where the adjustment bites is itself a concentration measure.** Run step 5 for every collateral asset. Usually only the largest makes the account insolvent on its own; if exactly one asset triggers the floor, the book is a single bet.

## A7. Group

Group by dependency family, the asset or component (WBTC, USDC, wstETH, the protocol, the entity itself), not by abstract layer. Families match how people ask: "what is my cbBTC exposure" is a real question, "what is my collateral-admin-layer risk" is not.

A dependency shared across families (a feed serving two collateral types, a base asset backing two wrappers) goes at top level, not nested inside one family. Nesting hides that it is shared and understates it, sometimes by an order of magnitude.

## A8. Cross-check against the protocol

Required on any vault, market or protocol subject. A vault report rests on the graph's model of a protocol's mechanics, and where that model is incomplete the graph cannot say so: every finding stays internally consistent and some are wrong. Redemption routing, timelock schedules, caps, the full role set and queued governance are protocol configuration, not graph structure.

The graph is authoritative for dependency structure; the protocol is authoritative for its own state and mechanics. Where they differ on state, prefer the protocol and note the difference. Routes, queries, schema traps and the check table are in `protocol-crosschecks.md`. Do not skip this phase silently: where no route exists, say which figures are graph-only.

## Mode A checklist

Per funded market:

- Price-authority oracle identified (`pricing_authority` on the market, or `value_defining` for a token's own price), or the price path recorded as unresolved with its dollars?
- Collateral asset's role set pulled, and each privileged role named (blacklist, pause, upgrade, mint) rather than summarised as "admins"?
- Backing read with `get_backing`, with proof-of-reserve attestation legs and self-loops rejected rather than counted as coverage?
- Collateral name checked for a maturity date against the snapshot?
- Borrower concentration row present, with shared control among the top borrowers checked?
- Current estimated loss and Maximum loss sized, with the binding constraint named?

For the entity:

- Denomination asset's governance, admin risk and backing read (it covers idle too)?
- Depositor concentration row present, checked against the protocol's position list, and loops between top depositors and top borrowers checked?
- Redemption capacity reported as two tiers (instant, and force-deallocatable with its penalty), taken from `instant_liquidity_usd` and the protocol, never derived from allocations alone?
- Every sub-account's own debt edges read before its allocation row was trusted?
