# Mode B: protocol control closure

**The question:** every contract and every key that can, directly or transitively, cause a user of this protocol to lose money, each sized by the value beneath it, each traced until it terminates.

A protocol has no NAV to split, so Mode A's frame produces a report about the wrong thing. This output answers questions the vault frame cannot ask: how many distinct private keys sit above deposits and what the largest single one reaches; which contracts are the protocol's own and which are merely called; and where the closure collapses onto one node.

**Stopping at one or two hops is the characteristic failure.** USDC is not a leaf: beneath it are minters, a blacklister that can freeze a vault's balance, a pauser, a proxy admin that can replace the implementation, and a master minter whose own owner is a further hop down. A report listing "USDC" as one dependency row has not done the work.

Queries for each step are in `query-patterns.md`, Control closure.

## B1. Fix the denominator and the floor

1. **Denominator.** Sum supplied and borrowed over the protocol's stat-bearing nodes, scoped on `lending_protocol` and grouped by subcategory (query-patterns, Protocol-level supply). Do not filter on `subcategory = 'lending_market'`: on the Aave family the per-reserve stats sit on nodes typed `atoken`, so a market-only filter drops nearly the whole protocol and returns a total that is self-consistent and wrong. Say which population the sum covers. `get_schema`'s `total_supplied_usd` caveat explains why no such total is custody; the protocol singleton's own `HOLDS` of each loan asset is the economic cross-check.
   - One brand can span several slugs: Aave's deployments sit under `aave_v3`, `aave_v3_prime`, `horizon` and `etherfi_cash`, each its own pool. List the slugs before summing, and say which the total covers.
   - Within a slug, only the nodes carrying stats are reserves. The rest are mostly debt tokens, but they also include `atoken`-typed nodes with no stats and the pool's own contracts, so a count by subcategory is not a reserve count. Reconcile the stat-bearing count against the protocol's own reserve list (`getReservesList`, `protocol-crosschecks.md`) before publishing one.
2. **Floor.** Everything above it is recursed to terminal; everything below is carried as one named aggregate row with its dollar figure, never dropped silently. $1M is a good default on a $100M+ protocol.

## B2. Separate internal from external

Query the protocol's own contracts by `project`. Here the brand label is the right field, because the question is branding rather than value; everywhere else scope on `lending_protocol`. Classify each internal contract by the supply it reaches: entity-wide (a factory beacon, an EVC), partial (one governor's markets) or negligible. Then list the external contracts the protocol calls: reserve assets, oracles, bridges, wrappers. They have different failure modes and different owners: the protocol can fix the first list and can only monitor the second.

## B3. Walk the closure

1. **Walk up.** Breadth-first up `ADMIN_CTRL` from every market, asset, oracle and internal contract above the floor, using the bounded variable-length walk. Its arrow walks up the control chain. Two or three roots per call: one `LIMIT` is shared by every root, so a wide batch truncates silently. Depth 4 is safe; 5 starts timing out on dense roots. Parallel duplicate admin edges repeat paths, so the walk deduplicates them; never count raw edges. Filter a known hub out, and keep a visited set: the graph has cycles, since a token can be an admin of its own supply-control contract.
2. **Resolve each controller** with `get_governance({nodeId})`: Safe verdict, signers, threshold, timelock and enabled modules in one call. An enabled Safe module moves the Safe's assets with zero signatures, so the threshold is not the binding control when one is present.
3. **Terminate a branch on one of three conditions, and record which.**

| Condition | Test | Meaning |
|---|---|---|
| EOA | `is_contract = false` and `safe_probe_status = 'not_safe'` | A single private key. Terminal and reportable. Where the verdict decides a finding, confirm it with `eth_getCode` (`protocol-crosschecks.md`, Any contract). |
| Multisig | `safe_probe_status = 'ok'`, a threshold present, and no enabled module | Terminal at the Safe; its owners are the keys. With an enabled module, the module is a controller that needs no signatures: continue up it. |
| Cycle | already visited | Stop. Do not report it as new. |

A probe that never ran (`safeProbe.status: never_probed`) is neither verdict: nobody looked. A contract controller with no discovery status (a token pool, a ProxyAdmin) is not terminal either: read its `owner()` or role members on-chain before calling the branch unresolved, because one call often closes it. Depth 4 is normal and 5 not unusual: markets to a governor to an unlabelled `DEFAULT_ADMIN_ROLE` holder to a Safe to one EOA is a real shape, and it is invisible to anything that stops at hop 1.

