# -*- coding: utf-8 -*-
import json

import sys, argparse
SCHEMA = '''
node tree JSON schema

pass a file containing:
  {"title":"Protocol name",        # required, used in the heading and the title tag
   "scope":"what the denominator covers",
   "page_title":"Aave v3 Control Closure",  # optional browser-tab / gallery name; defaults to "Dependency closure: <title>"
   "nav": 375500000,               # required, the denominator
   "snapshot":"14 Aug 2026",
   "floor":"$1M",                  # optional, expansion floor, quoted in the footer
   "headline":["1","key reaching 71.0% of supply"],   # optional lead KPI
   "positions":{...}, "tree":[node,...],
   "blast":{...},                  # optional blast-radius section, see below
   }
=====================
node = {
  "name":"USDC",                      # required
  "addr":"0xa0b869…eb48",
  "cat":"external / assets",          # category / subcategory, drives the filter
  "typ":"asset",                      # EOA | multisig | contract | asset | oracle | unconfirmed
  "ctrl":"15 admins, timelock 0",     # control summary
  "mech":"freeze",                    # failure mechanism: bug in code | key compromise | freeze | ...
  "pos":["eUSDC-80","eUSDC-70"],      # positions THIS node touches directly
  "note":"free text shown under the name",
  "flag":"crit",                      # crit | warn | unconfirmed | ok | eoa  -> left border colour
  "kids":[ node, ... ]                # recurse to any depth
}
positions = {"eUSDC-80": 67425072, ...}   # position key -> USD. Include a sub-floor aggregate
                                          # so entity-wide rows read 100%, not 97%.
Exposure per node is computed as the UNION of its own pos and all descendants', never a sum.
'''
if '--schema' in sys.argv:
    print(SCHEMA); raise SystemExit(0)
_ap = argparse.ArgumentParser()
_ap.add_argument('tree'); _ap.add_argument('-o','--out',default='closure.html')
_ns = _ap.parse_args()
_d = json.load(open(_ns.tree))
NAV = _d['nav']; SNAP = _d.get('snapshot',''); M = _d['positions']; TREE = _d['tree']
for _t in TREE:
    _t.setdefault('kids',[])
def _fill(x):
    for k in ('addr','cat','typ','ctrl','mech','note','flag'): x.setdefault(k,'')
    x.setdefault('pos',[]); x.setdefault('kids',[])
    for c in x['kids']: _fill(c)
for _t in TREE: _fill(_t)


def roll(node):
    """exposure = union of positions of self and all descendants"""
    s=set(node["pos"])
    for k in node["kids"]: s|=roll(k)
    node["_u"]=s
    node["_usd"]=sum(M[x] for x in s)
    return s
for t in TREE: roll(t)

rows=[]
def walk(nd,depth,path):
    i=len(rows)
    rows.append({"i":i,"d":depth,"name":nd["name"],"addr":nd["addr"],"cat":nd["cat"],
                 "typ":nd["typ"],"ctrl":nd["ctrl"],"mech":nd["mech"],"note":nd["note"],
                 "usd":nd["_usd"],"npos":len(nd["_u"]),"flag":nd["flag"],
                 "kids":[],"nk":len(nd["kids"])})
    for k in nd["kids"]:
        rows[i]["kids"].append(walk(k,depth+1,path+[i]))
    return i
for t in TREE: walk(t,0,[])

MAXD=max(r["d"] for r in rows)
stats={"rows":len(rows),"maxdepth":MAXD,
 "eoas":len({r["addr"] for r in rows if r["typ"]=="EOA" and r["addr"].startswith("0x")
             and not r["cat"].startswith("counterparty")}),
 "crit":len([r for r in rows if r["flag"]=="crit"])}

