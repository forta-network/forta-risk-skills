# Forta Risk Graph — skills

Claude skills for the **Forta Risk Graph MCP**: a live graph of on-chain
positions, governance, oracles, lending markets, custody and backing for
tokenized assets.

The MCP server answers queries. These skills carry the workflow — which tools in
which order for a given question, what to compute from the responses, how to
report it, and what to refuse rather than guess.

## Install

Add this repo as a plugin marketplace, then install the plugin:

```
/plugin marketplace add forta-network/forta-risk-skills
/plugin install forta-risk@forta-risk
```

Then add the MCP server. **The skills do nothing without it.**

```bash
claude mcp add --transport http risk-graph https://risk-graph-mcp.forta.network/mcp
```

The server is OAuth-gated: the first tool call opens a browser to authenticate.
Access is per-account and reads are scoped to your own controller — two users
legitimately see different totals for the same token.

Update later with `/plugin marketplace update forta-risk`.

## What is here

| Skill | Answers |
|---|---|
| `forta-risk-analysis` | **Dependency concentration** — what could make a vault, wallet, Safe or token lose money. **Control closure** — every contract and admin key that can cause user loss across a protocol, down to terminal keys. **Blast radius** — who loses money if a given asset, oracle, admin key, custodian or bridge is compromised. |

It loads on its own from a phrase in the question; no slash command needed. Ask
about exposure, dependencies, single points of failure, admin keys, contagion or
who can take user funds, or paste a contract address in a risk context.

Report requests render to standalone HTML via the Python scripts under
`skills/forta-risk-analysis/scripts/` (stdlib only, no install step).

## Scope, and what the graph cannot see

Both matter more than they usually do, because this graph's failure mode is not
an error — it is a confident wrong number that passes every internal consistency
check and points toward **less** risk than there really is.

- **Ethereum mainnet only.** A position on any other chain is absent, and
  absence is indistinguishable from zero.
- **Escrowed collateral is not modelled.** Any levered entity is understated.
- **Withdrawable capacity is not NAV.**
- A filtered query returning nothing means **unknown**, not none.

`docs/graph-semantics.md` is the full version, and it is the first thing to read
before writing your own skill against this MCP. `docs/mcp-reference.md` is the
endpoint, auth model and tool inventory.

## Support and security

Broken skill, wrong figure, or a gap you need modelled: open an issue on this
repo. For anything security-sensitive use **Security → Report a vulnerability**
on this repo instead of a public issue.

## License

MIT. See `LICENSE`.
