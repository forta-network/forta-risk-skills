#!/usr/bin/env python3
"""
Build a risk report (HTML) from a findings JSON file.

Renders two report shapes from one schema:
  * dependency concentration  - what a holder of value is exposed to
  * reverse impact            - who loses money if the subject is compromised

Why this exists: the analysis is the hard part. Re-deriving the layout, the union
arithmetic, and the collapsible grouping by hand every time wastes effort and
invites inconsistency between reports. Give this script the findings and it
handles impact math, ranking, banding, and rendering.

Usage:
    python3 build_report.py findings.json -o report.html
    python3 build_report.py --schema        # print the input schema and exit
    python3 build_report.py findings.json --check   # validate + print math, no HTML

Key behaviour you should understand before trusting the output:
  * Dependency impact  = sum of the positions it touches (or total assets if
    entity_wide is true).
  * Group impact       = UNION of its dependencies' positions, never the sum of
    their impacts. Overlapping dependencies would otherwise multi-count.
  * Percentages use total assets (nav) as the denominator so nothing exceeds 100%.
"""

import argparse
import json
import sys
from html import escape

SCHEMA = """
findings JSON schema
====================

{
  "entity": {
    "name":     "Example USDC Yield Vault",           # required
    "address":  "0xEXAMPLE...0001",                   # required
    "chain":    "Ethereum mainnet",
    "type":     "ERC-4626 vault",
    "snapshot": "24 Jul 2026",
    "mode":     "dependency"     # "dependency" | "impact". Sets the report label.
  },

  "totals": {
    "nav":      96670447,        # required. total assets = the denominator
    "deployed": 78352209,        # optional. value routed downstream
    "idle":     18318238         # optional. nav - deployed
  },

  # position key -> USD. A "position" is wherever value is deployed
  # (a market, a pool, a sub-vault). Keys are yours to choose; keep them short.
  "positions": {"mkt-a": 71069934.02, "mkt-b": 2614089.55},

  # optional "where the value sits" table
  "exposure": [
    {"name": "WBTC / USDC market", "key": "mkt-a", "collateral": "WBTC", "bucket": "BTC"}
  ],

  # optional bucket rollup (e.g. BTC vs ETH collateral)
  "buckets": [{"name": "BTC collateral", "keys": ["mkt-a", "mkt-f"]}],

  # the ranked dependency table, grouped by family
  "groups": [
    {
      "name": "WBTC",
      "note": "Collateral of the main market",       # optional one-liner
      "deps": [
        {
          "name":      "WBTC token",                # required
          "layer":     "Collateral",                # required
          "hops":      2,                           # required, distance from entity
          "positions": ["mkt-a", "mkt-f"],          # which positions it touches
          "addr":      "0x2260fa...c599",           # optional
          "entity_wide": false                      # true => impact = nav
        }
      ]
    }
  ],

  # optional. Per-market borrower concentration. Gives market LTV beside the
  # LLTV, so the headroom to liquidation is visible as the gap between two
  # stated numbers rather than as a third derived column.
  "borrower_concentration": [
    {"market": "Morpho kBTC/RLUSD", "collateral": "kBTC", "borrowers": 11,
     "market_debt_usd": 144757554, "market_ltv_pct": 59.13, "lltv_pct": 86.0,
     "top_borrower_pct": 67.71}
  ],

  "supplier_concentration": [
    {"market": "WBTC/USDC market mkt-a", "market_supply": 104024336,
     "our_supply": 71069934, "largest_other_pct": 8.5}
  ],

  # optional. Non-recourse adjustment on a levered entity. Renders a bridge
  # table: collateral consumed - loan extinguished = loss on the pledged leg,
  # plus the unpledged leg, the total, and the delta against gross.
  # The loss is DERIVED from the two inputs, so the table cannot drift from
  # the text. Name the counterparty that absorbs the difference.
  "encumbrance": {
    "asset":            "cbBTC",
    "collateral_usd":   54200000,   # pledged leg, gross
    "debt_usd":         39100000,   # borrowed against the basket
    "remaining_usd":    11400000,   # other collateral still seizable after the failure
    "unpledged_usd":    3100000,    # same asset held loose: 100% loss, no offset
    "liquidation_bonus": 10800,     # 1e4 scaled, 10800 = 8%. optional
    "absorbed_by":      "the lending protocol and its other suppliers"
  },

  # --- reverse impact reports only ---

  # additive dollars: bad debt, AMM counter-legs, anything denominated in
  # something other than the subject. These legitimately exceed market cap.
  "additive": [
    {"path": "Market X bad debt", "layer": "Lending", "hops": 2,
     "usd": 4100000, "note": "amount borrowed against the subject"}
  ],

  # the SAME dollars, better-named victims. Rendered under a do-not-add heading.
  "reattribution": [
    {"layer": "Receipt tokens", "usd": 61000000, "resolves": "holders of aTokens over the same deposits"}
  ],

  # subject's share of something else's backing, with the mechanism
  "backing_exposure": [
    {"asset": "A stablecoin", "mechanism": "CDP collateral", "share_pct": 0.282,
     "basis": "collateral-backed debt across all controllers"}
  ],

}

Notes
-----
* Put a dependency shared across families (a feed serving two collateral types,
  a base asset backing two wrappers) in its OWN single-dep group at top level.
  Nesting it inside one family hides that it is shared and understates it.
* entity_wide marks things that reach everything including idle capital: the
  entity's own contract, its roles, its denomination asset and that asset's admins.
* A dep's "addr" is free text, so you can collapse a set of near-identical
  addresses into one row ("9 further unlabelled USDC admins") and list them all
  in addr. Do that rather than emitting nine rows with identical impact.
"""