## B4. The fan-out guard

Check inbound admin degree before expanding any node. Some contracts are role registries, and the graph attaches every role holder across a whole protocol family to them; expanding one injects dozens of unrelated parties into the closure as though they controlled the asset.

More than about 50 inbound admins spanning 4 or more subcategories is a hub. Do not expand it: emit one row recording the degree and mark the branch unresolved. Real control sets cluster at 15 to 20 inbound admins across 1 to 3 subcategories, so the threshold is well separated and not a judgement call.

## B5. Expand each external asset's control plane

For every reserve or collateral asset above the floor, the asset is a subgraph.

1. `get_admin_risk({tokenId, keys: true})` for the per-key rows, and `get_governance({nodeId})` for the role and signer structure.
2. The node's `admin_roles` (a JSON string mapping address to role names) and the admin edges differ in coverage, and the edge's `role` does not always match the property in case or naming. Pull both and reconcile.
3. Rank the roles by what they can do to a depositor (SKILL.md §3 rule 10). Blacklist, freeze, clawback and asset protection can strand a vault's entire balance with no exploit and no protocol involvement: the most under-reported loss path in DeFi risk analysis.
4. A bridged asset has mint paths outside its role set: its bridge pool and its verifier set. Read `dvn_min_to_attack` and the route it covers (`mode-a-dependency.md`, A3 item 5).
5. `is_proxy = false` does not mean non-upgradeable. The probe reads only the EIP-1967 slots, so a contract can read false while its role set holds a `PROXY_ADMIN`. Read `proxy_type` and the role set before calling anything immutable.

## B6. Classify every empty admin set

An empty set means two opposite things, and the node tells you which:

- `admin_roles = '{}'` with an `owner_discovery_status` or `role_member_discovery_status` of `ok` or `success` is a verified absence of admin control. Report it as a genuine strength: an immutable contract with no admin is a real finding and worth stating plainly.
- `admin_roles = '{}'` with a null discovery status is not confirmed either way. Say the control set is not confirmed on-chain; never say "no admin".
- `get_governance` returning `governance_described: false` means every signal the server measures came back zero: a statement about coverage, never on its own a finding of no governance risk.

Getting this backwards makes something look safer than it is, which is the direction that costs money. When it matters to the conclusion, resolve it on-chain: `owner()` for an Ownable contract, `hasRole` and `getRoleMember` for AccessControl (`protocol-crosschecks.md`, Any contract). A revert on `owner()` means the contract is not Ownable, not that it has no controller.

## B7. Choke points and cross-layer overlap

1. **Size by union, bottom-up.** A node's exposure is the union of its own positions and every descendant's, so a parent is never the sum of its children. Assert `parent.usd >= max(child.usd)` before shipping. A key on two Safes covering the same market is one exposure.
2. **Rank keys, not just contracts.** The output a reader acts on is a table of EOAs ordered by supply reachable.
3. **Look across layers for shared identities.** The highest-value findings live here, and no single query produces them.
   - Compare the signer sets of curator or governance Safes against the admin sets of the assets their vaults are denominated in (`signer_overlap_for_safes` does the Safe side in one call). Curator independence from an issuer is the premise of an isolated-vault model; overlapping signers mean it does not hold.
   - One key controlling both an asset and its price feed: intersect the admin sets of every asset with those of every oracle. The same party setting the asset and what it is worth collapses two supposedly independent risks into one.
   - One Safe holding several roles over the same asset: count distinct roles per admin address, not only admins per asset. A count of admins can look diversified when one Safe is default admin, upgrade admin, keeper admin and curator.
   - Group borrower rows by canonical owner before ranking concentration on EVC protocols, and check shared control among the top borrowers (`mode-a-dependency.md`, A4).
4. **Beware the floor artifact.** If you exclude positions below the floor, entity-wide contracts read as 97% rather than 100% and look like they miss something. Carry the sub-floor remainder as one named aggregate and include it in entity-wide rows. Where a figure still differs from a direct query because of truncation, state both and explain the gap.

## Mode B checklist

- Every external reserve and collateral asset above the floor expanded per B5, or listed with its dollars and marked unexpanded?
- Fan-out guard run before expanding each node?
- Every branch's termination condition recorded, every contract controller with no discovery status read on-chain or marked unresolved, and every empty admin set classified per B6?
- Every internal contract classified by the supply it reaches?
