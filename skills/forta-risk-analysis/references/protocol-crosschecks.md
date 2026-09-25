# Cross-checking against the protocol (step A8)

Required on any vault, market or protocol subject, and on any levered account whose health you report. The graph is authoritative for dependency structure; the protocol is authoritative for its own state and mechanics. This step costs about five calls and settles what a graph-only report cannot: redemption routing, timelock schedules, caps, the full role set and queued governance.

**Do not silently skip it.** Where no route below applies, either find the protocol's own API or subgraph and record what you used, or state that no external cross-check was run and which figures are therefore graph-only. Add any route you establish here.

## Morpho (Blue, MetaMorpho, Vault V2)

Endpoint `https://blue-api.morpho.org/graphql`, no auth. Do not fetch `app.morpho.org`: it is a JavaScript shell with no data in the HTML, and this API is what renders it. Use `vaultV2ByAddress` for `vault_kind: morpho_v2` and `vaultByAddress` for MetaMorpho V1, both `(address, chainId)`. Write the query to a file rather than inlining it in `curl -d`; shell quoting mangles the nested quotes.

```bash
curl -s -X POST https://blue-api.morpho.org/graphql \
  -H 'Content-Type: application/json' -d @q.json | python3 -m json.tool
```

Introspect rather than guessing a field: `{ __type(name:"VaultV2"){ fields{ name } } }`.

```
vaultV2ByAddress(address:"<VAULT>", chainId:1){
  totalAssets totalAssetsUsd totalSupply sharePrice idleAssets
  liquidity liquidityUsd forceDeallocatableLiquidity
  owner{address} curator{address} allocators{allocator{address}} sentinels{sentinel{address}}
  timelocks{ functionName duration abdicatedAt }
  caps{ items{ id idData type absoluteCap relativeCap allocation } }
  pendingConfigs{ items{ validAt functionName } }
  adapters{ items{ address type assets forceDeallocatePenalty } }
  liquidityAdapter{ address }
  liquidityData{ __typename ... on MarketV1LiquidityData { market{ marketId collateralAsset{symbol} } } }
  positions(first:1000){ items{ user{address} assetsUsd } pageInfo{ countTotal count } }
  warnings{ type level } performanceFee managementFee }
```

Markets and one position, for validating collateral, LLTV, the oracle, bad debt and any LTV you publish:

```
markets(where:{uniqueKey_in:[...], chainId_in:[1]}){ items{ marketId lltv badDebt{usd}
  collateralAsset{symbol} state{ supplyAssetsUsd borrowAssetsUsd liquidityAssetsUsd utilization }
  oracle{ address type data{ __typename ... on MorphoChainlinkOracleV2Data {
    baseFeedOne{address} baseFeedTwo{address} quoteFeedOne{address} quoteFeedTwo{address} } } }
  supplyingVaultV2s{ address name } } }
marketPosition(userAddress:"<borrower>", marketUniqueKey:"<key>", chainId:1){
  state{ collateralUsd borrowAssetsUsd supplyAssets } healthFactor priceVariationToLiquidationPrice }
```

For escrowed Morpho Blue positions, which have no receipt token and are invisible to any balance read, `userByAddress(address, chainId){ marketPositions { ... } }` returns collateral, borrow and health factor. An empty `marketPositions` is a real absence and is worth having.

**Schema traps that cost calls.** The filter is `uniqueKey_in`, not `marketId_in`, and `Market` has no `uniqueKey` field to select: select `marketId`. `positions` takes `first` (up to 1000) and `skip` but no `orderBy`: fetch the whole list, confirm `count` equals `countTotal` (page with `skip` otherwise), and sort locally; a first page alone drops the tail that cumulative shares and HHI need. `absoluteCap` at `2^128-1` means unlimited. `relativeCap` and `forceDeallocatePenalty` are WAD, so divide by 1e16 for a percentage; the contract caps the penalty at 2%. A cap's `type` says what it bounds (`MarketV1`, `Collateral`, `Adapter`), and its `idData` encodes the market or asset it applies to: decode it to tie a cap to a market, and label a match made only by equal `allocation` as inferred. `liquidityData` is a union and needs an `... on` fragment, and it is null when no liquidity adapter is designated, in which case `liquidity` is the idle balance alone. A non-null `abdicatedAt` means that timelock was permanently renounced, a positive signal the graph cannot express. The adapter, not the vault, is the supplier of record in `marketPosition`.