BAND_DEFAULT = 70.0
IMPACT_BAND_DEFAULT = 10.0   # a blast radius is a share of the subject, not of a NAV


# ---------------------------------------------------------------- computation

def compute(f, band=BAND_DEFAULT):
    """Resolve impacts and ordering. Returns an enriched dict."""
    pos = f.get("positions", {}) or {}
    totals = f.get("totals", {}) or {}
    nav = totals.get("nav")
    if not nav:
        raise SystemExit("error: totals.nav is required (it is the denominator)")

    unknown = set()

    def usd_of(keys):
        s = 0.0
        for k in keys:
            if k not in pos:
                unknown.add(k)
            s += pos.get(k, 0.0)
        return s

    groups = []
    for g in f.get("groups", []) or []:
        deps = []
        union = set()
        gwide = False
        for d in g.get("deps", []) or []:
            keys = d.get("positions", []) or []
            wide = bool(d.get("entity_wide"))
            impact = float(nav) if wide else usd_of(keys)
            if wide:
                gwide = True
            else:
                union |= set(keys)
            deps.append({**d, "impact": impact, "pct": impact / nav * 100.0})
        deps.sort(key=lambda x: -x["impact"])
        gimpact = float(nav) if gwide else usd_of(union)
        groups.append({
            "name": g.get("name", "(unnamed)"),
            "note": g.get("note", ""),
            "deps": deps,
            "impact": gimpact,
            "pct": gimpact / nav * 100.0,
            "entity_wide": gwide,
            "row_sum": sum(d["impact"] for d in deps),
        })
    groups.sort(key=lambda x: -x["impact"])

    if unknown:
        print("warning: position keys referenced by deps but absent from "
              "'positions': " + ", ".join(sorted(unknown)), file=sys.stderr)

    n_over = sum(1 for g in groups for d in g["deps"] if d["pct"] > band)
    n_deps = sum(len(g["deps"]) for g in groups)

    # measured total across every family: the union of all positions touched,
    # never the sum of the family subtotals, which would multi-count any
    # position that two families both reach.
    all_keys = set()
    any_wide = False
    for g in f.get("groups", []) or []:
        for d in g.get("deps", []) or []:
            if d.get("entity_wide"):
                any_wide = True
            else:
                all_keys |= set(d.get("positions", []) or [])
    total = float(nav) if any_wide else usd_of(all_keys)

    return {"groups": groups, "nav": float(nav), "band": band,
            "n_over": n_over, "n_deps": n_deps, "n_groups": len(groups),
            "total": total}


def check(f, c):
    """Print the arithmetic so it can be eyeballed or diffed."""
    nav, t = c["nav"], f.get("totals", {})
    pos = f.get("positions", {}) or {}
    print(f"total assets (nav) : {nav:>16,.0f}")
    if t.get("deployed") is not None:
        print(f"deployed           : {t['deployed']:>16,.0f}")
    if t.get("idle") is not None:
        print(f"idle               : {t['idle']:>16,.0f}")
        if t.get("deployed") is not None:
            s = t["idle"] + t["deployed"]
            flag = "OK" if abs(s - nav) <= 2 else f"MISMATCH (sums to {s:,.0f})"
            print(f"idle + deployed    : {s:>16,.0f}  {flag}")
    ps = sum(pos.values())
    if t.get("deployed") is not None:
        flag = "OK" if abs(ps - t["deployed"]) <= 2 else "MISMATCH vs deployed"
        print(f"sum(positions)     : {ps:>16,.0f}  {flag}")
    print(f"\n{c['n_groups']} families, {c['n_deps']} dependencies, "
          f"{c['n_over']} above {c['band']:.0f}% of assets\n")
    print(f"{'#':<3}{'family':<34}{'impact':>15}{'%':>8}{'rows':>6}{'rowsum':>15}")
    for i, g in enumerate(c["groups"], 1):
        print(f"{i:<3}{g['name'][:32]:<34}{g['impact']:>15,.0f}"
              f"{g['pct']:>7.1f}%{len(g['deps']):>6}{g['row_sum']:>15,.0f}")
        if g["row_sum"] < g["impact"] - 2:
            print("     ^ WARNING: row sum below union impact, check positions")
    print("\nreminder: group impact is a UNION, so rowsum >= impact is expected.")


