# Report style

Read before writing a report. A chat answer follows the shape in SKILL.md section 5 instead.

## Build it with the scripts

`scripts/build_report.py` renders Mode A from a findings JSON, `scripts/build_closure_table.py` renders Mode B as one interactive table, and `scripts/build_blast_report.py` renders Mode C with `mode: "wide"` or `mode: "targeted"`. Run any of them with `--schema` for the input format and see `assets/` for a worked example of each. They own the union arithmetic, the ranking and the banding, so the tables cannot drift from the figures: extend the scripts rather than hand-building HTML.

- **Mode A.** Put the A6 encumbrance adjustment in the `encumbrance` block so it renders as a bridge table.
- **Mode B.** An admin node carries the positions of whatever it controls, since exposure rolls up as the union of a node and its descendants; a key with an empty position set and no descendants reads as reaching nothing, which is wrong if it sits above a market. Deliver one table, not many sections: a protocol closure has 100+ nodes across 5 hops, and splitting it per layer forces the reader to hold the cross-references in their head. One expandable table, indented by hop depth, collapsible back to a one-screen overview. Required columns: dependency (with an inline note), address, type, category, control, failure mechanism, exposure USD, share, markets.
- **Mode C.** The script translates relationship type names to plain phrases at the render boundary and passes anything unrecognised through, so a plain phrase can always be authored directly.

**State the failure mechanism per row**, because "compromised" means different things: `bug in code`, `key compromise`, `implementation swap`, `freeze`, `clawback`, `mint`, `stale or manipulated price`, `liquidation shortfall`, `custodial failure`. A reader stress-testing a freeze row with a price shock gets the wrong answer.

**On a wallet, mark each bucket in its name** as held directly, reached by look-through, or mixed, and as encumbered or free. The rollup has no field for it, and without it a look-through bucket reads as a direct holding, which inverts the risk.

## What the output is

**The report is a data product: tables, and as little else as possible.** No prose sections at all: no bottom line, executive summary, recommendations, caveats, key findings, takeaways, method essay or closing commentary. If a sentence is not qualifying a specific number in a specific row, it does not go in. The renderers emit none of these, and a report that reintroduces them by hand is wrong.

| Element | Content |
|---|---|
| Header | Subject, denominator, chain and snapshot. One line each |
| Ranked table(s) | The data. Every row a thing that can fail, with its dollars and its share |
| Row-level qualifier | Where a number is unsettled, the qualification sits in the cell |
| Footer | Source, snapshot, floor and the sizing basis in one compact line |

**A lead-in is at most one line, and only where a table needs an anchor.** "Eight markets, effectively one bet" earns its place ahead of a table that shows exactly that. A paragraph restating the table does not.

**Uncertainty goes in the cell, never in a section.** With no caveats section, an unqualified row implies a settled figure, so anything material and unsettled carries its qualification in the row:

| Situation | How the row reads |
|---|---|
| Price authority not resolved to one feed | "Oracle candidates listed; not confirmed to a single feed" |
| A Safe's signer set not readable on-chain | "Signer set not confirmed on-chain" |
| A custodian, fiat reserve or governance process | Name it as an off-chain arrangement, outside on-chain scope: a property of the thing, not a limitation to apologise for |
| A balance older than the snapshot | Append the as-of date to the row name |
| A figure with a genuine range | Give the range in the cell |
| An upper bound rather than a measurement | Label it a bound, and keep it out of the total |

**State the chain and the snapshot in the header:** "Ethereum mainnet, `<date>`". Where the subject has deployments elsewhere, name them as out of scope on the same line: an analysis boundary, not a caveat.

**Keep out entirely:** discussion of how the underlying data is assembled; tool names, query text and identifiers that mean nothing to the reader; any recommendation about tooling rather than the subject.

**The checkable version of that rule.** Before shipping, search the report for schema identifiers and pipeline vocabulary: relationship type names, property names, block numbers outside the footer, "the graph", "the partition", "writer", "edge". Any hit outside the footer is a defect. The fix is the rewrite below, never deleting the caveat: the uncertainty always survives, and what changes is whose problem the sentence sounds like. A caveat phrased as an admission about the data reads as a warning about the product; the same fact phrased as a property of the position reads as analysis, and the second one is true.

| Do not write | Write instead |
|---|---|
| "the graph wrote these 60,779 blocks apart" | "the collateral is dated 11 Aug and the debt 3 Aug, an eight-day spread" |
| a raw block number in the body | the date it corresponds to; block numbers belong in the footer |
| "no `pricing_authority` or `value_defining` oracle on this market" | "no feed on this market is identifiable as the one that sets its price" |
| "roles read empty with no discovery status" | "the role set could not be confirmed on-chain, which is unknown rather than absent" |
| "the only `BRIDGE_BACKED_BY` edge is a self-loop" | "the only backing reference points back at the token itself, so it resolves nothing" |
| "`BACKED_BY` points at a PoR aggregator, so rejected" | "the only backing reference points at a proof-of-reserve feed rather than a reserve asset, so it establishes nothing" |
| "`oracle_pricing_type` is unset on the token" | "nothing resolves the asset's own backing" |
| "`total_collateral_usd` is unpopulated on this protocol" | "collateralisation could not be checked independently here" |
| "not modelled in the graph" | name the thing as an off-chain arrangement, outside on-chain scope |
| a relationship type name on a diagram arrow | what the link is: holds, is a claim on, accepted as collateral by |

