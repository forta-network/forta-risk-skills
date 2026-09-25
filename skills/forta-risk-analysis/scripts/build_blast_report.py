#!/usr/bin/env python3
"""
Build a blast radius (reverse impact) report from a findings JSON file.

Two modes, matching the two modes in SKILL.md:

  wide      - rank everyone who loses money if the subject is compromised.
              Additive dollars and re-attributed dollars are kept in separate
              tables and never summed across.
  targeted  - one number for one named target, plus every path that carries it
              and every intermediary standing on those paths.

The arithmetic this script owns, so that no two reports disagree about it:

  * targeted total  = sum over DISTINCT position keys of the MAX loss booked
                      against that key. Within a key, channels are alternative
                      reasons for the same dollars, so the max wins and the sum
                      would multi-count the largest position in the analysis.
  * targeted totals are checked against the target's own assets. A total above
                      assets means positions were double-counted, and the script
                      refuses to render unless "leveraged": true says otherwise.
  * position reconciliation = resolved + bounded + unconnected must equal the
                      target's assets. This is what makes a small answer
                      distinguishable from a truncated traversal.
  * wide mode  keeps additive, re-attribution and backing subtotals apart and
                      prints each with its own basis. It never produces a single
                      grand total, because there isn't one.

Usage:
    python3 build_report.py findings.json -o report.html
    python3 build_report.py findings.json --check     # arithmetic only, no HTML
    python3 build_report.py --schema
"""

import argparse
import json
import sys
from html import escape

SCHEMA = """
findings JSON schema
====================

Shared
------
{
  "mode": "targeted" | "wide",              # required
  "subject": {
    "name": "Wrapped BTC (WBTC)",           # required
    "address": "0x2260fac5...",             # required
    "chain": "Ethereum mainnet",
    "snapshot": "21 Aug 2026",
    "partition": "risk-graph-rt-v3",
    "vector": "token value to zero"         # required in targeted mode
  },
  "floor": "0.5% of target assets ($48,000)",
  # ---- targeted mode also accepts, per path:
  #   "defensive_borrowing": {                 # sizes the rush-to-borrow effect
  #      "existing_debt_usd": 3039482,         # required
  #      "collateral_usd": 18644385,           # posted collateral, optional
  #      "lltv_pct": 86,                       # toFloat(lltv)/1e16, optional
  #      "available_liquidity_usd": 243072,    # optional
  #      "borrow_cap_usd": null}               # null = no cap, NOT a zero cap
  #   extra = min(collateral_usd*lltv_pct/100 - debt, liquidity, cap - debt)
  #   Adds a second headline figure and a constraint table. See SKILL.md.
  # ---- chain steps also accept "sub" (text under the node box) and
  #   "edge_sub" (text under the arrow); intermediaries accept "attach",
  #   the label of the spine node they hang beneath in the diagram.
}

Targeted mode
-------------
  "target": {
    "name": "...", "address": "0x...", "type": "Morpho VaultV2",
    "assets": 96670447,                     # required, the denominator
    # denominator_basis is recorded for --check and is NOT rendered in the report
  "denominator_basis": "sum of allocated_usd plus idle; allocated_usd/share_pct
                          identical on all 9 rows",
    "leveraged": false                      # only set true to allow loss > assets
  },

  # one entry per distinct path from target to subject
  "paths": [{
    "id": "P1",                             # required, unique
    "position_key": "mkt_3a85e6",           # required. THE DEDUPE KEY: the leaf
                                            # node where the target's dollars sit
    "position_label": "Morpho WBTC/USDC",
    "position_usd": 71069934,               # target's value in that position
    "mechanism": "isolated_market_collateral",
        # one of: direct_holding | receipt_holding | lp_position |
        #         isolated_market_collateral | multi_collateral_market |
        #         borrow_equity | oracle | admin_control | backing_share | other
    "chain": [                              # required, in order, target -> subject
      {"edge": "VAULT_ALLOCATION", "to": "mkt_3a85e6",
       "to_label": "Morpho WBTC/USDC", "fraction": 0.7352, "note": ""},
      {"edge": "LENDING_COLLATERAL", "to": "0x2260fac5...",
       "to_label": "WBTC", "fraction": 0.918, "note": "sole collateral, utilisation"}
    ],
    "exposure_usd": 71069934,               # value sitting behind the subject
    "loss_usd": 65241800,                   # loss under the stated vector
    "loss_basis": "allocation 71.07M x utilisation 0.918",
    "ceiling": false,                       # true if the figure is a ceiling
    "unresolved": false                     # true if the path is not fully resolved
  }],

  "intermediaries": [{
    "id": "0xbfe734ba...", "label": "MorphoMarketV1Adapter",
    "what": "vault adapter", "role": "routes 100% of assets into every market",
    "paths": ["P1", "P2"], "value_behind_usd": 71069934,
    "timelock_seconds": 0
  }],

  "unconnected": [{"position": "Idle USDC", "usd": 18318238,
                   "why": "denomination asset, no path to the subject"}],

  "bounded": [{"path": "target -> Morpho Blue singleton",
               "position_usd": 32646897,   # the position's OWN value. REQUIRED for
                                           # the reconciliation to close: a bounded
                                           # position still holds the target's money
               "resolved_usd": 6142521,    # only the part that reached the subject
               "upper_bound_usd": 1681228809,
               "missing": "VAULT_ALLOCATION points at the singleton, not market ids"}],

  "common_cause": [{"node": "0x3af3e85f", "label": "Paxos admin",
                    "shared_with": ["USDG", "USDP", "PYUSD"],
                    "combined_usd": 4130000000,
                    "note": "different vector, do not add to the path total"}]

Wide mode
---------
  "denominator": {"name": "market cap", "value": 7519987593,
                  "coverage_usd": 6723000000, "coverage_pct": 89.4},
  "impacts":      [{"family": "Aave", "node": "aWBTC", "layer": "receipt",
                    "hops": 2, "usd": 1160000000, "note": ""}],
  "additive":     [{"path": "Compound USDC bad debt", "layer": "lending",
                    "hops": 2, "usd": 98000000, "note": "56.5% pro-rated"}],
  "reattribution":[{"layer": "Receipt tokens", "usd": 2125260501,
                    "resolves": "to end wallets; never add to the reserve position"}],
  "backing":      [{"asset": "crvUSD", "mechanism": "minted against",
                    "share_pct": 47.3, "basis": "collateral-backed debt $36.7M"}]

Notes
-----
* Every "fraction" is the share of the upstream node's value transmitted by that
  hop. The path loss is the position value times the product of the fractions,
  unless "loss_basis" documents a mechanism-specific formula (lending does).
* "ceiling": true rows are excluded from the headline total and reported
  separately as an upper bound, because a ceiling is not a measurement.
"""

# Which failure vectors each mechanism can actually transmit. Maxing across
# channels is only legitimate inside ONE vector: an asset going to zero, its
# oracle printing a wrong price and its admin key being drained are three
# different events, and combining them manufactures a compound failure nobody
# asked about. This table is what stops that happening silently.
VECTORS = {
    "value_to_zero": "the subject's value goes to zero",
    "wrong_price": "the subject prints a wrong price",
    "drain": "the subject's control is used to drain what it holds",
    "custody_loss": "the custodied balance is lost",
    "unbacked_supply": "the wrapped or issued supply becomes unbacked",
}

MECH_VECTORS = {
    "direct_holding": {"value_to_zero", "custody_loss", "unbacked_supply"},
    "receipt_holding": {"value_to_zero", "custody_loss", "unbacked_supply", "drain"},
    "lp_position": {"value_to_zero", "unbacked_supply"},
    "isolated_market_collateral": {"value_to_zero", "wrong_price", "unbacked_supply"},
    "multi_collateral_market": {"value_to_zero", "wrong_price", "unbacked_supply"},
    "borrow_equity": {"value_to_zero", "unbacked_supply"},
    "oracle": {"wrong_price"},
    "admin_control": {"drain"},
    "backing_share": {"value_to_zero", "custody_loss", "unbacked_supply"},
    "other": set(VECTORS),
}

MECHANISMS = {
    "direct_holding": "Direct holding",
    "receipt_holding": "Receipt token",
    "lp_position": "LP position",
    "isolated_market_collateral": "Isolated market collateral",
    "multi_collateral_market": "Multi-collateral market",
    "borrow_equity": "Borrower equity",
    "oracle": "Oracle",
    "admin_control": "Admin control",
    "backing_share": "Backing share",
    "other": "Other",
}


# ----------------------------------------------------------------- arithmetic