# ---------------------------------------------------------------- rendering

def _pct(p):
    return f"{p:.1f}%" if p >= 0.1 else f"{p:.2f}%"


def _bar(p, band):
    cls = "red" if p > band else ""
    return (f'<div class="bar {cls}"><span style="width:'
            f'{max(p, 0.4):.1f}%"></span></div>')


def _addr(a):
    return f' <span class="addr">{escape(str(a))}</span>' if a else ""


def render(f, c):
    e = f.get("entity", {}) or {}
    t = f.get("totals", {}) or {}
    nav, band = c["nav"], c["band"]
    P = []

    # header
    bits = [x for x in [e.get("type"), e.get("chain")] if x]
    sub2 = []
    if e.get("snapshot"):
        sub2.append("Snapshot " + str(e["snapshot"]))
    sub2.append(f"Total assets ${nav/1e6:,.2f}M")
    if t.get("deployed") is not None:
        sub2.append(f"Deployed ${t['deployed']/1e6:,.2f}M")
    if t.get("idle") is not None:
        sub2.append(f"Idle ${t['idle']/1e6:,.2f}M")

    P.append('<header class="doc">')
    impact_mode = str(e.get("mode", "")).lower().startswith("impact")
    label = ("Reverse Impact Analysis" if impact_mode
             else "Dependency Concentration Analysis")
    dl = "of the subject" if impact_mode else "of assets"
    P.append(f'  <div class="eyebrow">Forta Risk Graph &middot; {label}</div>')
    P.append(f'  <h1>{escape(str(e.get("name", "Untitled")))}</h1>')
    if bits or e.get("address"):
        P.append('  <p class="sub">' + escape(" &middot; ".join(bits)).replace(
            "&amp;middot;", "&middot;") +
            (f' &middot; <span class="addr">{escape(str(e.get("address","")))}'
             f'</span>' if e.get("address") else "") + '</p>')
    P.append('  <p class="sub">' + " &middot; ".join(escape(s) for s in sub2) +
             '</p>')
    P.append('</header>')

    # kpis
    top = c["groups"][0] if c["groups"] else None
    if impact_mode:
        kpis = [("red", f"${c['total']/1e6:,.1f}M",
                 f"Measured impact, {_pct(c['total']/nav*100)} {dl}")]
    else:
        kpis = [("red", str(c["n_over"]),
                 f"Dependencies above {band:.0f}% {dl}")]
    if top:
        kpis.append(("red", f"${top['impact']/1e6:,.1f}M",
                     f"Top family: {escape(top['name'])} ({_pct(top['pct'])})"))
    kpis.append(("", str(c["n_deps"]), f"Dependencies across "
                                       f"{c['n_groups']} families"))
    if t.get("deployed") and f.get("positions"):
        mx = max(f["positions"].values())
        kpis.append(("warn", _pct(mx / t["deployed"] * 100),
                     "Deployed capital in one position"))
    P.append('<div class="grid kpis">')
    for cls, n, l in kpis[:4]:
        P.append(f'  <div class="kpi {cls}"><div class="n">{n}</div>'
                 f'<div class="l">{l}</div></div>')
    P.append('</div>')


    # ---- ranked dependency table
    if impact_mode:
        P.append('<h2>1. Ranked impact</h2>')
        P.append('<p class="lead">Everything that loses value if the subject is '
                 'compromised, direct and indirect. Grouped by family: click a '
                 'row to expand. Group impact is the <strong>union</strong> of '
                 'the positions the family touches, not the sum of its rows, '
                 'and the same holds for the measured total. Rows above '
                 f'{band:.0f}% of the subject are banded.</p>')
    else:
        P.append('<h2>1. Ranked dependency impact</h2>')
        P.append('<p class="lead">Every dependency whose compromise exposes '
                 'value, direct and indirect. Grouped by family: click a row to '
                 'expand. Group impact is the <strong>union</strong> of assets '
                 'the family touches, not the sum of its rows. Rows above '
                 f'{band:.0f}% of assets are banded.</p>')
    P.append('<div class="tblctl">'
             '<button type="button" id="exAll">Expand all</button>'
             '<button type="button" id="colAll">Collapse all</button>'
             f'<span class="hint">{c["n_groups"]} families &middot; '
             f'{c["n_deps"]} dependencies</span></div>')
    P.append('<table class="deps">')
    P.append('  <thead><tr><th style="width:30px" class="num">#</th>'
             '<th>Dependency family / dependency</th><th>Layer</th>'
             '<th class="c">Hops</th><th class="num">Impact (USD)</th>'
             f'<th class="num">% {dl}</th>'
             '<th style="width:110px">Share</th></tr></thead>')
    for i, g in enumerate(c["groups"], 1):
        band_c = " band" if g["pct"] > band else ""
        n = len(g["deps"])
        note = (f'<div class="gnote">{escape(g["note"])}</div>'
                if g["note"] else "")
        P.append('  <tbody class="grp">')
        P.append(f'    <tr class="ghead{band_c}" tabindex="0">'
                 f'<td class="num">{i}</td>'
                 f'<td><span class="chev" aria-hidden="true"></span>'
                 f'<strong>{escape(g["name"])}</strong> '
                 f'<span class="cnt">{n} dep{"s" if n != 1 else ""}</span>'
                 f'{note}</td>'
                 f'<td class="c">&mdash;</td><td class="c">&mdash;</td>'
                 f'<td class="num">{g["impact"]:,.0f}</td>'
                 f'<td class="num">{_pct(g["pct"])}</td>'
                 f'<td>{_bar(g["pct"], band)}</td></tr>')
        for d in g["deps"]:
            P.append(f'    <tr class="gsub"><td></td>'
                     f'<td class="ind">{escape(str(d["name"]))}'
                     f'{_addr(d.get("addr"))}</td>'
                     f'<td>{escape(str(d.get("layer","")))}</td>'
                     f'<td class="c">{d.get("hops","")}</td>'
                     f'<td class="num">{d["impact"]:,.0f}</td>'
                     f'<td class="num">{_pct(d["pct"])}</td>'
                     f'<td>{_bar(d["pct"], band)}</td></tr>')
        P.append('  </tbody>')
    P.append('</table>')

    sec = 2

    # ---- exposure
    if f.get("exposure"):
        pos = f.get("positions", {})
        dep = t.get("deployed") or sum(pos.values())
        P.append(f'<h2>{sec}. Value deployment</h2>')
        sec += 1
        P.append('<table><thead><tr><th>Position</th><th>Collateral</th>'
                 '<th class="num">Deployed (USD)</th>'
                 '<th class="num">% of deployed</th>'
                 '<th style="width:120px">Share</th></tr></thead><tbody>')
        rows = sorted(f["exposure"],
                      key=lambda r: -pos.get(r.get("key", ""), 0))
        mx = max([pos.get(r.get("key", ""), 0) for r in rows] or [1]) or 1
        for r in rows:
            u = pos.get(r.get("key", ""), 0)
            p = u / dep * 100 if dep else 0
            bc = " band" if p > 50 else ""
            P.append(f'<tr class="{bc.strip()}"><td>{escape(str(r.get("name","")))}'
                     f' <span class="addr">{escape(str(r.get("key","")))}</span></td>'
                     f'<td>{escape(str(r.get("collateral","")))}</td>'
                     f'<td class="num">{u:,.0f}</td>'
                     f'<td class="num">{_pct(p)}</td>'
                     f'<td><div class="bar {"red" if p>50 else ""}">'
                     f'<span style="width:{max(u/mx*100,0.4):.1f}%"></span>'
                     f'</div></td></tr>')
        P.append(f'</tbody><tfoot><tr><td>Deployed</td><td></td>'
                 f'<td class="num">{dep:,.0f}</td><td class="num">100%</td>'
                 f'<td></td></tr>')
        if t.get("idle") is not None:
            P.append(f'<tr><td>Idle / buffer</td><td></td>'
                     f'<td class="num">{t["idle"]:,.0f}</td>'
                     f'<td class="num">&mdash;</td><td></td></tr>')
        P.append(f'<tr><td>Total assets</td><td></td>'
                 f'<td class="num">{nav:,.0f}</td>'
                 f'<td class="num">&mdash;</td><td></td></tr></tfoot></table>')

        if f.get("buckets"):
            P.append('<table><thead><tr><th>Bucket</th>'
                     '<th class="num">Deployed (USD)</th>'
                     '<th class="num">% of deployed</th>'
                     '<th class="num">% of assets</th></tr></thead><tbody>')
            bk = sorted(f["buckets"],
                        key=lambda b: -sum(pos.get(k, 0) for k in b.get("keys", [])))
            for b in bk:
                u = sum(pos.get(k, 0) for k in b.get("keys", []))
                bc = " band" if (dep and u / dep * 100 > 50) else ""
                P.append(f'<tr class="{bc.strip()}"><td>{escape(str(b.get("name","")))}</td>'
                         f'<td class="num">{u:,.0f}</td>'
                         f'<td class="num">{_pct(u/dep*100 if dep else 0)}</td>'
                         f'<td class="num">{_pct(u/nav*100)}</td></tr>')
            P.append('</tbody></table>')

    # ---- supplier concentration
    if f.get("supplier_concentration"):
        P.append(f'<h2>{sec}. Supplier concentration</h2>')
        sec += 1
        P.append('<p class="lead">Our share of each position\'s total supply. '
                 'A high share is a two-way risk: hard to exit without moving '
                 'the market, and majority absorption of any bad debt.</p>')
        P.append('<table><thead><tr><th>Position</th>'
                 '<th class="num">Total supply</th><th class="num">Our supply</th>'
                 '<th class="num">Our share</th>'
                 '<th class="num">Largest other</th></tr></thead><tbody>')
        rows = sorted(f["supplier_concentration"],
                      key=lambda r: -(r.get("our_supply", 0) /
                                      (r.get("market_supply") or 1)))
        for r in rows:
            ms = r.get("market_supply") or 0
            os_ = r.get("our_supply") or 0
            sh = os_ / ms * 100 if ms else 0
            tag = "hi" if sh >= 60 else ("md" if sh >= 30 else "lo")
            lo = r.get("largest_other_pct")
            P.append(f'<tr><td>{escape(str(r.get("market","")))}</td>'
                     f'<td class="num">{ms:,.0f}</td>'
                     f'<td class="num">{os_:,.0f}</td>'
                     f'<td class="num"><span class="tag {tag}">{_pct(sh)}</span></td>'
                     f'<td class="num">'
                     f'{_pct(lo) if lo is not None else "&mdash;"}</td></tr>')
        P.append('</tbody></table>')

    # ---- borrower concentration
    if f.get("borrower_concentration"):
        P.append(f'<h2>{sec}. Borrower concentration</h2>')
        sec += 1
        P.append('<table><thead><tr><th>Market</th><th>Collateral</th>'
                 '<th class="num">Borrowers</th><th class="num">Market debt</th>'
                 '<th class="num">Market LTV</th><th class="num">LLTV</th>'
                 '<th class="num">Largest borrower</th></tr></thead><tbody>')
        for r in sorted(f["borrower_concentration"],
                        key=lambda r: -(r.get("market_debt_usd") or 0)):
            ltv = r.get("market_ltv_pct")
            lltv = r.get("lltv_pct")
            top = r.get("top_borrower_pct")
            toptag = "hi" if (top or 0) >= 60 else ("md" if (top or 0) >= 30 else "lo")
            P.append(f'<tr><td>{escape(str(r.get("market","")))}</td>'
                     f'<td>{escape(str(r.get("collateral","")))}</td>'
                     f'<td class="num">{r.get("borrowers","&mdash;")}</td>'
                     f'<td class="num">{(r.get("market_debt_usd") or 0):,.0f}</td>'
                     f'<td class="num">{_pct(ltv) if ltv is not None else "&mdash;"}</td>'
                     f'<td class="num">{_pct(lltv) if lltv is not None else "&mdash;"}</td>'
                     f'<td class="num"><span class="tag {toptag}">'
                     f'{_pct(top) if top is not None else "&mdash;"}</span></td></tr>')
        P.append('</tbody></table>')

    # ---- encumbrance bridge (levered entities)
    if f.get("encumbrance"):
        en = f["encumbrance"]
        collat = float(en.get("collateral_usd") or 0)
        debt = float(en.get("debt_usd") or 0)
        remain = float(en.get("remaining_usd") or 0)
        unpledged = float(en.get("unpledged_usd") or 0)
        bonus = float(en.get("liquidation_bonus") or 0)
        # what the walk-away option is worth: debt not covered by what is left
        relief = max(0.0, debt - remain)
        # liquidators are paid a premium, so seizing the REMAINING collateral
        # retires less debt than its face value. The entity's relief is
        # unchanged; the counterparty's shortfall grows by the premium.
        # liquidation_bonus is 1e4-scaled: 10800 means a 1.08 multiplier,
        # i.e. an 8% premium paid to liquidators.
        mult = (bonus / 1e4) if bonus and bonus > 1e4 else 1.0
        retired = remain / mult
        absorbed = max(0.0, debt - retired)
        pledged_loss = max(0.0, collat - relief)
        total = pledged_loss + unpledged
        gross = collat + unpledged
        P.append(f'<h2>{sec}. Non-recourse floor on the pledged leg</h2>')
        sec += 1
        P.append('<p class="lead">The pledged leg is collateral against '
                 'non-recourse debt, so loss on it is capped at equity. The '
                 'unpledged leg has no loan to walk away from and takes a full '
                 'loss. The relief is <strong>tail-only</strong>: in an orderly '
                 'decline liquidators unwind at the threshold and the entity '
                 'pays the liquidation bonus, so it does worse than '
                 'mark-to-market. The seizable base is reserve-level, so it is '
                 'an upper bound on what can be taken from this account.</p>')
        P.append('<table><thead><tr><th>Step</th><th class="num">USD</th>'
                 '<th>Basis</th></tr></thead><tbody>')

        def _row(lbl, val, basis, cls=""):
            P.append(f'<tr class="{cls}"><td>{escape(lbl)}</td>'
                     f'<td class="num">{val:,.0f}</td>'
                     f'<td class="lead">{escape(basis)}</td></tr>')

        _row(f'{en.get("asset","Pledged asset")} collateral consumed', collat,
             "pledged position, gross")
        _row("Loan extinguished", -relief,
             f'debt {debt:,.0f} less remaining seizable collateral {remain:,.0f}')
        _row("Loss on the pledged leg", pledged_loss, "capped at equity")
        _row("Loss on the unpledged leg", unpledged, "held loose, no offset")
        P.append('</tbody><tfoot>')
        _row("Total loss", total, "pledged plus unpledged")
        _row("Delta against gross", total - gross,
             f'gross exposure {gross:,.0f}')
        P.append('</tfoot></table>')
        if relief > 0:
            who = en.get("absorbed_by") or "the lending protocol"
            extra = ""
            if mult > 1.0:
                extra = (f' The remaining collateral retires only '
                         f'{retired:,.0f} of debt after a '
                         f'{(mult-1)*100:.1f}% liquidation bonus, so the shortfall '
                         f'absorbed is {absorbed:,.0f}, larger than the plain '
                         f'subtraction.')
            P.append('<p class="lead">The relief is a transfer, not a '
                     f'disappearance: the {relief:,.0f} this entity escapes is '
                     f'absorbed by {escape(str(who))}.{extra}</p>')

    # ---- additive impact (reverse impact reports)
    if f.get("additive"):
        P.append(f'<h2>{sec}. Additive impact on other assets</h2>')
        sec += 1
        P.append('<p class="lead">Dollars denominated in something other than '
                 'the subject: bad debt, pool counter-legs. These are new '
                 'dollars and legitimately exceed the subject\'s own size.</p>')
        P.append('<table><thead><tr><th style="width:30px" class="num">#</th>'
                 '<th>Path</th><th>Layer</th><th class="c">Hops</th>'
                 '<th class="num">Impact (USD)</th><th>Basis</th>'
                 '</tr></thead><tbody>')
        rows = sorted(f["additive"], key=lambda r: -(r.get("usd") or 0))
        for i, r in enumerate(rows, 1):
            P.append(f'<tr><td class="num">{i}</td>'
                     f'<td>{escape(str(r.get("path","")))}'
                     f'{_addr(r.get("addr"))}</td>'
                     f'<td>{escape(str(r.get("layer","")))}</td>'
                     f'<td class="c">{r.get("hops","")}</td>'
                     f'<td class="num">{(r.get("usd") or 0):,.0f}</td>'
                     f'<td class="lead">{escape(str(r.get("note","")))}</td></tr>')
        tot = sum(r.get("usd") or 0 for r in rows)
        P.append(f'</tbody><tfoot><tr><td></td><td>Total additive</td><td></td>'
                 f'<td></td><td class="num">{tot:,.0f}</td><td></td></tr>'
                 f'</tfoot></table>')

    # ---- re-attribution (reverse impact reports)
    if f.get("reattribution"):
        P.append(f'<h2>{sec}. Re-attribution layers, not additive</h2>')
        sec += 1
        P.append('<p class="lead">The same dollars as the ranked table above, '
                 'resolved to better-named victims. <strong>Do not add these '
                 'to the total.</strong></p>')
        P.append('<table><thead><tr><th>Layer</th><th class="num">Value (USD)'
                 '</th><th>Resolves to</th></tr></thead><tbody>')
        for r in sorted(f["reattribution"], key=lambda r: -(r.get("usd") or 0)):
            P.append(f'<tr><td>{escape(str(r.get("layer","")))}</td>'
                     f'<td class="num">{(r.get("usd") or 0):,.0f}</td>'
                     f'<td class="lead">{escape(str(r.get("resolves","")))}'
                     f'</td></tr>')
        P.append('</tbody></table>')

    # ---- backing exposure (reverse impact reports)
    if f.get("backing_exposure"):
        P.append(f'<h2>{sec}. Share of other assets\' backing</h2>')
        sec += 1
        P.append('<p class="lead">Where the subject is part of what backs '
                 'something else, impact attenuates by its share of that '
                 'backing. A low share is a real answer, not a row to '
                 'suppress.</p>')
        P.append('<table><thead><tr><th>Asset</th><th>Mechanism</th>'
                 '<th class="num">Subject\'s share of backing</th>'
                 '<th>Basis of the denominator</th></tr></thead><tbody>')
        for r in sorted(f["backing_exposure"],
                        key=lambda r: -(r.get("share_pct") or 0)):
            sp = r.get("share_pct")
            sp_txt = ("&mdash;" if sp is None else
                      (f"{sp:.2f}%" if sp < 1 else _pct(sp)))
            P.append(f'<tr><td>{escape(str(r.get("asset","")))}</td>'
                     f'<td>{escape(str(r.get("mechanism","")))}</td>'
                     f'<td class="num">{sp_txt}</td>'
                     f'<td class="lead">{escape(str(r.get("basis","")))}'
                     f'</td></tr>')
        P.append('</tbody></table>')

    # ---- method: one compact line, folded into the footer below
    method_line = (f"Exposure sizing under full compromise, not expected loss. "
                   f"Shares of ${nav:,.0f} total assets. Group rows are unions, "
                   f"not sums. Snapshot spot USD.")

    P.append(f'<div class="foot">Forta Risk Graph &middot; {label} &middot; '
             f'{method_line} '
             f'{escape(str(e.get("name","")))} '
             f'<span class="addr">{escape(str(e.get("address","")))}</span>'
             f'{" &middot; " + escape(str(e.get("snapshot"))) if e.get("snapshot") else ""}'
             ' &middot; point-in-time, not investment advice.</div>')

    return TEMPLATE.replace("__TITLE__",
                            escape(str(e.get("name", "Risk report")))
                            ).replace("__BODY__", "\n".join(P))


TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Risk analysis: __TITLE__</title>
<style>
  :root{
    --bg:#0d1117; --panel:#161b22; --panel2:#1c2230; --border:#2a3240;
    --text:#e6edf3; --muted:#9aa7b4; --accent:#4f8cff;
    --warn:#f0a742; --danger:#f0553d; --ok:#38d39f;
    --mono:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    line-height:1.5;font-size:14.5px}
  .wrap{max-width:1120px;margin:0 auto;padding:36px 26px 70px}
  header.doc{border-bottom:1px solid var(--border);padding-bottom:18px}
  .eyebrow{color:var(--accent);font-size:11.5px;letter-spacing:.14em;
    text-transform:uppercase;font-weight:600}
  h1{font-size:25px;margin:8px 0 5px;font-weight:700;letter-spacing:-.01em}
  h2{font-size:18px;margin:38px 0 6px;font-weight:650;letter-spacing:-.01em;
    padding-bottom:7px;border-bottom:1px solid var(--border)}
  .sub{color:var(--muted);font-size:13px}
  .addr{font-family:var(--mono);font-size:11.5px;color:var(--muted)}
  p{margin:9px 0}
  .lead{color:var(--muted);font-size:13px;margin:8px 0 2px}
  .grid{display:grid;gap:12px}
  .kpis{grid-template-columns:repeat(4,1fr);margin:18px 0 4px}
  .kpi{background:var(--panel);border:1px solid var(--border);
    border-radius:9px;padding:14px}
  .kpi .n{font-size:20px;font-weight:700;letter-spacing:-.02em}
  .kpi .l{color:var(--muted);font-size:11.5px;margin-top:3px}
  .kpi.red .n{color:var(--danger)} .kpi.warn .n{color:var(--warn)}
  table{width:100%;border-collapse:collapse;margin:14px 0;font-size:13px}
  th,td{text-align:left;padding:8px 9px;border-bottom:1px solid var(--border);
    vertical-align:top}
  th{color:var(--muted);font-weight:600;font-size:11.5px;
    text-transform:uppercase;letter-spacing:.05em}
  td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;
    font-family:var(--mono);font-size:12.5px;white-space:nowrap}
  td.c,th.c{text-align:center}
  tr:hover td{background:rgba(255,255,255,.02)}
  tbody tr.band td{background:rgba(240,85,61,.06)}
  tbody tr.band:hover td{background:rgba(240,85,61,.1)}
  tfoot td{font-weight:600;border-top:1px solid var(--border);border-bottom:none}
  .tag{display:inline-block;padding:1px 7px;border-radius:999px;font-size:10.5px;
    font-weight:600;border:1px solid var(--border);background:var(--panel2);
    color:var(--muted);white-space:nowrap}
  .tag.hi{color:var(--danger);border-color:#5a2a24}
  .tag.md{color:var(--warn);border-color:#5a4420}
  .tag.lo{color:var(--ok);border-color:#1f4d3d}
  .bar{position:relative;height:7px;background:var(--panel2);border-radius:4px;
    overflow:hidden;min-width:70px}
  .bar > span{position:absolute;left:0;top:0;bottom:0;border-radius:4px;
    background:var(--accent)}
  .bar.red > span{background:var(--danger)}
  .callout{border-radius:9px;padding:13px 15px;margin:14px 0;
    border:1px solid var(--border);background:var(--panel)}
  .callout.key{border-left:3px solid var(--accent)}
  .callout.gap{border-left:3px solid var(--muted);background:var(--panel2)}
  .callout h4{margin:0 0 6px;font-size:12px;text-transform:uppercase;
    letter-spacing:.06em;color:var(--muted)}
  .callout p{margin:6px 0;font-size:13px}
  .foot{margin-top:44px;padding-top:15px;border-top:1px solid var(--border);
    color:var(--muted);font-size:12px}
  code{font-family:var(--mono);font-size:12px;background:var(--panel2);
    padding:1px 4px;border-radius:3px}
  table.deps tbody.grp{border-bottom:1px solid var(--border)}
  tr.ghead{cursor:pointer;user-select:none}
  tr.ghead td{border-bottom:1px solid var(--border)}
  tr.ghead:hover td{background:rgba(79,140,255,.07)}
  tr.ghead:focus{outline:2px solid var(--accent);outline-offset:-2px}
  .chev{display:inline-block;width:0;height:0;margin-right:8px;
    vertical-align:middle;border-left:5px solid var(--muted);
    border-top:4px solid transparent;border-bottom:4px solid transparent;
    transition:transform .15s ease}
  tbody.grp.open .chev{transform:rotate(90deg) translateX(1px)}
  .cnt{display:inline-block;margin-left:7px;padding:1px 7px;border-radius:999px;
    font-size:10.5px;font-weight:600;color:var(--muted);
    border:1px solid var(--border);background:var(--panel2)}
  .gnote{color:var(--muted);font-size:11.5px;font-weight:400;margin-top:2px}
  tbody.grp:not(.open) tr.gsub{display:none}
  tr.gsub td{background:rgba(0,0,0,.18);font-size:12.5px}
  tr.gsub:hover td{background:rgba(255,255,255,.03)}
  td.ind{padding-left:26px;position:relative}
  td.ind:before{content:"";position:absolute;left:12px;top:0;bottom:0;
    border-left:1px solid var(--border)}
  .tblctl{display:flex;gap:8px;align-items:center;margin:10px 0 0}
  .tblctl button{background:var(--panel);color:var(--text);
    border:1px solid var(--border);border-radius:6px;padding:5px 11px;
    font-size:12px;cursor:pointer;font-family:inherit}
  .tblctl button:hover{border-color:var(--accent);color:var(--accent)}
  .tblctl .hint{color:var(--muted);font-size:11.5px;margin-left:2px}
  @media(max-width:820px){.kpis{grid-template-columns:repeat(2,1fr)}}
</style>
</head>
<body>
<div class="wrap">
__BODY__
</div>
<script>
(function(){
  var groups=[].slice.call(document.querySelectorAll('table.deps tbody.grp'));
  function toggle(g){ g.classList.toggle('open'); }
  groups.forEach(function(g){
    var h=g.querySelector('tr.ghead');
    if(!h) return;
    h.addEventListener('click',function(){ toggle(g); });
    h.addEventListener('keydown',function(e){
      if(e.key==='Enter'||e.key===' '){ e.preventDefault(); toggle(g); }
    });
  });
  var ex=document.getElementById('exAll'), col=document.getElementById('colAll');
  if(ex) ex.addEventListener('click',function(){
    groups.forEach(function(g){ g.classList.add('open'); }); });
  if(col) col.addEventListener('click',function(){
    groups.forEach(function(g){ g.classList.remove('open'); }); });
})();
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(
        description="Build a dependency-concentration or reverse-impact risk report from findings JSON.")
    ap.add_argument("findings", nargs="?", help="path to findings JSON")
    ap.add_argument("-o", "--out", default="report.html", help="output HTML path")
    ap.add_argument("--band", type=float, default=BAND_DEFAULT,
                    help="highlight threshold, %% of assets (default 70)")
    ap.add_argument("--check", action="store_true",
                    help="print the arithmetic, write no HTML")
    ap.add_argument("--schema", action="store_true", help="print input schema")
    a = ap.parse_args()

    if a.schema:
        print(SCHEMA)
        return
    if not a.findings:
        ap.error("findings JSON required (or use --schema)")

    with open(a.findings) as fh:
        f = json.load(fh)

    band = a.band
    if not any(x.startswith("--band") for x in sys.argv[1:]):
        mode = str((f.get("entity") or {}).get("mode", "")).lower()
        if mode.startswith("impact"):
            band = IMPACT_BAND_DEFAULT
    c = compute(f, band)

    if a.check:
        check(f, c)
        return

    with open(a.out, "w") as fh:
        fh.write(render(f, c))
    print(f"wrote {a.out}: {c['n_groups']} families, {c['n_deps']} dependencies, "
          f"{c['n_over']} above {c['band']:.0f}% of the denominator")


if __name__ == "__main__":
    main()