| Check | Against | Fail action |
|---|---|---|
| NAV | `totalAssets`, `totalAssetsUsd` | About 0.1% is price and block skew. Beyond that, stop and reconcile |
| **Per-market allocation** | `caps.items[].allocation`, and `marketPosition` on the **adapter** | Reconcile row by row. The ratio test in A1 will not catch a wrong row |
| Market count | number of capped markets | An edge with no cap is dead, not idle capacity |
| Collateral asset and LLTV | `collateralAsset`, `lltv` | Any mismatch invalidates the family grouping |
| **Redemption capacity, instant** | `liquidity`, `liquidityAdapter`, `liquidityData.market` | Compare with the vault's `instant_liquidity_usd`; where they differ, prefer the protocol's figure and say so |
| **Redemption capacity, force-deallocatable** | `forceDeallocatableLiquidity`, `adapters[].forceDeallocatePenalty` | Any holder can reach it, at the penalty. The graph does not store it; its cross-check query is in query-patterns (Value deployment). Report it beside the instant figure, never merged into it |
| **Timelocks** | `timelocks[]` | Replace any scalar with the per-selector schedule; the graph serves a V2 timelock as `shape_mismatch` |
| **Caps** | `absoluteCap`, `relativeCap` | `uint128` max means reallocation is unconstrained. Say so |
| **Roles** | `owner`, `curator`, `allocators`, `sentinels` | The graph carries owner and curator. Add the rest; drop any row with no role |
| Pending governance | `pendingConfigs` | A queued change with a `validAt` is a live finding the graph cannot show |
| Depositor concentration | `positions`, `pageInfo.countTotal` | Recompute cumulative shares from the protocol's full list, and say which list each published share came from. Graph holder coverage lags, and a partial holder set understates concentration at every rank below the first |
| Oracle behind a market | `oracle.type`, `oracle.data` feeds | Names the feeds the graph's `ORACLE_DEP` walk should reach. A feed that prices a wrapper as its underlying (BTC/USD for a wrapped BTC) does not move when the wrapper fails |
| Any borrower LTV published | `healthFactor`, `priceVariationToLiquidationPrice` | Within about 1pt is fine |
| Bad debt | `badDebt.usd` | The graph serves no per-market bad-debt figure for Morpho; take it from here |
| Shared suppliers | `supplyingVaultV2s` | Names every other V2 vault in the same market. No graph equivalent, and a cleaner contagion input than `VAULT_ALLOCATION` fan-in |
| The protocol's own warnings | `warnings` | An empty array is not an all-clear; a non-empty one you omitted is a miss |

## Aave v3 and Spark

Read-only `eth_call` over any public RPC. Public nodes reject a request with no user-agent header. Arguments are 32-byte left-padded addresses.

| Contract | Aave v3 | Spark |
|---|---|---|
| Pool | `0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2` | `0xc13e21b648a5ee794902342038ff3adab66be987` |
| Protocol data provider | `0x0a16f2fcc0d44fae41cc54e079281d84a363becd` | `0xfc21d6d146e6086b8359705c8b28512a983db0cb` |

Sources: `bgd-labs/aave-address-book` (`src/AaveV3Ethereum.sol`, `POOL` and `AAVE_PROTOCOL_DATA_PROVIDER`) and `sparkdotfi/spark-address-registry` (`src/SparkLend.sol`, `POOL` and `PROTOCOL_DATA_PROVIDER`). If a call reverts or returns an implausible shape, re-check against those registries before trusting it.