def _rush_paths(f, by_key, offvector_ids):
    """The paths that count toward the rush total: on-vector, and one per position
    key (the highest loss), so two channels onto one market cannot double count."""
    off = set(offvector_ids or [])
    best = {}
    for p in f.get("paths") or []:
        k = p.get("position_key") or p.get("id")
        # A ceiling is excluded from total_loss, so it must be excluded here too:
        # letting one into the rush total makes Maximum loss exceed Current loss
        # by a figure the report elsewhere says is not a measurement.
        if k not in by_key or p.get("id") in off or p.get("ceiling"):
            continue
        v = _rush_loss(p)
        if k not in best or v > best[k][0]:
            best[k] = (v, p)
    return [v for v, _ in best.values()], [p for _, p in best.values()]


def _rush_loss(p):
    d = p.get("defensive_borrowing")
    if not d:
        return float(p.get("loss_usd") or 0)
    extra, _, _ = defensive_borrow(d)
    return float(d.get("existing_debt_usd") or p.get("loss_usd") or 0) + extra


def _rush_total(f, by_key, offvector_ids):
    if not any((p.get("defensive_borrowing") for p in (f.get("paths") or []))):
        return 0.0
    vals, _ = _rush_paths(f, by_key, offvector_ids)
    return sum(vals)


def _dead(f, by_key):
    best = {}
    for p in f.get("paths") or []:
        k = p.get("position_key") or p.get("id")
        if k not in by_key:
            continue
        best[k] = 0.0
    return sum(best.values())


def compute_targeted(f):
    tgt = f.get("target") or {}
    assets = float(tgt.get("assets") or 0)
    paths = f.get("paths") or []

    vclass = (f.get("subject") or {}).get("vector_class")

    by_key, offvector = {}, []
    for p in paths:
        k = p.get("position_key")
        if not k:
            raise SystemExit("every path needs a position_key (the dedupe key)")
        mech = p.get("mechanism") or "other"
        allowed = MECH_VECTORS.get(mech, set(VECTORS))
        if vclass and vclass in VECTORS and vclass not in allowed:
            # This path cannot be transmitted by the declared vector, so it is not
            # part of this answer. Held out of the total and reported separately.
            offvector.append(p)
            continue
        by_key.setdefault(k, []).append(p)

    # within a key: max, never sum. Ceilings are held out of the headline.
    keys = {}
    for k, group in by_key.items():
        firm = [p for p in group if not p.get("ceiling")]
        ceil = [p for p in group if p.get("ceiling")]
        best = max(firm, key=lambda p: float(p.get("loss_usd") or 0)) if firm else None
        keys[k] = {
            "label": (best or group[0]).get("position_label") or k,
            "position_usd": float((best or group[0]).get("position_usd") or 0),
            "loss": float(best.get("loss_usd") or 0) if best else 0.0,
            "exposure": max((float(p.get("exposure_usd") or 0) for p in firm), default=0.0),
            "channels": len(group),
            "winner": best.get("id") if best else None,
            "also": [p.get("id") for p in group if best and p.get("id") != best.get("id")],
            "ceiling_usd": sum(float(p.get("loss_usd") or 0) for p in ceil),
            "unresolved": any(p.get("unresolved") for p in group),
        }

    total_loss = sum(v["loss"] for v in keys.values())
    total_exposure = sum(v["exposure"] for v in keys.values())
    total_ceiling = sum(v["ceiling_usd"] for v in keys.values())
    resolved_positions = sum(v["position_usd"] for v in keys.values())

    unconnected = sum(float(u.get("usd") or 0) for u in (f.get("unconnected") or []))
    # A bounded path's POSITION still holds the target's money even though the
    # link to the subject is unresolved, so the position value is what has to
    # appear in the reconciliation. resolved_usd is only the part that reached
    # the subject and is usually 0 on a bounded row; using it here silently
    # dropped the whole position out of the denominator.
    bounded_resolved = sum(
        float(b.get("position_usd") if b.get("position_usd") is not None
              else (b.get("resolved_usd") or 0))
        for b in (f.get("bounded") or []))
    bounded_reached = sum(float(b.get("resolved_usd") or 0) for b in (f.get("bounded") or []))
    bounded_upper = sum(float(b.get("upper_bound_usd") or 0) for b in (f.get("bounded") or []))

    deepest = max((len(p.get("chain") or []) for p in paths), default=0)
    offvector_ids = [p.get("id") for p in offvector]

    return {
        "assets": assets,
        "keys": keys,
        "total_loss": total_loss,
        "total_exposure": total_exposure,
        "total_ceiling": total_ceiling,
        "resolved_positions": resolved_positions,
        "unconnected": unconnected,
        "bounded_resolved": bounded_resolved,
        "bounded_reached": bounded_reached,
        "bounded_upper": bounded_upper,
        "reconciled": resolved_positions + unconnected + bounded_resolved,
        "loss_pct": (total_loss / assets * 100) if assets else 0,
        "exposure_pct": (total_exposure / assets * 100) if assets else 0,
        "deepest": deepest,
        "n_paths": len(paths),
        "vclass": vclass,
        "rush_loss": _rush_total(f, by_key, offvector_ids),
        "offvector": offvector,
        "offvector_ids": offvector_ids,
    }


def check_targeted(f, c):
    out, fatal = [], []
    assets = c["assets"]
    if not assets:
        fatal.append("target.assets is missing or zero, so no percentage is meaningful")

    if assets and c["total_loss"] > assets * 1.0001:
        msg = ("total loss %s exceeds target assets %s by %s"
               % (money(c["total_loss"]), money(assets),
                  money(c["total_loss"] - assets)))
        if (f.get("target") or {}).get("leveraged"):
            out.append("ALLOWED: " + msg + " (target flagged leveraged)")
        else:
            fatal.append(msg + ". Positions are double-counted: check position_key.")

    if assets:
        drift = abs(c["reconciled"] - assets) / assets * 100
        line = ("position reconciliation: resolved %s + unconnected %s + bounded %s = %s "
                "against assets %s (%.2f%% off)"
                % (money(c["resolved_positions"]), money(c["unconnected"]),
                   money(c["bounded_resolved"]), money(c["reconciled"]),
                   money(assets), drift))
        (out if drift <= 1.0 else fatal).append(
            ("OK: " if drift <= 1.0 else "positions do not reconcile. ") + line)

    for k, v in c["keys"].items():
        if v["loss"] > v["position_usd"] * 1.0001 and v["position_usd"]:
            fatal.append("position %s: loss %s exceeds the position itself %s"
                         % (k, money(v["loss"]), money(v["position_usd"])))
        if v["channels"] > 1:
            out.append("position %s: %d channels, kept %s, dropped %s (max, not sum)"
                       % (k, v["channels"], v["winner"], ", ".join(v["also"]) or "none"))

    for p in f.get("paths") or []:
        chain = p.get("chain") or []
        if not chain:
            fatal.append("path %s has an empty chain" % p.get("id"))
        prod = 1.0
        for h in chain:
            fr = h.get("fraction")
            if fr is not None:
                prod *= float(fr)
        implied = float(p.get("position_usd") or 0) * prod
        loss = float(p.get("loss_usd") or 0)
        if implied and abs(implied - loss) / max(implied, loss, 1) > 0.02 and not p.get("loss_basis"):
            out.append("path %s: chain product implies %s but loss_usd is %s, and no "
                       "loss_basis explains the difference"
                       % (p.get("id"), money(implied), money(loss)))

    if c.get("rush_loss"):
        if c["rush_loss"] < c["total_loss"] - 1:
            fatal.append("rush loss %s is below the base loss %s, which cannot happen: "
                         "defensive borrowing only adds"
                         % (money(c["rush_loss"]), money(c["total_loss"])))
        _, kept = _rush_paths(f, c["keys"], c.get("offvector_ids"))
        tbl = sum(_rush_loss(p) for p in kept if p.get("defensive_borrowing"))
        base = sum(_rush_loss(p) for p in kept)
        if abs(base - c["rush_loss"]) > 1:
            fatal.append("headline rush loss %s does not match the deduped path set %s"
                         % (money(c["rush_loss"]), money(base)))
        if tbl and abs(tbl + (base - tbl) - c["rush_loss"]) > 1:
            fatal.append("section 2 total %s cannot be reconciled with the headline %s"
                         % (money(tbl), money(c["rush_loss"])))

    subj = f.get("subject") or {}
    if not subj.get("vector"):
        fatal.append("subject.vector is required in targeted mode: an asset going to "
                     "zero, a wrong oracle price and a drained admin key are three "
                     "different events with three different numbers")
    vclass = subj.get("vector_class")
    if not vclass:
        fatal.append("subject.vector_class is required, one of: %s. Without it the "
                     "channels cannot be checked for vector consistency and the total "
                     "silently becomes a max across incompatible events."
                     % ", ".join(sorted(VECTORS)))
    elif vclass not in VECTORS:
        fatal.append("subject.vector_class %r is not one of: %s"
                     % (vclass, ", ".join(sorted(VECTORS))))

    if c["offvector"]:
        out.append("held out of the total, mechanism cannot transmit vector %r: %s. "
                   "Report these as a separate vector, never maxed into this one."
                   % (vclass, ", ".join(p.get("id") or "?" for p in c["offvector"])))
    if c["offvector"] and not by_vector_note(f):
        out.append("consider a second run with the matching vector_class so those "
                   "paths get their own number rather than being dropped")
    return out, fatal


