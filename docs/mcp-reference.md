# Forta Risk Graph MCP — reference

Source of truth is the MCP server itself: the `instructions` field it returns
on `initialize`, and its live `tools/list`. This page is the skill-author's
digest. When the two disagree, the server wins.

## Endpoints

| Environment | URL |
|---|---|
| Production | `https://risk-graph-mcp.forta.network/mcp` |

Transport is Streamable HTTP.

```bash
claude mcp add --transport http risk-graph https://risk-graph-mcp.forta.network/mcp
```

## Auth

Two independent gates, both server-side:

- **Inbound** — the transport requires a valid API key before the MCP protocol
  or any tool runs.
- **Outbound** — the MCP server forwards the caller's JWT verbatim to the
  risk-graph API, which does the Auth0 RS256 + membership + admin
  authorization. **The MCP server mints no credentials.**

For an interactive client this is an OAuth flow: the server proxies to Auth0 and
the first tool call opens a browser. Machine-to-machine callers provision
credentials separately — not a skill concern.

Consequences a skill must handle:

- A **403** means the caller's identity lacks that controller or that admin
  scope. It is not a graph result. Say so; never degrade it into "no data".
- Reads are scoped to the caller's controller. Two users legitimately see
  different totals for the same token. Never state a figure as global.

## Partition and chain scope

The server injects its own `graph_id` onto any unanchored read path, and names
it in rule 1 of the served instructions. Production is `risk-graph-rt-v3`.

**Production indexes `chain_id 1` (Ethereum mainnet) only.** A position on any
other chain is absent and indistinguishable from absence. Every "complete",
every "exact" and every zero row is scoped to mainnet. A skill that reconciles a
multi-chain book must name the chains the graph cannot see.

## Tool inventory (production, 32 tools)

Call `get_schema` before traversing — it serves the live edge vocabulary and
per-type direction. This table is a map, not a contract; `tools/list` is
authoritative.

### Discovery / resolution
| Tool | Use for |
|---|---|
| `get_schema` | live node + edge vocabulary, per-type direction, retired types |
| `graph_summary` | partition-level counts by category / chain / project |
| `resolve_address` | one address → node + bounded neighbourhood (50 nodes / 100 edges default; page with `nodeCursor` / `edgeCursor`) |
| `resolve_entities` | controller-wide entity resolution, un-rooted (pre-trade asset picker) |
| `search_nodes` | text / symbol search |
| `query_nodes` | filtered node listing |
| `get_node` | one node's full property set |
| `get_node_relationships` | one node's edges, defaults to 25, reports exact `totalNodeCount` |
| `available_traversals` | which traversals are legal from a given node |

### Risk lenses
| Tool | Use for |
|---|---|
| `get_admin_risk` | per-token attack-vector breakdown; `keys=true` for the key-level detail |
| `get_measures` | computed risk measures on a node |
| `get_governance` | governance surface / controllers |
| `get_concentration` | holder / provider concentration |
| `get_backing` | backing legs (reserves, wrappers, CDP, facilitator) |
| `get_denominator` | which denominator a figure is computed against |
| `wallet_risk_profile` | a wallet's aggregate risk posture |
| `get_levered_position` | leveraged position detail |
| `get_withdrawable_capacity` | exit capacity, not NAV |
| `blast_radius` | forward reachability from a compromise |
| `dependency_closure` | everything a node depends on |
| `find_path` / `find_connections` | paths between two nodes |

### Simulation
| Tool | Use for |
|---|---|
| `simulate_position` | one hypothetical position |
| `simulate_basket` | a basket of positions |

### Limits and alerts
| Tool | Use for |
|---|---|
| `list_limits` / `get_limit` | configured risk limits |
| `get_alerts` | fired alerts |

### Custody config
| Tool | Use for |
|---|---|
| `list_custody` | custody structures |
| `get_custody_wallet_ids` | configured custody wallet ids |
| `get_custody_wallet_ids_history` | change history |
| `update_custody_wallet_ids` | **write, Admin-gated** — never call from a read skill |

### Escape hatch
| Tool | Use for |
|---|---|
| `read_cypher` | raw read-only Cypher |

## `read_cypher` — last resort, not first

Prefer a typed tool. The typed tools carry server-resolved semantics a raw query
does not: `semantic_direction.controller_id` on admin edges, `is_aggregate_node`
on hubs, `meta.aggregates`, `canonical_owner_id` on borrower rows. A raw Cypher
query that reproduces the same MATCH gets none of that and silently
double-counts.

Hard constraints when you do use it:

- `LIMIT` is capped. Keep matches directed and single-typed.
- Single-argument `round()` only. `round(x, 2)` returns an opaque **502** that
  looks like an outage rather than a rejected query.
- `coveragePct` is **always null when truncated**. Get totals from an explicit
  aggregate query, never by counting returned rows.
- Never filter `graph_id` on a **relationship** — thousands of edges carry no
  stamp, so the filter hides real edges and the empty result reads as "no owner".

## The `meta` block is not optional reading

Every response carries one. Before quoting any figure:

- `meta.page.truncated` — a truncated set cannot produce a coverage or gap statistic.
- `meta.declared` — names the guarantees backing **this** response. Every `false`
  is a gap to state out loud. An **absent** section means UNKNOWN, never zero.
- `meta.cellGaps` — absent means no cell was examined, never zero gaps.
- `meta.asOf.block` — the partition mutates mid-session and no snapshot can be
  pinned. **Never sum figures from responses whose `asOf.block` differ.**

## Errors

A tool that fails returns an error, not an empty result — the two are not
interchangeable and a skill must not collapse one into the other. A **403** is
an authorization outcome, a **502** from `read_cypher` is usually a rejected
query (see the `round()` note above), and a timeout is unknown coverage, not
zero rows. Report the failure; never fold it into the answer as absence.