**A gap is a property of the subject, not a confession.** A custodian, a fiat reserve, an off-chain credit book and a governance process are real parts of how the thing works, and none of them is on-chain. Say what stands behind the asset and that it sits outside on-chain scope; never apologise for it. An unnamed off-chain dependency is the weaker report.

**Two rules survive the cull, because they are about numbers rather than narrative.** Never upgrade something unresolved into a verified absence: "no admin control found" and "no admin control exists" are different claims, and only one is checkable. And a verified absence is a finding, so state it plainly in a row rather than omitting it.

## Presentation

- Tables. Prose is the exception, never the frame.
- State the denominator wherever a percentage appears; a reader cannot check the work otherwise.
- No composite risk indices and no raw node risk scores.
- Exposure sizing, not expected loss: full compromise, no probability weighting, no recovery. Once, in the footer.
- Truncate addresses consistently, and give enough to verify.
- Avoid em dashes. Use commas, colons, or restructure.

**Name things the way a reader would.** "Subject", "target" and "vector" are this skill's internal vocabulary; on the page say what each one is:

| Internal term | What the header says |
|---|---|
| target | **Portfolio analysed** |
| subject | **What could fail** |
| vector / vector_class | **Failure assumed**, as a plain sentence: "LBTC becomes worthless" |

**Provenance goes in the footer, not the header.** Partition id, path count, hop depth and the floor formula are reproducibility apparatus: they must appear, but they are the last thing a reader needs.

**Do not narrate how the denominator was measured.** The figure belongs in the header, because every percentage is a share of it. Its derivation is the analyst's working: record it in `denominator_basis`, which `--check` prints and the renderer deliberately does not, so it stays verifiable without occupying the page.

## The targeted blast report has a fixed shape

Use these headings, in this order, so two reports on different subjects read the same way:

| | Heading | Holds |
|---|---|---|
| Header | `Impact of <asset> on <portfolio>` | Portfolio analysed, What could fail, Failure assumed. Three lines |
| Hero | two figures, no third | **Current estimated loss** and **Maximum loss**. Never a separate exposure card: on most paths it equals the maximum loss, and three near-identical figures cannot be told apart |
| 1 | **Impact and contagion paths** | The diagram, then `Path detail`: the same paths as a ranked table |
| 2 | **Defensive borrowing: what the loss becomes in a rush** | The competing constraints per market, the binding one marked |
| Close | one reconciliation line | Paths plus unconnected against the portfolio total, with the drift |
| Footer | one line | Chain, snapshot, graph partition, reporting floor and the sizing basis |

Anything that does not fit those rows is not a section; it is a reply to a question the user has not asked yet.

- **Number rows; do not expose path ids.** `P1` and `P2` are internal keys that read as priority labels. Rank the rows `1..n`; where one row is superseded by another, say "same dollars as row 2".
- **Name a path for where the money sits**, not for the edges it traverses: "Morpho LBTC / PYUSD market", with the diagram carrying the route. Never print a bare `(?)` or a placeholder glyph; if a value is unknown, the row says what is unknown in words.
- **A reconciliation line states both of its addends** and what they sum to, then in one clause what the check is for: a traversal that quietly missed something must not be able to look like one that found everything.
- **Do not give a column to a percentage derived from two others on the same row.** Market LTV beside LLTV already shows the headroom; a third percentage makes all three look independent. The same applies to any ratio whose numerator and denominator are both already columns. Quote the derivation in prose where it carries a finding.
- **Qualifying text spans the table, not a column:** its own row with a `colspan` across the remaining columns. A totals row is labelled **Total**.
- **A targeted report opens with a diagram, not a table.** Draw the spine left to right, portfolio to failing asset, with every intermediate contract as its own box (the adapter that routes the money, the market, the asset), and hang what sits beside the path (oracles, bridge lockboxes, admin role sets) underneath the node it attaches to. The diagram replaces a separate intermediary register, so do not also table what it shows. `build_blast_report.py` draws it from `paths[].chain` plus any `intermediaries` carrying an `attach` label. Keep node labels short (about 40 characters, sub-labels about 30); longer ones wrap.

## Hand it over

Save the report where the user can open it, then hand it over with whatever the environment provides for files: a path in a terminal, the file or artifact tool in a chat app. Do not paste its contents into the conversation.