| Call | Selector | On | Returns |
|---|---|---|---|
| `getUserAccountData(address)` | `0xbf92857c` | pool | six words: total collateral, total debt and available borrows in 8-decimal base currency (USD); the **account-effective** liquidation threshold and LTV in basis points; the health factor, 1e18-scaled |
| `getUserEMode(address)` | `0xeddf1b79` | pool | the account's e-mode category; 0 is none |
| `getReservesList()` | `0xd1946dbc` | pool | every reserve asset |
| `getReserveData(address)` | `0x35ea6a75` | pool | a 15-word struct: aToken at word 8, variable-debt token at word 10 |
| `getReserveCaps(address)` | `0x46fbe558` | data provider | `(borrowCap, supplyCap)` in whole tokens; 0 means no cap |
| `getConfiguration(address)` | `0xc44b11f7` | pool | the reserve bitmap: LTV bits 0-15, liquidation threshold bits 16-31, borrow cap bits 80-115, supply cap bits 116-151 (whole tokens) |

`getUserAccountData` is the authoritative answer to three traps at once: it prices collateral at the protocol's own oracle, it counts positions that emit no token, and its threshold is the account-effective one (e-mode included), so the health factor needs no reconstruction from reserve parameters. It is one call per account.

To learn which asset a levered account actually holds, sweep `getReservesList()`, take each reserve's aToken and variable-debt token from `getReserveData`, and read `balanceOf(account)` on each: an account the graph describes by one asset can hold a different, larger one.

For a reserve's totals, `totalSupply()` (`0x18160ddd`) on its aToken and on its variable-debt token cross-checks the graph's supplied and borrowed figures in token units. They are aggregates: never read them as any one account's position.

Aave v3's own caps are also on the underlying token node (`borrow_cap`, `supply_cap`, whole tokens), so for Aave v3 `getReserveCaps` is the cross-check. Spark's caps are not in the graph, and the token node's values are Aave v3's, not Spark's: for Spark the defensive-borrowing cap terms come from `getReserveCaps` on the Spark data provider. The bitmap decode is an independent second read of the same values.

## Any contract: code, owner and roles

The route for a control set the graph leaves unconfirmed (Mode B, B3 and B6): a controller with no discovery status, an EOA verdict that decides a finding, or a role holder whose own owner is unknown. Same RPC conventions as above.

| Read | Selector | Answers |
|---|---|---|
| `eth_getCode(address, "latest")` | (RPC method) | `0x` means no code at that address: an EOA, or nothing deployed there. Anything longer is a contract |
| `owner()` | `0x8da5cb5b` | the single owner of an Ownable contract (a ProxyAdmin, a token pool). A revert means the contract is not Ownable, not that it has no controller |
| `hasRole(bytes32,address)` | `0x91d14854` | whether an address holds an AccessControl role; the default admin role is `0x00…00` |
| `getRoleMemberCount(bytes32)` | `0xca15c873` | how many hold the role, on enumerable AccessControl only |
| `getRoleMember(bytes32,uint256)` | `0x9010d07c` | each holder by index, to enumerate the full set |

Take a role's hash from `role_hash` on the token's incoming `ADMIN_CTRL` edge rather than hashing the name yourself. Where `getRoleMemberCount` reverts, the contract is not enumerable: say that role completeness rests on the graph's `role_member_discovery_status`, and confirm each known holder with `hasRole`.

Public nodes serve the latest state only, so a historical read (`eth_call` at an old block) fails: state which block the read reflects. Record each on-chain read as its own source beside the graph figure it confirms or contradicts.

## No route established

Euler, Pendle and Maple have no route here yet. Find the protocol's own API or subgraph and record what you used, or state that no external cross-check was run and which figures are therefore graph-only.
