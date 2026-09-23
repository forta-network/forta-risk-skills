# Contributing

## Read first

`docs/graph-semantics.md`. It carries the one rule that most changes what you
write: **skills encode workflow, the server owns semantics.**

The MCP server ships its semantic rules in the `instructions` field returned on
every `initialize`, rendered from the deployment's actual configuration. A skill
that restates one of those rules forks it, and the fork goes stale silently —
which is the exact failure this MCP's rules exist to prevent. Reference a rule
by number; never quote it.

Then connect the MCP and run some tool calls by hand. A skill written without
having seen a real response is guesswork.

```bash
claude mcp add --transport http risk-graph https://risk-graph-mcp.forta.network/mcp
```

## Before opening a PR

```bash
./scripts/validate-skills.sh
python3 scripts/lint-cypher-anchors.py
```

That is structure only — frontmatter, naming, JSON manifests. It cannot tell
whether a skill is correct.

So also **run the skill**, from a cold session, using only a phrase from its
`description`:

```
/plugin marketplace add /absolute/path/to/this/repo
/plugin install forta-risk@forta-risk
```

Observe two things: did it load without a nudge, and did it follow its own
numbered steps. If it needed a nudge, the `description` is the problem, not the
body. Paste the transcript into the PR.

A skill that has never run is presumed broken. This is not negotiable — the
failures this MCP produces are confident wrong numbers, not errors, so a skill
cannot be reviewed on the page alone.

## PR description

```
Skill: <name>
The question it answers: <one sentence>
The trap it encodes: <the specific silent-wrong-answer it prevents>
Exercised against production: yes — transcript below
```

If the last line would be "no", the PR is not ready.

## Scope

- **One skill per PR.**
- **No write tools.** `update_custody_wallet_ids` is Admin-gated and must not be
  reachable from any skill here.
- **No scoring skills.** The graph produces quantities and mechanisms; a letter
  grade launders their uncertainty away.
- **Every report states its gaps.** A number without its denominator, coverage
  and as-of block is not an answer.

## Where changes land

Skills are developed in a private upstream repo and copied here. A fix landed
here is backported upstream by a maintainer — one direction, so the two copies
cannot diverge silently. Open the PR here anyway; that backport is our problem,
not yours.