CSS="""*{box-sizing:border-box}body{margin:0;background:#0d1117;color:#e6edf3;font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.w{max-width:1440px;margin:0 auto;padding:30px 22px 80px}
h1{font-size:25px;margin:8px 0 4px;letter-spacing:-.01em}h2{font-size:17px;margin:34px 0 6px;padding-bottom:7px;border-bottom:1px solid #2a3240}
.eyebrow{color:#4f8cff;font-size:11px;letter-spacing:.14em;text-transform:uppercase;font-weight:600}
.sub,.lead{color:#9aa7b4;font-size:12.5px}
.kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin:16px 0}
.kpi{background:#161b22;border:1px solid #2a3240;border-radius:8px;padding:12px}
.kpi .n{font-size:19px;font-weight:700}.kpi .l{color:#9aa7b4;font-size:11px;margin-top:2px}
.kpi.red .n{color:#f0553d}.kpi.warn .n{color:#f0a742}.kpi.ok .n{color:#38d39f}
.call{background:#1c1410;border:1px solid #6b3b22;border-left:3px solid #f0553d;border-radius:8px;padding:13px 15px;margin:14px 0}
.call h4{margin:0 0 5px;font-size:12.5px;text-transform:uppercase;letter-spacing:.03em;color:#f0553d}
.bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:14px 0 6px}
button{background:#1c2230;color:#cbd5e1;border:1px solid #2a3240;border-radius:6px;padding:6px 11px;font-size:12px;cursor:pointer;font-family:inherit}
button:hover{background:#243044;color:#fff}
input[type=text]{background:#0f141b;color:#e6edf3;border:1px solid #2a3240;border-radius:6px;padding:6px 10px;font-size:12px;min-width:230px;font-family:inherit}
select{background:#1c2230;color:#cbd5e1;border:1px solid #2a3240;border-radius:6px;padding:6px 9px;font-size:12px;font-family:inherit}
table.dep{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:8px}
table.dep th{position:sticky;top:0;background:#0d1117;z-index:2;text-align:left;padding:8px 8px;border-bottom:1px solid #2a3240;color:#9aa7b4;font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;font-weight:600}
table.dep td{padding:5px 8px;border-bottom:1px solid #1b2230;vertical-align:top}
table.dep tr.lvl0>td{background:#141a23;font-weight:650}
table.dep tr.lvl1>td{background:#11161e}
table.dep tr:hover>td{background:#1a212c}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.addr{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:11px;color:#8b98a5;word-break:break-all}
.tw{cursor:pointer;display:inline-block;width:14px;color:#4f8cff;user-select:none;font-size:11px}
.tw.leaf{color:#39424e;cursor:default}
.nm{display:inline}
.pill{display:inline-block;font-size:10px;padding:1px 6px;border-radius:4px;border:1px solid #2a3240;color:#9aa7b4;white-space:nowrap}
.pill.eoa{border-color:#7a2f24;color:#f0553d}.pill.multisig{border-color:#1f5445;color:#38d39f}
.pill.gap,.pill.unconfirmed{border-color:#6b3b22;color:#f0a742}.pill.asset{border-color:#2f4a7a;color:#7fa8ff}
.pill.oracle{border-color:#4a3570;color:#b291f0}.pill.contract{border-color:#39424e;color:#9aa7b4}
.f-crit{border-left:3px solid #f0553d}.f-warn{border-left:3px solid #f0a742}
.f-gap,.f-unconfirmed{border-left:3px solid #6b3b22}.f-ok{border-left:3px solid #38d39f}.f-eoa{border-left:3px solid #7a2f24}
.note{color:#7d8998;font-size:11.5px;display:block;margin-top:2px;max-width:640px}
.mech{color:#9aa7b4;font-size:11.5px}
details{margin:7px 0}summary{cursor:pointer;color:#cbd5e1;font-size:12.5px;padding:4px 0}
footer{margin-top:40px;padding-top:14px;border-top:1px solid #2a3240;color:#9aa7b4;font-size:11.5px}
.hidden{display:none}
.legend{color:#7d8998;font-size:11.5px;margin:6px 0 0}
table.bl{width:100%;border-collapse:collapse;font-size:12.5px;margin:10px 0 4px}
table.bl th{text-align:left;padding:7px 8px;border-bottom:1px solid #2a3240;color:#9aa7b4;font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;font-weight:600}
table.bl td{padding:6px 8px;border-bottom:1px solid #1b2230;vertical-align:top}
table.bl tr:hover>td{background:#1a212c}
table.bl td.n,table.bl th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
td.bind{background:#2a1d10;color:#f0a742;font-weight:650;box-shadow:inset 2px 0 0 #f0a742}
td.unread{color:#7d8998;font-style:italic}
.asset{font-weight:650}
.scen{color:#7d8998;font-size:11.5px;margin:4px 0 12px}"""

