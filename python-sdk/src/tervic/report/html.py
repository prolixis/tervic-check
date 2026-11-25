"""One self-contained HTML file: no external requests, opens offline.

Contents: run summary and verdict, a step waterfall (durations and cost), findings grouped by root
cause, a step inspector (input / output / state diff, click any step) and the baseline diff.
"""

from __future__ import annotations

import html
import json
from typing import Any

from tervic.analyze.engine import Report
from tervic.analyze.model import RunView


def _data(report: Report, view: RunView) -> dict[str, Any]:
    start = view.started_ts or (view.steps[0].started_ts if view.steps else 0.0)
    steps = []
    for step in view.steps:
        summary = next((s for s in report.steps if s.step_id == step.step_id), None)
        steps.append(
            {
                "id": step.step_id,
                "name": step.name,
                "kind": step.kind,
                "parent": step.parent_step_id,
                "start_ms": max(0.0, (step.started_ts - start) * 1000),
                "duration_ms": step.duration_ms,
                "cost": summary.cost_usd if summary else 0.0,
                "severity": summary.severity if summary else None,
                "source": step.source,
                "error": f"{step.error.type}: {step.error.message}" if step.error else None,
                "input": step.input,
                "output": step.output,
                "diff": step.state_diff,
                "llm": [
                    {
                        "model": c.model,
                        "finish": c.finish_reason,
                        "in": c.input_tokens,
                        "out": c.output_tokens,
                        "cost": c.cost_usd,
                        "output": c.output,
                    }
                    for c in step.llm_calls
                ],
                "tools": [
                    {
                        "name": t.tool_name,
                        "args": t.args,
                        "result": t.result,
                        "error": t.error.message if t.error else None,
                    }
                    for t in step.tool_calls
                ],
            }
        )
    return {"report": report.model_dump(mode="json"), "steps": steps, "total_ms": max(view.duration_ms, 1.0)}


_CSS = """
:root{--bg:#fff;--fg:#1d2433;--muted:#667085;--line:#e4e7ec;--card:#f8f9fb;--err:#d92d20;--warn:#dc6803;
--ok:#079455;--info:#1570ef;--bar:#98a2b3}
@media (prefers-color-scheme:dark){:root{--bg:#0f1218;--fg:#e6e9ef;--muted:#98a2b3;--line:#2a303c;--card:#171b23;
--bar:#475467}}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;background:var(--bg);
color:var(--fg)}main{max-width:1100px;margin:0 auto;padding:24px 16px}h1{font-size:20px;margin:0 0 4px}
.muted{color:var(--muted)}.badge{display:inline-block;padding:2px 10px;border-radius:999px;font-weight:700;color:#fff}
.pass{background:var(--ok)}.warn{background:var(--warn)}.fail{background:var(--err)}
.tabs{display:flex;gap:4px;border-bottom:1px solid var(--line);margin:20px 0 12px;flex-wrap:wrap}
.tabs button{border:0;background:none;color:var(--muted);padding:8px 12px;cursor:pointer;font:inherit;
border-bottom:2px solid transparent}.tabs button.on{color:var(--fg);border-color:var(--fg)}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin:10px 0}
.sev-error{border-left:4px solid var(--err)}.sev-warn{border-left:4px solid var(--warn)}
.sev-info{border-left:4px solid var(--info)}code,pre{font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace}
pre{white-space:pre-wrap;word-break:break-word;background:var(--bg);border:1px solid var(--line);border-radius:6px;
padding:8px;max-height:340px;overflow:auto}.row{display:grid;grid-template-columns:minmax(120px,240px) 1fr;
gap:8px;align-items:center;padding:3px 0;cursor:pointer}.row:hover{background:var(--card)}
.track{position:relative;height:16px}.bar{position:absolute;height:16px;border-radius:3px;background:var(--bar);
min-width:2px}.bar.error{background:var(--err)}.bar.warn{background:var(--warn)}.name{overflow:hidden;
text-overflow:ellipsis;white-space:nowrap}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media (max-width:700px){.grid2{grid-template-columns:1fr}.row{grid-template-columns:110px 1fr}}
.hidden{display:none}ul{padding-left:18px;margin:6px 0}
"""

