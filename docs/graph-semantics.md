# Graph semantics — read this before writing a skill

## The rule

**A skill must not restate the graph's semantic rules.**

The MCP server ships 26 of them in the MCP `instructions` field, returned on
every `initialize` — so every client has them before its first tool call. They are rendered from the deployment's *actual* configuration (partition name,
chain scope) and pinned by tests on the server side.

A skill that copies them creates a second copy that:

- goes stale when the server's changes, silently;
- contradicts the server's version, with no way for the model to tell which is right;
- multiplies — the same rule ends up in three skills and four report templates.

That is exactly the class of failure the rules exist to prevent. So:

> **Semantics come from the server. Skills encode workflow.**

## What "workflow" means

Legitimate skill content:

- which tools, in which order, for a given question shape;
- what to compute from the responses (ratios, groupings, denominators);
- how to structure the answer;
- **what to refuse to answer, and the wording of the refusal**;
- domain judgment the graph does not encode (what counts as a concerning
  concentration, when a governance delay is protective).

Not skill content:

- "node ids are lowercase hex";
- "ADMIN_CTRL runs admin→contract";
- the edge vocabulary;
- "zero rows means unknown, not none".

## Referencing a rule

When workflow depends on a rule, **name it, don't quote it**:

> Group borrower rows on the server-provided `canonical_owner_id` (server
> instructions, rule 16) before computing per-owner exposure.

That survives a rewording of the rule. A verbatim quote does not.

## The failure mode these rules guard against

Not errors — **confident wrong numbers that pass every internal consistency
check, and that point overwhelmingly toward less risk than there really is.**

Three real examples:

- a checksummed address returns zero rows with no warning → "no admin";
- an unmodelled dollar figure returns `0` → "no exposure";
- a brand-name `project` filter returns a self-consistent total that omits the
  protocol's largest market (Euler: `project` and `lending_protocol` differ by
  18.8%).

So the standing bias for every skill: **a report must state its gaps.** A number
without its denominator, coverage, and as-of block is not an answer.

## The five habits every skill should carry

1. **Call `get_schema` before traversing.** Retired and nonexistent edge types
   return `[]` silently, and `[]` reads as "no risk".
2. **Read `meta` before quoting.** Truncation, declared guarantees, `asOf.block`.
3. **Never sum across differing `asOf.block`.** The partition mutates mid-session.
4. **Distinguish unknown from zero, every time.** A filtered query returning
   nothing means UNKNOWN. Name what the filter dropped, in dollars.
5. **Name the scope.** Mainnet only; caller's controller only; graph excludes
   escrowed collateral, so any levered entity is understated.

## Known structural gaps to state, never infer

The server flags these as things to ask about because they are not tool-shaped
yet. If a skill's answer depends on one, say it is a gap — do not fill it from
world knowledge:

- **Escrowed collateral is not in the graph.** Any levered entity is understated.
  Say so in every report on one.
- **Backing and custody one hop past the last on-chain token** is unmodelled for
  custodial wrappers and off-chain credit.
- **The base asset is reachable only via `HOLDS`**, never a dependency-typed
  edge — a type-restricted traversal misses it.
- **Withdrawable capacity** ≠ NAV. Supplied minus borrowed per market against
  the entity's own position.
- **Depositor concentration** does not appear in any position table. A single 97%
  depositor is a solvency-of-exit event.