JS="""
var ROWS=__ROWS__;
var open={};
ROWS.forEach(function(r){ open[r.i] = (r.d<1); });
function visible(){
  var vis=[], stack=[];
  function rec(i){
    var r=ROWS[i]; vis.push(i);
    if(open[i]) r.kids.forEach(rec);
  }
  ROWS.filter(function(r){return r.d===0;}).forEach(function(r){rec(r.i);});
  return vis;
}
function money(x){ return x?('$'+x.toLocaleString('en-US',{maximumFractionDigits:0})):'—'; }
function pct(x){ return x?((x/__NAV__*100).toFixed(2)+'%'):'—'; }
function render(){
  var q=document.getElementById('q').value.toLowerCase();
  var cf=document.getElementById('cf').value;
  var tb=document.getElementById('tb'), h='';
  var vis=visible();
  vis.forEach(function(i){
    var r=ROWS[i];
    if(cf && r.cat.indexOf(cf)!==0 && r.d>0) { }
    var hay=(r.name+' '+r.addr+' '+r.cat+' '+r.mech+' '+r.note).toLowerCase();
    if(q && hay.indexOf(q)<0) return;
    if(cf && r.cat.indexOf(cf)!==0) return;
    var tw = r.nk? "<span class='tw' onclick='tog("+i+")'>"+(open[i]?'▾':'▸')+"</span>"
                 : "<span class='tw leaf'>·</span>";
    var pad = 4 + r.d*17;
    h += "<tr class='lvl"+r.d+" "+(r.flag?'f-'+r.flag:'')+"'>"
      + "<td style='padding-left:"+pad+"px'>"+tw+" <span class=nm>"+esc(r.name)+"</span>"
      + (r.nk? " <span class=pill>"+r.nk+"</span>":"")
      + (r.note? "<span class=note>"+esc(r.note)+"</span>":"") + "</td>"
      + "<td class=addr>"+esc(r.addr)+"</td>"
      + "<td>"+(r.typ? "<span class='pill "+r.typ+"'>"+r.typ+"</span>":"")+"</td>"
      + "<td class=mech>"+esc(r.cat)+"</td>"
      + "<td class=mech>"+esc(r.ctrl)+"</td>"
      + "<td class=mech>"+esc(r.mech)+"</td>"
      + "<td class=n>"+money(r.usd)+"</td>"
      + "<td class=n>"+pct(r.usd)+"</td>"
      + "<td class=n>"+(r.npos||'—')+"</td></tr>";
  });
  tb.innerHTML=h;
  document.getElementById('shown').textContent=tb.children.length;
}
function esc(s){ return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;'); }
function tog(i){ open[i]=!open[i]; render(); }
function setAll(v){ ROWS.forEach(function(r){ open[r.i]=v; }); render(); }
function lvl(n){ ROWS.forEach(function(r){ open[r.i]= r.d<n; }); render(); }
function crit(){ ROWS.forEach(function(r){open[r.i]=true;}); document.getElementById('q').value=''; document.getElementById('cf').value=''; render();
  }
"""