_JS = """
const D=JSON.parse(document.getElementById('tervic-data').textContent);const R=D.report;
const $=s=>document.querySelector(s);const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmt=v=>esc(JSON.stringify(v,null,2));const ms=v=>v>=1000?(v/1000).toFixed(2)+'s':Math.round(v)+'ms';
function tab(name){document.querySelectorAll('.tabs button').forEach(b=>b.classList.toggle('on',b.dataset.t===name));
document.querySelectorAll('section[data-t]').forEach(s=>s.classList.toggle('hidden',s.dataset.t!==name));}
document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>tab(b.dataset.t));
function showStep(id){const s=D.steps.find(x=>x.id===id);if(!s)return;tab('steps');
$('#step').innerHTML=`<div class="card"><b>${esc(s.name)}</b> <span class="muted">${esc(s.kind)} · ${ms(s.duration_ms)}${s.cost?' · $'+s.cost.toFixed(4):''}${s.source?' · '+esc(s.source):''}</span>
${s.error?`<div style="color:var(--err)">${esc(s.error)}</div>`:''}
<div class="grid2"><div><div class="muted">input</div><pre>${fmt(s.input)}</pre></div><div><div class="muted">output</div><pre>${fmt(s.output)}</pre></div></div>
${s.diff?`<div class="muted">state diff</div><pre>${fmt(s.diff)}</pre>`:''}
${s.llm.length?`<div class="muted">LLM calls</div><pre>${fmt(s.llm)}</pre>`:''}
${s.tools.length?`<div class="muted">tool calls</div><pre>${fmt(s.tools)}</pre>`:''}</div>`;}
function waterfall(){const total=D.total_ms;$('#timeline').innerHTML=D.steps.map(s=>{const left=Math.min(99,s.start_ms/total*100);
const w=Math.max(.3,Math.min(100-left,s.duration_ms/total*100));return `<div class="row" onclick="showStep('${s.id}')">
<div class="name" title="${esc(s.name)}">${esc(s.name)} <span class="muted">${ms(s.duration_ms)}</span></div>
<div class="track"><div class="bar ${s.severity||''}" style="left:${left}%;width:${w}%"></div></div></div>`}).join('')||'<p class="muted">No steps recorded.</p>';}
function findings(){const g=R.groups;$('#findings').innerHTML=g.length?g.map((x,i)=>`<div class="card sev-${x.severity}">
<b>${i+1}. ${x.root_cause_step_name?'Root cause: <a href="#" onclick="showStep(\\''+x.root_cause_step_id+'\\');return false">'+esc(x.root_cause_step_name)+'</a>':'Run-level'}</b>
${x.source?`<span class="muted"> ${esc(x.source)}</span>`:''}<ul>${x.findings.map(f=>`<li><code>${f.detector_id}</code> ${esc(f.message)}</li>`).join('')}</ul>
<div class="muted">${esc(x.findings[0].how_to_fix)}</div></div>`).join(''):'<p style="color:var(--ok)">No silent failures found.</p>';}
function baseline(){const d=R.baseline_diff;$('#baseline').innerHTML=d?`<div class="card"><div class="muted">baseline: ${esc(R.baseline)}</div>
<pre>${fmt({steps_removed:d.steps_removed,steps_added:d.steps_added,order_changed:d.order_changed,changes:d.changes,metrics:d.metrics})}</pre></div>`:'<p class="muted">No baseline for this run name. Set one with <code>tervic baseline set &lt;run&gt; --name &lt;name&gt;</code>.</p>';}
waterfall();findings();baseline();tab('findings');
"""


def render(report: Report, view: RunView) -> str:
    payload = json.dumps(_data(report, view), default=str).replace("</", "<\\/")
    stats = report.stats
    title = html.escape(f"Tervic · {report.run_name}")
    summary = (
        f"{stats.steps} steps · {stats.llm_calls} LLM calls · {stats.tool_calls} tool calls · "
        f"{stats.duration_ms / 1000:.2f}s · ${stats.cost_usd:.4f} · "
        f"{report.counts.get('error', 0)} errors, {report.counts.get('warn', 0)} warnings"
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>{_CSS}</style></head>
<body><main>
<h1>{html.escape(report.run_name)} <span class="badge {report.verdict}">{report.verdict.upper()}</span></h1>
<div class="muted">run {html.escape(report.run_id)} · {html.escape(report.framework)} · {html.escape(report.started_at or "")}
{(" · git " + html.escape(report.git_sha[:10])) if report.git_sha else ""}</div>
<div class="muted">{html.escape(summary)}</div>
{f'<div class="card sev-error">Run crashed: {html.escape(report.run_error)}</div>' if report.status == "crashed" and report.run_error else ""}
<nav class="tabs"><button data-t="findings">Findings</button><button data-t="timeline">Timeline</button>
<button data-t="steps">Step inspector</button><button data-t="baseline">Baseline diff</button></nav>
<section data-t="findings"><div id="findings"></div></section>
<section data-t="timeline" class="hidden"><div id="timeline"></div><p class="muted">Click a step to inspect it.</p></section>
<section data-t="steps" class="hidden"><div id="step"><p class="muted">Click a step in the timeline or a root cause.</p></div></section>
<section data-t="baseline" class="hidden"><div id="baseline"></div></section>
<p class="muted" style="margin-top:32px">Generated by Tervic {html.escape(report.tervic_version)} · offline report, no data leaves this file.</p>
</main>
<script id="tervic-data" type="application/json">{payload}</script>
<script>{_JS}</script>
</body></html>
"""