def by_vector_note(f):
    return bool((f.get("subject") or {}).get("other_vectors"))


def compute_wide(f):
    den = f.get("denominator") or {}
    v = float(den.get("value") or 0)
    impacts = sum(float(i.get("usd") or 0) for i in (f.get("impacts") or []))
    additive = sum(float(i.get("usd") or 0) for i in (f.get("additive") or []))
    reattr = sum(float(i.get("usd") or 0) for i in (f.get("reattribution") or []))
    return {"denominator": v, "impacts": impacts, "additive": additive,
            "reattribution": reattr,
            "impacts_pct": (impacts / v * 100) if v else 0}


def check_wide(f, c):
    out, fatal = [], []
    if not c["denominator"]:
        fatal.append("denominator.value is missing: every percentage needs it stated")
    out.append("direct + indirect on the subject: %s (%.1f%% of the denominator)"
               % (money(c["impacts"]), c["impacts_pct"]))
    out.append("additive dollars on non-subject assets: %s, reported separately"
               % money(c["additive"]))
    out.append("re-attribution layers: %s, NOT added to anything"
               % money(c["reattribution"]))
    return out, fatal


# ------------------------------------------------------------------- render

def money(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return "n/a"
    a = abs(x)
    if a >= 1e9:
        return "$%.2fB" % (x / 1e9)
    if a >= 1e6:
        return "$%.2fM" % (x / 1e6)
    if a >= 1e3:
        return "$%.0fK" % (x / 1e3)
    return "$%.0f" % x


def exact(x):
    try:
        return "${:,.0f}".format(float(x))
    except (TypeError, ValueError):
        return "n/a"


def addr(a):
    if not a or len(a) < 14:
        return escape(a or "")
    return escape(a[:8] + ".." + a[-4:])


def pct(x):
    return "%.2f%%" % x if x else "0%"


CSS = """
:root{--ink:#14161a;--dim:#6a7280;--line:#e3e6ea;--bg:#fff;--soft:#f7f8fa;
--hot:#b4232c;--warm:#c8791b;--cool:#2f6f8f;--ok:#2b7a4b}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:40px 28px 80px}
h1{font-size:27px;line-height:1.2;margin:0 0 6px;letter-spacing:-.01em}
h2{font-size:17px;margin:38px 0 12px;padding-bottom:7px;border-bottom:1px solid var(--line)}
h3{font-size:14px;margin:24px 0 8px;color:var(--dim);text-transform:uppercase;
letter-spacing:.06em}
.meta{color:var(--dim);font-size:13px;margin:0 0 4px}
.meta b{color:var(--ink);font-weight:600}
.heads{display:flex;flex-wrap:wrap;gap:14px;margin:22px 0 4px}
.head{flex:1 1 200px;border:1px solid var(--line);border-radius:9px;padding:14px 16px;
background:var(--soft)}
.head .k{font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--dim)}
.head .v{font-size:23px;font-weight:650;margin-top:3px;letter-spacing:-.02em}
.head .s{font-size:12px;color:var(--dim);margin-top:2px}
.head.hot .v{color:var(--hot)}
blockquote{margin:22px 0;padding:14px 18px;background:var(--soft);
border-left:3px solid var(--cool);border-radius:0 7px 7px 0}
blockquote p{margin:0}
table{width:100%;border-collapse:collapse;font-size:13.5px;margin:10px 0 4px}
th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.06em;
color:var(--dim);font-weight:600;padding:7px 9px;border-bottom:1px solid var(--line)}
td{padding:8px 9px;border-bottom:1px solid #f0f2f4;vertical-align:top}
tr:last-child td{border-bottom:none}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
tfoot td{font-weight:650;border-top:1px solid var(--line);background:var(--soft)}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px}
.chain{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;
line-height:1.9;color:var(--ink)}
.chain .e{color:var(--cool)}
.chain .f{color:var(--warm)}
.chain .arw{color:var(--dim)}
.tag{display:inline-block;font-size:10.5px;padding:1px 6px;border-radius:4px;
background:#eef1f4;color:var(--dim);text-transform:uppercase;letter-spacing:.05em}
.tag.c{background:#fdf3e3;color:var(--warm)}
.tag.u{background:#fdeceb;color:var(--hot)}
.note{font-size:12px;color:var(--dim)}

/* Print / PDF. Backgrounds here are load-bearing, not decoration: the binding
   constraint in the rush table and the drained rows in the incidence table are
   marked by colour alone, and Chrome drops backgrounds unless told otherwise. */
@media print{
  @page{size:A4;margin:14mm 12mm}
  *{-webkit-print-color-adjust:exact;print-color-adjust:exact}
  body{font-size:10.5px}
  .wrap{max-width:none;padding:0}
  h1{font-size:19px}
  h2{font-size:14px;break-after:avoid;page-break-after:avoid}
  h3{break-after:avoid;page-break-after:avoid}
  table{break-inside:auto}
  thead{display:table-header-group}
  /* thead SHOULD repeat per page; tfoot must NOT. A Total repeated at the
     foot of every page reads as a subtotal of the rows above it. */
  tr{break-inside:avoid;page-break-inside:avoid}
  /* a figure row and the sentence qualifying it must not be split across pages */
  tr.basis{break-before:avoid;page-break-before:avoid}
  .heads{break-inside:avoid;page-break-inside:avoid}
  .head{flex:1 1 auto}
  .diag{break-inside:avoid;page-break-inside:avoid;overflow:visible}
  .diag svg{min-width:0;width:100%;height:auto}
  .note,.warn{break-inside:avoid;page-break-inside:avoid}
  a{text-decoration:none;color:inherit}
}
.diag{margin:18px 0 8px;padding:14px 6px;background:var(--soft);
border:1px solid var(--line);border-radius:9px;overflow-x:auto}
.diag svg{display:block;min-width:640px}
.diag text{font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
text-anchor:middle}
.diag .nl{font-size:12px;font-weight:600;fill:var(--ink)}
.diag .ns{font-size:10.5px;fill:var(--dim)}
.diag .el{font-size:10px;fill:var(--cool);letter-spacing:.04em;font-weight:600}
.diag .es{font-size:10px;fill:var(--dim)}
.diag rect{fill:#fff;stroke:var(--line);stroke-width:1.5}
.diag .n0 rect{stroke:var(--cool);stroke-width:2}
.diag .nx rect{stroke:var(--hot);stroke-width:2;fill:#fdf6f5}
.diag .nb rect{fill:var(--soft);stroke-dasharray:3 3}
.diag .nb .nl{font-size:11px;font-weight:500}
.diag .nb .ns{font-size:9.5px}
.diag .ar line{stroke:var(--dim);stroke-width:1.5}
.diag .ar path,.diag marker path{fill:var(--dim)}
.diag .brl{stroke:var(--line);stroke-width:1.5;stroke-dasharray:3 3;fill:none}
tr.sum td{background:var(--soft);font-weight:600}
tr.basis td{border-top:0;padding-top:0;padding-bottom:11px}
.warn{border:1px solid #f0c9c6;background:#fdf6f5;border-radius:8px;padding:12px 15px;
margin:14px 0;font-size:13px}
.warn b{color:var(--hot)}
ul.cav{padding-left:20px;font-size:13.5px;color:#3a4048}
ul.cav li{margin:5px 0}
.foot{margin-top:44px;padding-top:14px;border-top:1px solid var(--line);
font-size:12px;color:var(--dim)}
"""


def render_chain(chain, subject_label):
    out = ['<div class="chain">']
    for i, h in enumerate(chain):
        fr = h.get("fraction")
        frtxt = ('<span class="f">%.4g</span>' % float(fr)) if fr is not None else "?"
        lbl = escape(h.get("to_label") or h.get("to") or "")
        note = h.get("note")
        out.append('%s<span class="arw">&nbsp;&mdash;</span><span class="e">%s</span> '
                   '(%s)<span class="arw">&rarr;</span> <b>%s</b>%s<br>'
                   % ("&nbsp;" * (2 * i), escape(edge_label(h.get("edge"))), frtxt, lbl,
                      ('<span class="note"> &middot; %s</span>' % escape(note)) if note else ""))
    out.append("</div>")
    return "".join(out)


# ------------------------------------------------------------------ diagram
# Inline SVG so the report stays a single self-contained file with no script
# tags and no network fetch. One horizontal spine per path, target on the left
# and the failing asset on the right, with branch nodes hanging underneath the
# spine node they attach to.

# The graph's relationship type names are precise and are not reader-facing. A
# report says what the link IS, not what the schema calls it. Anything not in
# this map renders as authored, so a plain phrase can always be passed instead.
EDGE_PLAIN = {
    "HOLDS": "holds",
    "RECEIPT_FOR": "is a claim on",
    "LENDING_COLLATERAL": "accepted as collateral by",
    "LENDING_BORROW": "borrowed from",
    "VAULT_ALLOCATION": "allocates to",
    "VAULT_ASSET": "denominated in",
    "ORACLE_DEP": "priced by",
    "WRAP_UNWRAP": "wraps",
    "BACKED_BY": "backed by",
    "BRIDGE_BACKED_BY": "bridge-locked in",
    "RESERVE_BACKING": "reserves held at",
    "ADMIN_CTRL": "controls",
    "ADMIN_OF": "controlled by",
    "CURATES": "curated by",
    "CUSTODY_VIA": "custodied by",
    "DEBT_FOR": "debt token for",
    "POOL_ASSET": "pooled with",
    "OWNS": "signer of",
    "OWNS_ADMIN": "reaches",
    "DEPLOYED_BY": "deployed by",
    "APPROVES": "approves",
    "EXIT_VIA": "exits via",
    "SERVICE_FOR": "services",
}


def edge_label(e):
    return EDGE_PLAIN.get(e, e or "")


NODE_W, GAP, PAD = 176, 112, 26      # spine box width, column gap, canvas padding
BR_W, BR_IND, BR_GAP = 176, 12, 12   # branch box width, indent from spine, vertical gap
BR_LBL_PX, BR_SUB_PX = 11.0, 9.5     # branch fonts, must match .nb in the CSS
BR_LBL_LH, BR_SUB_LH = 13.0, 11.5
LBL_PX, SUB_PX = 12.0, 10.5          # font sizes
LBL_LH, SUB_LH = 14.0, 12.5          # line heights
BOX_PADX, BOX_PADY = 10.0, 11.0

# SVG carries no font metrics, so line breaking is done against an estimated
# advance width. 0.58em is a safe average for mixed-case sans-serif text at
# these sizes: it over-estimates slightly, which errs toward breaking a line
# early rather than letting it run past the edge of its box.
EM = 0.58


def _fit(text, box_w, px, max_lines):
    """Break text to fit box_w at font size px. Returns (lines, overflowed)."""
    avail = box_w - BOX_PADX * 2
    per = max(int(avail / (px * EM)), 4)
    words, lines, cur = (text or "").split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if len(t) <= per:
            cur = t
            continue
        if cur:
            lines.append(cur)
        if len(lines) == max_lines:
            cur = ""
            break
        # a single word longer than the line gets hard-split rather than
        # pushed past the box edge
        while len(w) > per:
            lines.append(w[:per - 1] + "‑")
            w = w[per - 1:]
            if len(lines) == max_lines:
                return lines, True
        cur = w
    if cur and len(lines) < max_lines:
        lines.append(cur)
    used = sum(len(x) for x in lines) + max(len(lines) - 1, 0)
    over = used < len((text or "").strip())
    if over and lines:
        last = lines[-1]
        lines[-1] = (last[:per - 1].rstrip() + "…") if len(last) >= per else last + "…"
    return (lines or [""]), over


def _measure(label, sub, w, lbl_lines=3, sub_lines=2, branch=False):
    """Fit a box's text. The font sizes here MUST track .nb in the CSS: measuring
    at one size and rendering at another is what puts text outside its box."""
    lp, sp = (BR_LBL_PX, BR_SUB_PX) if branch else (LBL_PX, SUB_PX)
    llh, slh = (BR_LBL_LH, BR_SUB_LH) if branch else (LBL_LH, SUB_LH)
    ls, _ = _fit(label, w, lp, lbl_lines)
    ss, _ = _fit(sub, w, sp, sub_lines) if sub else ([], False)
    h = BOX_PADY * 2 + len(ls) * llh + (len(ss) * slh + 3 if ss else 0)
    return ls, ss, h, (lp, sp, llh, slh)


def _box(x, y, w, h, ls, ss, cls, fonts=None):
    lp, sp, LBL_LH_, SUB_LH_ = fonts or (LBL_PX, SUB_PX, LBL_LH, SUB_LH)
    g = ['<g class="%s">' % cls]
    g.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="8"/>' % (x, y, w, h))
    # the text block is centred vertically inside the box
    blk = len(ls) * LBL_LH_ + (len(ss) * SUB_LH_ + 3 if ss else 0)
    ty = y + (h - blk) / 2 + lp
    cx = x + w / 2
    for ln in ls:
        g.append('<text class="nl" x="%.1f" y="%.1f">%s</text>' % (cx, ty, escape(ln)))
        ty += LBL_LH_
    if ss:
        ty += 3
        for ln in ss:
            g.append('<text class="ns" x="%.1f" y="%.1f">%s</text>' % (cx, ty, escape(ln)))
            ty += SUB_LH_
    g.append("</g>")
    return "".join(g)


def _arrow(x1, x2, y, label, sub):
    span = x2 - x1
    g = ['<g class="ar">']
    g.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" marker-end="url(#ah)"/>'
             % (x1, y, x2 - 9, y))
    mid = (x1 + x2) / 2
    # edge labels sit in the gap between two boxes, so they are fitted to the
    # gap rather than to a box: an unfitted label here is what runs under the
    # neighbouring node.
    if label:
        ls, _ = _fit(label, span + BOX_PADX * 2, 10.0, 2)
        ty = y - 9 - (len(ls) - 1) * 11
        for ln in ls:
            g.append('<text class="el" x="%.1f" y="%.1f">%s</text>' % (mid, ty, escape(ln)))
            ty += 11
    if sub:
        ls, _ = _fit(sub, span + BOX_PADX * 2, 10.0, 2)
        ty = y + 16
        for ln in ls:
            g.append('<text class="es" x="%.1f" y="%.1f">%s</text>' % (mid, ty, escape(ln)))
            ty += 11
    g.append("</g>")
    return "".join(g)


def render_path_diagram(f):
    """One spine per path: portfolio on the left, the asset that fails on the right."""
    paths = f.get("paths") or []
    if not paths:
        return ""
    t = (f.get("target") or {}).get("name") or "target"
    inter = f.get("intermediaries") or []

    rows = []
    for p in paths:
        chain = p.get("chain") or []
        spine = [{"label": t, "sub": money(f.get("target", {}).get("assets")), "cls": "n0"}]
        edges = []
        for st in chain:
            spine.append({"label": st.get("to_label") or st.get("to") or "?",
                          "sub": st.get("sub") or "", "cls": "nm"})
            frac = st.get("fraction")
            sub = st.get("edge_sub")
            if sub is None:
                sub = (("%.2f%% of assets" % (float(frac) * 100))
                       if frac not in (None, "", 1.0)
                       else ("100%" if frac == 1.0 else ""))
            edges.append((edge_label(st.get("edge")), sub))
        if len(spine) > 1:
            spine[-1]["cls"] = "nx"
        br = {}
        for i in inter:
            if p.get("id") not in (i.get("paths") or []):
                continue
            a, idx = i.get("attach"), None
            for k, sp in enumerate(spine):
                if a and a == sp["label"]:
                    idx = k
                    break
            if idx is None:
                idx = len(spine) - 1
            br.setdefault(idx, []).append(i)
        # every spine box in a row shares the tallest height so the arrows
        # between them stay on one line
        meas = [_measure(sp["label"], sp["sub"], NODE_W) for sp in spine]
        nh = max(m[2] for m in meas)
        rows.append((spine, edges, br, meas, nh))

    # canvas width must account for branch boxes hanging off the LAST column,
    # which extend past the spine and would otherwise be clipped
    right = 0.0
    for spine, edges, br, meas, nh in rows:
        for k in range(len(spine)):
            x = PAD + k * (NODE_W + GAP)
            right = max(right, x + NODE_W)
            if br.get(k):
                right = max(right, x + NODE_W / 2 + BR_IND + BR_W)
    W = right + PAD

    parts, y = [], PAD
    for spine, edges, br, meas, nh in rows:
        row_bottom = y + nh
        for k, sp in enumerate(spine):
            x = PAD + k * (NODE_W + GAP)
            ls, ss, _, fnt = meas[k]
            parts.append(_box(x, y, NODE_W, nh, ls, ss, sp["cls"], fnt))
            if k < len(edges):
                parts.append(_arrow(x + NODE_W, x + NODE_W + GAP, y + nh / 2,
                                    edges[k][0], edges[k][1]))
            by = y + nh + BR_GAP
            for i in br.get(k, []):
                bls, bss, bh, bfnt = _measure(i.get("label") or "", i.get("what") or "",
                                              BR_W, 2, 2, branch=True)
                sx = x + NODE_W / 2
                parts.append('<path class="brl" d="M%.1f %.1f V%.1f H%.1f"/>'
                             % (sx, y + nh, by + bh / 2, sx + BR_IND))
                parts.append(_box(sx + BR_IND, by, BR_W, bh, bls, bss, "nb", bfnt))
                by += bh + BR_GAP
                row_bottom = max(row_bottom, by - BR_GAP)
        y = row_bottom + 34
    H = y - 34 + PAD

    return ('<div class="diag"><svg viewBox="0 0 %.0f %.0f" width="100%%" '
            'preserveAspectRatio="xMinYMin meet" role="img" '
            'aria-label="path from the portfolio to the asset that fails">'
            '<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" '
            'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            '<path d="M0,0 L10,5 L0,10 z"/></marker></defs>%s</svg></div>'
            % (W, H, "".join(parts)))


# -------------------------------------------------- defensive borrowing model
# When an asset is known to be heading to zero, the rational move for anyone
# holding it is to borrow against it while the oracle still prices it, walk
# away with the loan and abandon the collateral. That converts the market's
# remaining liquidity into bad debt on top of whatever was already drawn, so
# the loss is not current utilisation, it is everything the borrowers can
# reach. Three things bound how much extra they can draw, and the smallest of
# the three is the one that binds:
#
#   collateral headroom = posted collateral x LLTV - debt already drawn
#   available liquidity = what is actually sitting in the market to lend
#   cap headroom        = borrow cap - debt already drawn, where a cap exists
#
# On a market with no cap (Morpho Blue markets carry none) the cap term drops
# out, and liquidity almost always binds because collateral is posted well
# above the drawn amount. The result is that a sole-supplier isolated market
# loses its whole position rather than its utilised fraction.

INF = float("inf")


def _room(v):
    return "no limit" if v == INF else money(v)


def defensive_borrow(d):
    """Return (extra_usd, binding_label, terms) for one market's rush scenario.

    terms is a list of (name, value, basis). A value of INF means that
    constraint does not bind at all, which is a real and reportable state: a
    market with no borrow cap places no limit on the draw, and an asset whose
    minter is compromised places no limit on the collateral.
    """
    if not d:
        return 0.0, "", []
    debt = float(d.get("existing_debt_usd") or 0)
    # The debt SECURED BY the collateral asset is not always the loss booked on
    # this one path: one collateral family can back debt drawn across several
    # reserves. collateral_debt_usd carries that figure when they differ; the
    # loss base stays existing_debt_usd.
    cdebt = d.get("collateral_debt_usd")
    cdebt = debt if cdebt is None else float(cdebt)
    col = d.get("collateral_usd")
    lltv = d.get("lltv_pct")
    newc = d.get("new_collateral")            # None | "unbounded" | usd number
    ccap = d.get("collateral_supply_cap_usd")  # cap on TOTAL collateral posted
    terms, collateral_room = [], None

    if col is not None and lltv is not None:
        lt = float(lltv) / 100.0
        posted = float(col)
        floor = max(posted * lt - cdebt, 0.0)
        terms.append(("collateral already posted", floor,
                      "%s posted x %.0f%% LLTV, less %s already drawn against it"
                      % (money(posted), float(lltv), money(cdebt))))
        collateral_room = floor
        if newc is not None:
            if ccap is not None:
                postable = max(float(ccap) - posted, 0.0)
                room = max((posted + postable) * lt - cdebt, 0.0)
                why = ("collateral is capped at %s in total, so at most %s more can "
                       "be posted" % (money(ccap), money(postable)))
            elif newc == "unbounded":
                if lt <= 0:
                    room = 0.0
                    why = ("the asset carries a zero LLTV, so posting more of it buys no "
                           "borrowing power however much is posted")
                else:
                    room, why = INF, ("nothing caps how much collateral can be posted, so "
                                      "this constraint does not bind")
            else:
                room = max((posted + float(newc)) * lt - cdebt, 0.0)
                why = (d.get("collateral_basis")
                       or "assumes a further %s of collateral is posted" % money(newc))
            terms.append(("collateral that could still be posted", room, why))
            collateral_room = room

    liq = d.get("available_liquidity_usd")
    if liq is not None:
        terms.append(("available liquidity", max(float(liq), 0.0),
                      d.get("liquidity_basis")
                      or "what is actually left in the market to lend"))

    cap = d.get("borrow_cap_usd")
    head = d.get("borrow_cap_headroom_usd")
    if head is not None:
        # Some protocols cap per reserve, so the headroom a draw actually faces is
        # a sum over the reserves it can reach, not one cap minus one debt.
        terms.append(("borrow cap headroom", max(float(head), 0.0),
                      d.get("borrow_cap_basis")
                      or "cap headroom summed over the reserves this draw can reach"))
    elif cap == "unknown":
        terms.append(("borrow cap headroom", INF,
                      "a borrow cap is not readable here, so this constraint is "
                      "unquantified rather than absent, and the draw below is an "
                      "upper bound on what the caps would actually permit"))
    else:
        terms.append(("borrow cap headroom",
                      max(float(cap) - debt, 0.0) if cap is not None else INF,
                      ("%s borrow cap, less %s already drawn" % (money(cap), money(debt)))
                      if cap is not None
                      else "this market has no borrow cap, so nothing caps the draw here"))

    # the floor row is context, not a constraint, once new collateral is admitted
    binding_pool = [t for t in terms if t[0] != "collateral already posted"] \
        if collateral_room is not None and len(
            [t for t in terms if t[0].startswith("collateral")]) > 1 else terms
    live = [t for t in binding_pool if t[1] < INF]
    if not live:
        return 0.0, "", terms
    win = min(live, key=lambda x: x[1])
    return win[1], win[0], terms


def render_defensive(f, c):
    _, kept = _rush_paths(f, c["keys"], c.get("offvector_ids"))
    paths = [p for p in kept if p.get("defensive_borrowing")]
    paths.sort(key=lambda p: -_rush_loss(p))
    if not paths:
        return ""
    h = ['<h2>2. Defensive borrowing: what the loss becomes in a rush</h2>']
    h.append('<p class="note">An asset known to be failing keeps its old price until the '
             'market oracle catches up. In that window the rational move for anyone '
             'holding it is to post it as collateral, borrow whatever the market will '
             'lend, and abandon the position. The borrowed asset is real and it leaves; '
             'the collateral left behind is worthless. Four things bound how much can be '
             'drawn, and the smallest of them is the one that binds.</p>')
    h.append("<table><thead><tr><th>Market</th><th>Constraint</th>"
             '<th class="n">Room to borrow</th><th>Basis</th></tr></thead><tbody>')
    tot_base = tot_extra = 0.0
    for p in paths:
        d = p["defensive_borrowing"]
        extra, binding, terms = defensive_borrow(d)
        base = float(d.get("existing_debt_usd") or 0)
        drawn = d.get("collateral_debt_usd")
        drawn = base if drawn is None else float(drawn)
        tot_base += base
        tot_extra += extra
        h.append('<tr><td rowspan="%d"><b>%s</b><div class="note">already drawn %s</div>'
                 "</td>" % (len(terms) + 1, escape(p.get("position_label")
                                                   or p.get("id") or ""), money(drawn)))
        for k, (nm, val, why) in enumerate(terms):
            b = nm == binding
            cell = ('<td>%s%s</td><td class="n">%s</td><td class="note">%s</td></tr>'
                    % (("<b>%s</b>" % escape(nm)) if b else escape(nm),
                       ' <span class="tag u">binds</span>' if b else "",
                       _room(val), escape(why)))
            h.append(cell if k == 0 else "<tr>" + cell)
        h.append('<tr class="sum"><td>Extra borrowing this market absorbs</td>'
                 '<td class="n">%s</td>'
                 '<td class="note">the smallest of the constraints above</td></tr>'
                 % money(extra))
    # The footer must agree with the headline cards by construction. Summing
    # existing_debt_usd over the defensive rows only does NOT: a position whose
    # loss arrives by some other mechanism, or one that wins its key without a
    # defensive block, is in the headline and not in that sum, so the two
    # figures drift apart and the report states its own loss twice, differently.
    base = float(c.get("total_loss") or tot_base)
    rush = float(c.get("rush_loss") or (base + tot_extra))
    h.append('</tbody><tfoot><tr><td>Current estimated loss</td>'
             '<td colspan="2" class="n">%s</td>'
             '<td class="note">every path in section 1, at the amounts borrowed today</td></tr>'
             '<tr><td>Extra borrowing under a rush</td><td colspan="2" class="n">%s</td>'
             '<td class="note">added to the loss</td></tr>'
             '<tr><td><b>Maximum loss</b></td>'
             '<td colspan="2" class="n"><b>%s</b></td>'
             '<td class="note">%s of the portfolio</td></tr></tfoot></table>'
             % (money(base), money(rush - base), money(rush),
                pct(rush / c["assets"] * 100 if c["assets"] else 0)))
    return "".join(h)


def render_unconnected(f, c):
    """Positions the traversal reached and could NOT connect to the subject.

    These dollars are in the portfolio total and in the coverage check, so
    without this table the reader sees an aggregate in one sentence and cannot
    tell a position with a stated reason apart from one that was never walked.
    A no-route row is a finding, and the reason is the finding.
    """
    rows = f.get("unconnected") or []
    if not rows:
        return ""
    s = f.get("subject") or {}
    h = ['<h3>Positions with no route to %s</h3>' % escape(s.get("name") or "the subject")]
    h.append('<table><thead><tr><th>Position</th>'
             '<th class="n">Held</th><th class="n">% assets</th></tr></thead><tbody>')
    tot = 0.0
    for r in sorted(rows, key=lambda r: -float(r.get("usd") or 0)):
        v = float(r.get("usd") or 0)
        tot += v
        h.append('<tr><td><b>%s</b></td><td class="n">%s</td><td class="n">%s</td></tr>'
                 % (escape(r.get("position") or ""), money(v),
                    pct(v / c["assets"] * 100 if c["assets"] else 0)))
        if r.get("why"):
            h.append('<tr class="basis"><td class="note" colspan="3">%s</td></tr>'
                     % escape(r["why"]))
    h.append('</tbody><tfoot><tr><td>Total</td><td class="n">%s</td>'
             '<td class="n">%s</td></tr></tfoot></table>'
             % (money(tot), pct(tot / c["assets"] * 100 if c["assets"] else 0)))
    return "".join(h)


def render_incidence(f, c, n=2):
    """Who actually bears the loss.

    A path table says where the money sits and what it is worth after the
    failure. On a pooled cross-collateral protocol that is not the same question
    as who is out of pocket: a levered depositor abandons a position rather than
    repaying it, and the shortfall lands on people who never touched the failing
    asset. Those dollars are a REDISTRIBUTION of the path total, never an
    addition to it, so this section is checked against the path total and never
    summed with it.
    """
    inc = f.get("incidence")
    if not inc:
        return ""
    rows = inc.get("bearers") or []
    if not rows:
        return ""
    # The rush column is optional: it only means anything when a defensive
    # scenario was sized, and a bearer that the extra draw cannot reach keeps
    # its current figure rather than showing a blank.
    has_max = any(r.get("max_usd") is not None for r in rows)
    h = ['<h2>%d. %s</h2>' % (n, escape(inc.get("title") or "Who bears the loss"))]
    if inc.get("note"):
        h.append('<p class="note">%s</p>' % escape(inc["note"]))
    # Each loss is shown against the assets of the party that bears it, never
    # against the portfolio: a $360k hit is noise to WETH suppliers and 7.7% of
    # the RLUSD reserve. The asset base sits under the bearer's name rather than
    # in its own column, so no column here is a division of two others.
    ncol = 6 if has_max else 4
    h.append('<table><thead><tr><th>Bears the loss</th><th>How it reaches them</th>'
             '<th class="n">Current loss</th><th class="n">of its assets</th>'
             + ('<th class="n">Maximum loss</th><th class="n">of its assets</th>'
                if has_max else '')
             + '</tr></thead><tbody>')
    tot = sum(float(r.get("usd") or 0) for r in rows)
    tmax = sum(float(r.get("max_usd") if r.get("max_usd") is not None else r.get("usd") or 0)
               for r in rows)
    def _share(num, den):
        if not den:
            return "&mdash;"
        p = num / float(den) * 100.0
        # a sub-basis-point hit reads as 0.00% and looks like a missing figure
        return "&lt;0.01%" if 0 < p < 0.005 else pct(p)
    for r in sorted(rows, key=lambda r: -float(r.get("max_usd")
                                               if r.get("max_usd") is not None
                                               else r.get("usd") or 0)):
        v = float(r.get("usd") or 0)
        mv = float(r.get("max_usd") if r.get("max_usd") is not None else v)
        den = r.get("denominator_usd")
        cells = ('<td class="n">%s</td><td class="n">%s</td>'
                 % (money(v), _share(v, den)))
        if has_max:
            grew = mv > v * 1.0001
            cells += ('<td class="n">%s%s</td><td class="n">%s</td>'
                      % (money(mv), ' <span class="tag u">drained</span>' if grew else '',
                         _share(mv, den)))
        sub = ('<div class="note">%s %s</div>'
               % (escape(r.get("denominator_label") or "its assets"), money(den))) if den else ''
        h.append('<tr><td><b>%s</b>%s</td><td>%s</td>%s</tr>'
                 % (escape(r.get("who") or ""), sub, escape(r.get("how") or ""), cells))
        if r.get("basis"):
            h.append('<tr class="basis"><td></td><td class="note" colspan="%d">%s</td></tr>'
                     % (ncol - 1, escape(r["basis"])))
    h.append('</tbody><tfoot><tr><td colspan="2">Total</td><td class="n">%s</td>'
             '<td class="n">&mdash;</td>' % money(tot))
    if has_max:
        h.append('<td class="n">%s</td><td class="n">&mdash;</td>' % money(tmax))
    h.append('</tr></tfoot></table>')
    tl = float(c.get("total_loss") or 0)
    rl = float(c.get("rush_loss") or 0)
    drift = abs(tot - tl) / tl * 100 if tl else 0
    extra = ""
    if has_max and rl:
        d2 = abs(tmax - rl) / rl * 100
        drift = max(drift, d2)
        extra = (" The maximum column accounts for %s against the %s headline, %.2f%% apart."
                 % (money(tmax), money(rl), d2))
    h.append('<div class="%s"><b>Incidence check.</b> The bearers above account for %s '
             'against the %s of loss in section 1, %.2f%% apart.%s These are the same '
             'dollars seen by who loses them, so they are never added to the path '
             'total.%s</div>'
             % ("note" if drift <= 1.0 else "warn", money(tot), money(tl), drift, extra,
                "" if drift <= 1.0 else " <b>This does not close.</b>"))
    return "".join(h)


def render_common_cause(f, c, n):
    rows = f.get("common_cause") or []
    if not rows:
        return ""
    h = ['<h2>%d. Common cause: what a shared issuer or admin would add</h2>' % n]
    h.append('<p class="note">These assets do not depend on the subject and are held out of '
             'every figure above. They are here because one compromise upstream of both would '
             'reach them too, which is a different assumption from the one this report makes '
             'and produces a different number. Never added to the path total.</p>')
    h.append('<table><thead><tr><th>Shared point</th><th>Also reaches</th>'
             '<th class="n">Held in portfolio</th></tr></thead><tbody>')
    for r in rows:
        h.append('<tr><td><b>%s</b><div class="note mono">%s</div></td><td>%s</td>'
                 '<td class="n">%s</td></tr>'
                 % (escape(r.get("label") or ""), addr(r.get("node")),
                    escape(", ".join(r.get("shared_with") or [])),
                    money(r.get("combined_usd"))))
        if r.get("note"):
            h.append('<tr class="basis"><td></td><td class="note" colspan="2">%s</td></tr>'
                     % escape(r["note"]))
    h.append("</tbody></table>")
    return "".join(h)


def render_accounts(f, c, n):
    """Named-address impact.

    A blast table sizes the portfolio. This sizes individual counterparties
    inside it, which is a different question: a levered account's OWN loss is
    its equity, not its collateral, because it abandons the position rather
    than repaying. The two figures that matter per account are therefore what
    IT loses and what it leaves behind for everyone else, and they must be in
    separate columns because they land on different people.
    """
    a = f.get("accounts")
    if not a:
        return ""
    rows = a.get("entries") or []
    if not rows:
        return ""
    h = ['<h2>%d. %s</h2>' % (n, escape(a.get("title") or "Impact on named addresses"))]
    if a.get("note"):
        h.append('<p class="note">%s</p>' % escape(a["note"]))
    NC = 6
    h.append('<table><thead><tr><th>Address</th><th class="n">Exposed collateral</th>'
             '<th class="n">Surviving collateral</th><th class="n">Debt abandoned</th>'
             '<th class="n">Loss to the account</th>'
             '<th class="n">Bad debt it leaves behind</th></tr></thead><tbody>')
    t_exp = t_surv = t_debt = t_own = t_bad = 0.0
    for r in sorted(rows, key=lambda r: -float(r.get("bad_debt_usd") or 0)):
        exp = float(r.get("exposed_collateral_usd") or 0)
        surv = float(r.get("surviving_collateral_usd") or 0)
        debt = float(r.get("debt_usd") or 0)
        own = float(r.get("own_loss_usd") or 0)
        bad = float(r.get("bad_debt_usd") or 0)
        t_exp += exp; t_surv += surv; t_debt += debt; t_own += own; t_bad += bad
        h.append('<tr><td><b>%s</b><div class="note mono">%s</div>'
                 '<div class="note">%s</div></td>'
                 '<td class="n">%s</td><td class="n">%s</td><td class="n">%s</td>'
                 '<td class="n">%s</td><td class="n">%s</td></tr>'
                 % (escape(r.get("label") or ""), addr(r.get("id")),
                    escape(r.get("what") or ""),
                    money(exp), money(surv), money(debt), money(own), money(bad)))
        if r.get("basis"):
            h.append('<tr class="basis"><td></td><td class="note" colspan="%d">%s</td></tr>'
                     % (NC - 1, escape(r["basis"])))
    h.append('<tr class="sum"><td>Total</td><td class="n">%s</td><td class="n">%s</td>'
             '<td class="n">%s</td><td class="n">%s</td><td class="n">%s</td></tr>'
             % (money(t_exp), money(t_surv), money(t_debt), money(t_own), money(t_bad)))
    h.append("</tbody></table>")
    if a.get("footnote"):
        h.append('<div class="note">%s</div>' % escape(a["footnote"]))
    return "".join(h)


def render_targeted(f, c, checks):
    s = f.get("subject") or {}
    t = f.get("target") or {}
    h = []
    h.append('<h1>Impact of %s on %s</h1>' % (escape(s.get("name") or "?"),
                                              escape(t.get("name") or "?")))
    h.append('<p class="meta"><b>Portfolio analysed</b> %s &middot; %s '
             '&middot; total assets <b>%s</b> &middot; <span class="mono">%s</span></p>'
             % (escape(t.get("name") or ""), escape(t.get("type") or ""),
                exact(c["assets"]), addr(t.get("address"))))
    h.append('<p class="meta"><b>What could fail</b> %s &middot; <span class="mono">%s'
             '</span></p>' % (escape(s.get("name") or ""), addr(s.get("address"))))
    h.append('<p class="meta"><b>Failure assumed</b> %s</p>'
             % escape(s.get("vector") or "not stated"))

    h.append('<div class="heads">')
    h.append('<div class="head%s"><div class="k">Current estimated loss</div>'
             '<div class="v">%s</div><div class="s">%s of the portfolio &middot; '
             'at the amounts borrowed today</div></div>'
             % ("" if c.get("rush_loss") else " hot",
                money(c["total_loss"]), pct(c["loss_pct"])))
    if c.get("rush_loss"):
        h.append('<div class="head hot"><div class="k">Maximum loss</div>'
                 '<div class="v">%s</div><div class="s">%s of the portfolio &middot; '
                 'once borrowers draw everything they can reach</div></div>'
                 % (money(c["rush_loss"]),
                    pct(c["rush_loss"] / c["assets"] * 100 if c["assets"] else 0)))
    if c["total_ceiling"]:
        h.append('<div class="head"><div class="k">Additional ceiling</div><div class="v">%s</div>'
                 '<div class="s">reported, not measured</div></div>' % money(c["total_ceiling"]))
    if c["bounded_upper"]:
        h.append('<div class="head"><div class="k">Unresolved upper bound</div>'
                 '<div class="v">%s</div><div class="s">breakdown unavailable</div></div>'
                 % money(c["bounded_upper"]))
    h.append('</div>')

    h.append("<h2>1. Impact and contagion paths</h2>")
    h.append('<p class="note">How %s reaches %s, and every contract in between.</p>'
             % (escape(s.get("name") or "the asset"), escape(t.get("name") or "")))
    h.append(render_path_diagram(f))

    # 1. paths
    h.append("<h3>Path detail</h3>")
    paths = sorted(f.get("paths") or [], key=lambda p: -float(p.get("loss_usd") or 0))
    # rows are numbered 1..n in rank order; the internal path id stays out of the
    # report because "P1" reads as a priority rather than a row number
    num = {p.get("id"): i + 1 for i, p in enumerate(paths)}
    NCOL = 7
    h.append("<table><thead><tr><th>#</th><th>Path</th><th>Mechanism</th>"
             '<th class="n">Position</th><th class="n">Exposure</th>'
             '<th class="n">Loss</th><th class="n">% assets</th></tr></thead><tbody>')
    for p in paths:
        key = p.get("position_key")
        kd = c["keys"].get(key) or {}
        dropped = kd.get("winner") and kd["winner"] != p.get("id")
        tags = ""
        if p.get("ceiling"):
            tags += ' <span class="tag c">ceiling</span>'
        if p.get("unresolved"):
            tags += ' <span class="tag u">unresolved</span>'
        if dropped:
            tags += (' <span class="tag">same dollars as row %s</span>'
                     % escape(str(num.get(kd["winner"], kd["winner"]))))
        loss = float(p.get("loss_usd") or 0)
        # the path is named for where the money sits, not spelled out as a chain of
        # edge types: the diagram above already carries the route
        label = (p.get("position_label")
                 or (p.get("chain") or [{}])[-1].get("to_label") or p.get("id") or "")
        h.append("<tr><td><b>%d</b>%s</td><td>%s</td><td>%s</td>"
                 '<td class="n">%s</td><td class="n">%s</td><td class="n">%s</td>'
                 '<td class="n">%s</td></tr>'
                 % (num.get(p.get("id"), 0), tags, escape(label),
                    escape(MECHANISMS.get(p.get("mechanism"), p.get("mechanism") or "")),
                    money(p.get("position_usd")), money(p.get("exposure_usd")),
                    "&mdash;" if dropped else money(loss),
                    "&mdash;" if dropped else pct(loss / c["assets"] * 100 if c["assets"] else 0)))
        # qualifying text gets the full table width rather than one narrow column
        if p.get("loss_basis"):
            h.append('<tr class="basis"><td></td><td class="note" colspan="%d">%s</td></tr>'
                     % (NCOL - 1, escape(p["loss_basis"])))
    h.append('</tbody><tfoot><tr><td colspan="4">Total</td>'
             '<td class="n">%s</td><td class="n">%s</td><td class="n">%s</td></tr></tfoot></table>'
             % (money(c["total_exposure"]), money(c["total_loss"]), pct(c["loss_pct"])))

    h.append(render_unconnected(f, c))

    # 2. the rush, 3. who bears it, then the optional tail sections. Sections are
    # numbered by what is actually present, so deleting one does not leave a gap.
    nxt = 2
    d = render_defensive(f, c)
    if d:
        h.append(d); nxt += 1
    if (f.get("incidence") or {}).get("bearers"):
        h.append(render_incidence(f, c, nxt)); nxt += 1
    if f.get("common_cause"):
        h.append(render_common_cause(f, c, nxt)); nxt += 1
    if (f.get("accounts") or {}).get("entries"):
        h.append(render_accounts(f, c, nxt)); nxt += 1

    # arithmetic check, kept as one line rather than a section
    drift = (abs(c["reconciled"] - c["assets"]) / c["assets"] * 100) if c["assets"] else 0
    cls = "note" if drift <= 1.0 else "warn"
    h.append('<div class="%s"><b>Coverage check.</b> %s reaches %s through the paths above, '
             'and %s has no route to it. Those two account for %s of the portfolio\'s %s, '
             'leaving %.2f%% unexplained. This is here so a traversal that quietly missed '
             'something cannot look like one that found everything.%s</div>'
             % (cls, money(c["resolved_positions"]), escape(s.get("name") or "the asset"),
                money(c["unconnected"]),
                money(c["reconciled"]), money(c["assets"]), drift,
                "" if drift <= 1.0 else " <b>This does not close.</b> A headline number "
                "without a closing reconciliation cannot be told apart from a truncated "
                "traversal."))
    return "".join(h)


def render_wide(f, c):
    s = f.get("subject") or {}
    den = f.get("denominator") or {}
    h = []
    h.append("<h1>%s reverse impact analysis</h1>" % escape(s.get("name") or "?"))
    h.append('<p class="meta"><b>Subject</b> <span class="mono">%s</span> &middot; %s '
             '&middot; %s &middot; partition <span class="mono">%s</span></p>'
             % (addr(s.get("address")), escape(s.get("chain") or ""),
                escape(s.get("snapshot") or ""),
                escape(s.get("partition") or "risk-graph-rt-v3")))
    h.append('<p class="meta"><b>Denominator</b> %s %s &middot; graph coverage %s (%s%%)</p>'
             % (escape(den.get("name") or ""), exact(den.get("value")),
                money(den.get("coverage_usd")), den.get("coverage_pct")))
    h.append('<div class="heads">')
    h.append('<div class="head hot"><div class="k">Measured blast radius</div>'
             '<div class="v">%s</div><div class="s">%s of the denominator</div></div>'
             % (money(c["impacts"]), pct(c["impacts_pct"])))
    h.append('<div class="head"><div class="k">Additive, non-subject assets</div>'
             '<div class="v">%s</div><div class="s">new dollars, reported apart</div></div>'
             % money(c["additive"]))
    h.append('<div class="head"><div class="k">Re-attribution</div><div class="v">%s</div>'
             '<div class="s">same dollars &middot; do NOT add</div></div>'
             % money(c["reattribution"]))
    h.append("</div>")

    fams = {}
    for i in f.get("impacts") or []:
        fams.setdefault(i.get("family") or "Other", []).append(i)
    h.append("<h2>1. Ranked impact</h2>")
    h.append("<table><thead><tr><th>Impact family / node</th><th>Layer</th>"
             '<th class="n">Hops</th><th class="n">Impact</th><th class="n">% subject</th>'
             "</tr></thead><tbody>")
    for fam, rows in sorted(fams.items(), key=lambda kv: -sum(float(r.get("usd") or 0)
                                                              for r in kv[1])):
        sub = sum(float(r.get("usd") or 0) for r in rows)
        h.append('<tr><td colspan="3"><b>%s</b> <span class="tag">union of %d</span></td>'
                 '<td class="n"><b>%s</b></td><td class="n"><b>%s</b></td></tr>'
                 % (escape(fam), len(rows), money(sub),
                    pct(sub / c["denominator"] * 100 if c["denominator"] else 0)))
        for r in sorted(rows, key=lambda r: -float(r.get("usd") or 0)):
            u = float(r.get("usd") or 0)
            h.append("<tr><td>&nbsp;&nbsp;&nbsp;%s%s</td><td>%s</td>"
                     '<td class="n">%s</td><td class="n">%s</td><td class="n">%s</td></tr>'
                     % (escape(r.get("node") or ""),
                        ('<div class="note">%s</div>' % escape(r["note"])) if r.get("note") else "",
                        escape(r.get("layer") or ""), r.get("hops", ""), money(u),
                        pct(u / c["denominator"] * 100 if c["denominator"] else 0)))
    h.append("</tbody></table>")
    h.append('<p class="note">Family subtotals are unions of the positions touched, not '
             'sums of the member impacts. Do not try to reconcile the arithmetic row by row.</p>')

    if f.get("additive"):
        h.append("<h2>2. Additive impact on non-subject assets</h2>")
        h.append("<table><thead><tr><th>Path</th><th>Layer</th><th class=\"n\">Hops</th>"
                 '<th class="n">Impact</th><th>Note</th></tr></thead><tbody>')
        for r in sorted(f["additive"], key=lambda r: -float(r.get("usd") or 0)):
            h.append("<tr><td>%s</td><td>%s</td><td class=\"n\">%s</td>"
                     '<td class="n">%s</td><td class="note">%s</td></tr>'
                     % (escape(r.get("path") or ""), escape(r.get("layer") or ""),
                        r.get("hops", ""), money(r.get("usd")), escape(r.get("note") or "")))
        h.append("</tbody></table>")
        h.append('<p class="note">These are new dollars, so the total legitimately exceeds '
                 "the subject's own size.</p>")

    if f.get("reattribution"):
        h.append("<h2>3. Re-attribution layers (do NOT add)</h2>")
        h.append("<table><thead><tr><th>Layer</th><th>Resolves</th>"
                 '<th class="n">Value</th></tr></thead><tbody>')
        for r in f["reattribution"]:
            h.append('<tr><td>%s</td><td>%s</td><td class="n">%s</td></tr>'
                     % (escape(r.get("layer") or ""), escape(r.get("resolves") or ""),
                        money(r.get("usd"))))
        h.append("</tbody></table>")

    if f.get("backing"):
        h.append("<h2>4. Backing exposure</h2>")
        h.append("<table><thead><tr><th>Asset</th><th>Mechanism</th>"
                 '<th class="n">Subject share of backing</th><th>Basis</th>'
                 "</tr></thead><tbody>")
        for r in sorted(f["backing"], key=lambda r: -float(r.get("share_pct") or 0)):
            h.append('<tr><td>%s</td><td>%s</td><td class="n">%.3f%%</td>'
                     '<td class="note">%s</td></tr>'
                     % (escape(r.get("asset") or ""), escape(r.get("mechanism") or ""),
                        float(r.get("share_pct") or 0), escape(r.get("basis") or "")))
        h.append("</tbody></table>")
    return "".join(h)


def render(f, c, checks):
    mode = f.get("mode")
    body = render_targeted(f, c, checks) if mode == "targeted" else render_wide(f, c)
    tail = []
    s_ = f.get("subject") or {}
    prov = " &middot; ".join(x for x in [
        escape(s_.get("chain") or ""), escape(s_.get("snapshot") or ""),
        "Forta Risk Graph <span class=\"mono\">%s</span>"
        % escape(s_.get("partition") or "risk-graph-rt-v3"),
        ("reporting floor %s" % escape(f.get("floor"))) if f.get("floor") else "",
    ] if x)
    tail.append('<div class="foot">Sizing assumes the stated failure happens in full: '
                "no probability weighting, no recovery assumption, not expected loss. "
                "The graph is live and mutates between queries, so figures read minutes "
                "apart will not reconcile to the dollar.<br>%s</div>" % prov)
    title = ("Impact of %s on %s" % ((f.get("subject") or {}).get("name", "?"),
                                     (f.get("target") or {}).get("name", "?"))
             if mode == "targeted"
             else "%s reverse impact" % (f.get("subject") or {}).get("name", "?"))
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>%s</title><style>%s</style></head><body><div class=\"wrap\">%s%s"
            "</div></body></html>" % (escape(title), CSS, body, "".join(tail)))