def render_blast(_d, NAV, esc_py):
    b=_d.get("blast")
    if not b: return []
    A=[]; w=A.append
    LBL={"collateral_room":"Collateral already posted, at reserve LTV",
         "postable":"Further collateral that could be posted",
         "liquidity":"Liquidity in the other reserves",
         "borrow_cap":"Borrow cap headroom"}
    rows=[]
    for x in b.get("assets",[]):
        cons=x.get("constraints",{})
        live={k:v for k,v in cons.items() if isinstance(v,(int,float))}
        extra=min(live.values()) if live else 0
        binding=[k for k,v in live.items() if v==extra]
        cur=x["supplied"]; mx=cur+extra
        rows.append(dict(x=x,cons=cons,extra=extra,binding=set(binding),cur=cur,mx=mx))
    rows.sort(key=lambda r:-r["mx"])

    w("<h2>%s</h2>"%esc_py(b.get("title","Blast radius")))
    w("<p class=lead>%s</p>"%esc_py(b.get("lead","")))
    w("<p class=scen>%s</p>"%esc_py(b.get("scenario_note","")))

    w("<table class=bl><thead><tr><th>Reserve</th><th>Failure assumed</th><th class=n>Supplied</th>"
      "<th class=n>Current estimated loss</th><th class=n>Share</th><th class=n>Maximum loss</th><th class=n>Share</th>"
      "<th>What bounds the extra draw</th></tr></thead><tbody>")
    for r in rows:
        x=r["x"]
        bindlbl=", ".join(LBL.get(k,k) for k in sorted(r["binding"])) if r["binding"] else "nothing bounds it"
        w("<tr><td><span class=asset>%s</span><span class=addr>%s</span></td>"
          "<td class=mech>%s</td><td class=n>$%s</td><td class=n>$%s</td><td class=n>%s%%</td>"
          "<td class=n>$%s</td><td class=n>%s%%</td><td class=mech>%s</td></tr>"
          %(esc_py(x["name"]),esc_py(x.get("addr","")),esc_py(b.get("vector","becomes worthless")),
            format(x["supplied"],","),format(r["cur"],","),("%.2f"%(100.0*r["cur"]/NAV)),
            format(r["mx"],","),("%.2f"%(100.0*r["mx"]/NAV)),esc_py(bindlbl)))
        if x.get("note"):
            w("<tr><td colspan=8 class=note style='padding-left:10px'>%s</td></tr>"%esc_py(x["note"]))
    w("</tbody></table>")

    w("<h2>%s</h2>"%esc_py(b.get("constraints_title","Defensive borrowing: the four constraints")))
    w("<p class=lead>%s</p>"%esc_py(b.get("constraints_lead","")))
    order=["collateral_room","postable","liquidity","borrow_cap"]
    w("<table class=bl><thead><tr><th>Reserve</th>"+"".join("<th class=n>%s</th>"%LBL[k] for k in order)
      +"<th class=n>Extra drawable</th><th class=n>Maximum loss</th></tr></thead><tbody>")
    for r in rows:
        x=r["x"]; w("<tr><td><span class=asset>%s</span></td>"%esc_py(x["name"]))
        for k in order:
            v=r["cons"].get(k)
            if isinstance(v,(int,float)):
                cls="n bind" if k in r["binding"] else "n"
                w("<td class='%s'>$%s</td>"%(cls,format(int(v),",")))
            else:
                w("<td class='n unread'>%s</td>"%esc_py(v if isinstance(v,str) else "not carried"))
        w("<td class=n>$%s</td><td class=n>$%s</td></tr>"%(format(r["extra"],","),format(r["mx"],",")))
    w("</tbody></table>")
    w("<p class=legend>%s</p>"%esc_py(b.get("constraints_legend","The highlighted cell is the constraint that binds: the smallest of the four sets the extra draw.")))

    hs=b.get("holders")
    if hs:
        w("<h2>%s</h2>"%esc_py(hs.get("title","Who absorbs the loss")))
        w("<p class=lead>%s</p>"%esc_py(hs.get("lead","")))
        w("<table class=bl><thead><tr><th>Reserve</th><th class=n>Holders</th><th>Largest single holder</th>"
          "<th class=n>Its position</th><th class=n>Share of reserve</th></tr></thead><tbody>")
        for x in hs.get("rows",[]):
            w("<tr><td><span class=asset>%s</span></td><td class=n>%s</td><td>%s<span class=addr>%s</span></td>"
              "<td class=n>$%s</td><td class=n>%s%%</td></tr>"
              %(esc_py(x["name"]),format(x["holders"],","),esc_py(x["top_label"]),esc_py(x.get("top_addr","")),
                format(x["top_usd"],","),("%.2f"%(100.0*x["top_usd"]/x["reserve_total"]))))
            if x.get("note"):
                w("<tr><td colspan=5 class=note style='padding-left:10px'>%s</td></tr>"%esc_py(x["note"]))
        w("</tbody></table>")
        if hs.get("legend"): w("<p class=legend>%s</p>"%esc_py(hs["legend"]))
    return A

def esc_py(x):
    return str(x).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

