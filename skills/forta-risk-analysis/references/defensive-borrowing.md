# Defensive borrowing: what a failure costs once borrowers see it coming

**A lending market's loss is not what has been borrowed. It is what can be borrowed.**

A failing asset keeps its old price for as long as the market's oracle takes to catch up, and that window is the whole game. Inside it, the rational move for anyone holding the asset is to post it as collateral, borrow whatever the market will lend, and walk away. The borrowed asset is real and it leaves; the collateral left behind is worthless. The lender is left holding both the original debt and everything drawn on the way out.

So a report that sizes the loss at current utilisation understates it, and the part it drops is the part a reader cares about: the liquidity that looked recoverable. **Never describe a market's unborrowed liquidity as recoverable under a value-to-zero vector.** It is the first thing to go.

## The constraints, and the smallest one binds

| Constraint | How to compute it | Where it comes from |
|---|---|---|
| Collateral already posted | `posted collateral x LLTV - debt already drawn` | `total_collateral_usd`, or `sum(collateral_usd)` over the market's borrow legs; `toFloat(lltv)/1e16` for the percentage |
| Collateral that could still be posted | `(posted + postable) x LLTV - debt`, where `postable` is bounded by any cap on total collateral and is otherwise unbounded | a collateral supply cap where the protocol has one, and the attacker's reachable supply of the asset |
| Available liquidity | `available_liquidity_usd`, or `total_supplied_usd - total_borrowed_usd` | the market node or edge |
| Borrow cap headroom | `borrow cap - debt already drawn` | per protocol, below |

```
extra borrowing   = min(collateral room, available liquidity, borrow cap headroom)
loss under a rush = debt already drawn + extra borrowing
```

Three terms compete for the minimum: collateral room, available liquidity and borrow-cap headroom.

**Collateral already posted is a floor, not a ceiling, and treating it as a ceiling is the easy mistake.** It asks what today's borrowers could draw against collateral they have already committed, which is the right question only if the collateral set is frozen, and a failing asset is exactly when it is not: anyone holding the asset can post more and borrow against that too, and the worse the news the stronger the incentive. So the collateral-room term is the postable figure, and it is frequently unbounded, most obviously where the asset's own minter is among the compromised roles, because then the attacker's supply of collateral is whatever they choose to mint (a mint role with `max_key_value_usd` approaching the asset's market cap; query-patterns, Defensive borrowing inputs). Report the posted figure as context and let the postable figure compete for the minimum.

**When the collateral term drops out, liquidity and caps decide it, and a cap is the only thing that can hold the loss below the whole market.** That is why the cap belongs in the model rather than a footnote: where a protocol caps how much of an asset may be posted, the cap converts an unbounded exposure into a computable one, and it is usually the number a risk committee actually controls. Where no cap exists, say so in the row: "no borrow cap, so nothing caps the draw here" is a finding, not a blank.

**Caps live in different places per protocol, and a null means something different on each.** Coalescing any of them to zero makes the extra borrowing zero and silently deletes the effect.

| Protocol | Borrow cap | Collateral supply cap | What a null in the graph means |
|---|---|---|---|
| Morpho Blue | none exists | none exists | Both terms drop out of the minimum. The caps Morpho does have are vault-level `absoluteCap` and `relativeCap`, which bound how much the vault supplies, not how much borrowers can draw. |
| Aave v3 | `borrow_cap` on the **underlying token node** (not the aToken, not the edge) | `supply_cap` on the same node | Whole tokens, with `borrow_cap_block` / `supply_cap_block` stamps. In Aave, 0 means unlimited and 1 means closed; borrowing disabled is a separate flag, never a 0 cap. These are Aave v3's own caps: never apply them to a fork that lists the same token. Where the node carries none, read `getReserveCaps(asset)` on-chain (`protocol-crosschecks.md`). |
| Spark and other Aave forks | exists on-chain, not in the graph | exists on-chain, not in the graph | A null is UNREAD, never "no cap", and the token node's caps belong to Aave v3, not to the fork. Read `getReserveCaps(asset)` on the fork's data provider. `(0, 0)` is ambiguous (unlimited, or not a reserve on that pool: check `getReservesList`), and `68,719,476,735` (2^36 - 1) is the field maximum, effectively unlimited. If you cannot read them, mark the constraint unknown and label the maximum loss an upper bound. |
| Compound v3 | not in the graph | per collateral asset per Comet: `supply_cap` on that asset's `LENDING_COLLATERAL` edge | Raw units in the collateral token's own decimals, not whole tokens; 0 is a stored value there, not a sentinel. This cap is precisely what stops an arbitrarily large collateral deposit. |

**Liquidity usually binds, and that has a consequence worth stating plainly.** Borrowers post collateral well above what they draw, so collateral headroom is typically an order of magnitude larger than what is left to lend. When liquidity binds, `drawn + liquidity` is the market's entire supplied amount, which means **a sole supplier in an isolated market loses its whole position, not its utilised fraction.**

**Report both numbers, never one.** They answer different questions and a reader needs the pair. Head the report with exactly these two, and no third: **Current estimated loss**, the loss at the amounts borrowed today, and **Maximum loss**, the loss once borrowers draw everything they can reach, marked as the hot one. Show the three competing constraints with the binding one marked, so the reader can see which lever would change the answer.

## Where the effect does not apply

Saying so is part of the analysis. **Read the market's price-authority oracle first, even under a value-to-zero vector** (query-patterns, Oracles, and the hop-4 query in Recursing outward from a vault): it decides whether the window exists and how long it stays open. A feed on the asset's own market price closes it at the feed's heartbeat or deviation threshold (`oracle_heartbeat_secs`, `oracle_deviation_bps` on the feed). A feed that prices a wrapper as its underlying (BTC/USD behind a wrapped BTC) does not move when the wrapper alone fails, so the window never closes and the maximum applies in full. Name the feed and its pair in the row.

| Situation | Why |
|---|---|
| The asset is not accepted as collateral anywhere | Nothing to borrow against. The loss is the holding, full stop |
| The oracle updates faster than borrowers can act | The mechanism is liquidation, not borrowing, and the market keeps its liquidity |
| The market can be paused, and the pause is credible | Name the role that can pause it and its timelock. Do not assume it fires |
| A multi-collateral pool sized by pro-rata share | Pro-rating already attributes the asset's whole share of pool debt, so adding a rush on top double counts. Say which basis was used |

**In Mode A** this changes the impact figure for any collateral asset the subject lends against. **In Mode C** it changes the new-dollars bad-debt figure for every market where the subject is collateral, which on an ecosystem-wide run is usually the largest line. Compute it per market and sum, rather than applying a factor to a protocol total.