def main():
    ap = argparse.ArgumentParser(description="Build a blast radius report from findings JSON.")
    ap.add_argument("findings", nargs="?")
    ap.add_argument("-o", "--out", default="report.html")
    ap.add_argument("--check", action="store_true",
                    help="run the arithmetic checks and print them, no HTML")
    ap.add_argument("--schema", action="store_true")
    a = ap.parse_args()

    if a.schema:
        print(SCHEMA)
        return 0
    if not a.findings:
        ap.error("findings JSON required (or use --schema)")

    f = json.load(open(a.findings))
    mode = f.get("mode")
    if mode not in ("targeted", "wide"):
        raise SystemExit('mode must be "targeted" or "wide"')

    if mode == "targeted":
        c = compute_targeted(f)
        notes, fatal = check_targeted(f, c)
    else:
        c = compute_wide(f)
        notes, fatal = check_wide(f, c)

    for n in notes:
        print("  " + n)
    for x in fatal:
        print("  FAIL: " + x, file=sys.stderr)
    if fatal:
        print("\n%d check(s) failed. Fix the findings, not the script." % len(fatal),
              file=sys.stderr)
        return 2
    if a.check:
        basis = ((f.get("target") or {}).get("denominator_basis")
                 if mode == "targeted" else (f.get("denominator") or {}).get("basis"))
        if basis:
            print("\n  denominator basis (not rendered in the report): " + basis)
        print("\nall checks passed")
        return 0

    open(a.out, "w").write(render(f, c, notes))
    print("\nwrote %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