H=[]; a=H.append
TITLE=_d.get("title","Protocol")
a("<!doctype html><meta charset=utf-8><title>%s</title>"%esc_py(_d.get("page_title") or ("Dependency closure: "+TITLE)))
a("<style>%s</style><div class=w>"%CSS)
a("<div class=eyebrow>Protocol dependency closure</div><h1>%s: every dependency, one table</h1>"%esc_py(TITLE))
a("<div class=sub>%s &middot; snapshot %s &middot; partition <span class=addr>risk-graph-rt-v3</span> &middot; denominator $%s</div>"%(_d.get("scope",""),SNAP,format(NAV,",")))
a("<div class=kpis>")
_hl=_d.get("headline")
if _hl:
    a("<div class='kpi red'><div class=n>%s</div><div class=l>%s</div></div>"%(esc_py(_hl[0]),esc_py(_hl[1])))
else:
    a("<div class='kpi red'><div class=n>%d</div><div class=l>terminal keys above the floor</div></div>"%stats["eoas"])
a("<div class=kpi><div class=n>%d</div><div class=l>dependency rows</div></div>"%stats["rows"])
a("<div class=kpi><div class=n>%d</div><div class=l>max hops from the protocol</div></div>"%stats["maxdepth"])
a("<div class='kpi warn'><div class=n>%d</div><div class=l>rows flagged critical</div></div>"%stats["crit"])
a("<div class=kpi><div class=n>$%s</div><div class=l>denominator</div></div>"%format(NAV,","))
a("</div>")
a("""<div class=call><h4>How to read the exposure column</h4><p>Exposure is the market supply that sits beneath a node, computed as the <b>union</b> of the positions of that node and everything under it, so a parent is never the sum of its children. It answers: <i>if this contract is compromised, whether by a bug or a key, how much user supply is in scope.</i> It is not expected loss: no probability weighting, no recovery, and no assumption that an attacker extracts the full amount.</p></div>""")

H.extend(render_blast(_d, NAV, esc_py))

a("<h2>All dependencies</h2>")
a("<p class=lead>Expand any row to see what it depends on, and keep going. Depth runs to %d hops from the protocol.</p>"%stats["maxdepth"])
a("<div class=bar>")
a("<button onclick='lvl(1)'>Overview</button><button onclick='lvl(2)'>2 levels</button><button onclick='lvl(3)'>3 levels</button>")
a("<button onclick='setAll(true)'>Expand all</button><button onclick='setAll(false)'>Collapse all</button>")
a("<input type=text id=q placeholder='Filter: address, name, mechanism…' oninput='render()'>")
a("<select id=cf onchange='render()'><option value=''>All categories</option>")
for c in ["internal","internal / core","internal / governance","internal / curators","internal / periphery",
          "external / assets","external / oracles","counterparty","unconfirmed"]:
    a("<option value='%s'>%s</option>"%(c,c))
a("</select>")
a("<span class=lead><span id=shown>0</span> rows shown</span></div>")
a("<p class=legend>Left border: red = critical single point, orange = warning or not confirmed on-chain, green = verified absence of control. Type pill: EOA, multisig, contract, asset, oracle.</p>")
a("<table class=dep><thead><tr><th>Dependency</th><th>Address</th><th>Type</th><th>Category</th><th>Control</th><th>Failure mechanism</th><th class=n>Exposure</th><th class=n>Share</th><th class=n>Mkts</th></tr></thead><tbody id=tb></tbody></table>")

a("<footer>Recursive inbound ADMIN_CTRL traversal from every dependency above %s of supply, terminating at an EOA, a multisig, a cycle or a role registry. Exposure is a union over markets, not a sum: full-compromise sizing, not expected loss. Forta Risk Graph, %s.</footer>"%(_d.get("floor","$1M"),SNAP))
a("<script>%s</script><script>render();</script></div>"%JS.replace("__ROWS__",json.dumps(rows)).replace("__NAV__",str(NAV)))
open(_ns.out,"w").write("".join(H))
print("wrote %s: %d rows, max depth %d, %d critical, %d terminal keys"%(_ns.out,stats["rows"],stats["maxdepth"],stats["crit"],stats["eoas"]))
