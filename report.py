"""Kill Point's live page, served from inside a Daytona sandbox on a public preview URL.

Everything on the page is driven by state.json, pushed into this same sandbox by
livesweep.py (the fault matrix) and fixer.py --live (the repair loop). Nothing is
pre-rendered from the persisted sweeps except the interaction-cell note, so what the page
shows is what actually just happened on the machines.

The page polls state.json rather than holding a websocket: the sandbox serves the page
with `python -m http.server`, which cannot upgrade a connection. Smoothness comes from
diffing instead -- a frame whose signature is unchanged repaints nothing, so scroll
position, the pinned defect and the open code panes all survive a poll.
"""
import json, os, sys, time
from dotenv import load_dotenv

load_dotenv("/home/mx/daytona_hacksprint/.env")
from daytona import Daytona, CreateSandboxFromSnapshotParams, SessionExecuteRequest
import fixture as fx

# refund_bot's rows get their hand-written captions; any other fixture is labelled from
# its own crash points, so the page follows the fixture instead of the other way round.
ROW_LABEL = {"clean": "clean", "crash4": "crash@4<br><small>truncated cache</small>",
             "crash5": "crash@5<br><small>half-written ledger</small>"}


def row_label(f, m):
    if m in ROW_LABEL:
        return ROW_LABEL[m]
    if m == "clean":
        return "clean"
    return f"{m}<br><small>killed at step {f.crash_points[m]}</small>"

CSS = """
 *{box-sizing:border-box}
 body{font:15px/1.55 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:30px 34px 60px;
      background:#0d1117;color:#e6edf3}
 h1{margin:0 0 4px;font-size:27px;letter-spacing:-.01em}
 .lede{color:#8b949e;max-width:820px;margin:0 0 24px}
 code{background:#21262d;padding:1px 5px;border-radius:4px;font-size:13px}
 .card{margin:0 0 22px;padding:20px 24px;background:#161b22;border-radius:12px;
       border:1px solid #21262d}
 .head{display:flex;align-items:baseline;gap:13px;flex-wrap:wrap;margin-bottom:15px}
 .head h2{font-size:19px;margin:0}
 .sub{color:#8b949e;font-size:12px;font-family:ui-monospace,monospace}
 .pill{font-size:12px;font-weight:600;padding:3px 10px;border-radius:99px;background:#21262d;
       color:#8b949e}
 .pill.run{background:#1f6feb;color:#fff} .pill.fin{background:#1a7f37;color:#fff}
 .pill.bad{background:#b3261e;color:#fff}

 /* --- before | after boards --- */
 .boards{display:grid;grid-template-columns:1fr 1fr;gap:22px;align-items:start}
 @media(max-width:960px){.boards{grid-template-columns:1fr}}
 .board{background:#0d1117;border:1px solid #21262d;border-radius:9px;padding:14px 16px 12px;
        transition:border-color .3s}
 .board.after{border-color:#1a7f37}
 .board.before{border-color:#5c2320}
 .bh{display:flex;align-items:center;gap:9px;margin-bottom:9px}
 .bh h3{font-size:13px;margin:0;text-transform:uppercase;letter-spacing:.07em;color:#8b949e}
 .tag{font-size:11px;font-weight:700;padding:2px 7px;border-radius:4px}
 .tag.b{background:#5c2320;color:#ffb4ab} .tag.a{background:#12351f;color:#7ee787}
 table{border-collapse:separate;border-spacing:4px;width:100%}
 th{font-size:11px;color:#8b949e;font-weight:600;text-align:center}
 th.rl{text-align:right;white-space:nowrap;padding-right:6px;font-size:11px}
 td{color:#fff;font-size:13px;font-weight:600;text-align:center;padding:15px 4px;
    border-radius:6px;transition:background .35s}
 td.pending{background:#161b22;color:#484f58}
 td.running{background:#21262d;color:#8b949e;animation:pulse 1.1s ease-in-out infinite}
 @keyframes pulse{0%,100%{opacity:.4}50%{opacity:1}}
 small{font-weight:400;opacity:.8}
 .tally{margin-top:9px;font-size:12px;font-family:ui-monospace,monospace;color:#8b949e}
 .tally b.r{color:#ff7b72} .tally b.g{color:#3fb950}
 .delta{margin-top:14px;padding:10px 14px;background:#0d1117;border:1px solid #21262d;
        border-radius:8px;font-size:13px;font-family:ui-monospace,monospace;color:#8b949e;
        display:none}
 .delta.on{display:block} .delta b{color:#e6edf3}

 /* --- findings --- */
 .wrap{display:grid;grid-template-columns:340px 1fr;gap:22px;align-items:start}
 @media(max-width:960px){.wrap{grid-template-columns:1fr}}
 .feedcol{max-height:420px;overflow:auto}
 .f{background:#0d1117;border-left:3px solid #b3261e;border-radius:6px;padding:9px 12px;
    margin-bottom:7px;cursor:pointer;animation:in .3s ease-out}
 .f:hover{background:#151d27;border-left-color:#f0883e}
 .f.sel{background:#1c2431;border-left-color:#f0883e}
 @keyframes in{from{opacity:0;transform:translateY(-5px)}to{opacity:1;transform:none}}
 .f b{color:#ff7b72}
 .f .where{color:#8b949e;font-size:12px;font-family:ui-monospace,monospace}
 .none{color:#6e7681;font-size:13px}
 .hint{color:#6e7681;font-size:12px;margin:0 0 9px}

 /* --- detail / code panes --- */
 #detail{color:#6e7681;font-size:13px}
 #detail h4{margin:0 0 3px;font-size:16px;color:#ff7b72}
 #detail .why{color:#c9d1d9;font-size:14px;margin:6px 0 12px;max-width:820px}
 .kv{color:#8b949e;font-size:12px;font-family:ui-monospace,monospace;margin-bottom:9px}
 .panes{display:grid;grid-template-columns:1fr 1fr;gap:14px}
 @media(max-width:1250px){.panes{grid-template-columns:1fr}}
 h5{margin:0 0 5px;font-size:11px;color:#8b949e;text-transform:uppercase;letter-spacing:.06em}
 h5 .n{color:#484f58}
 pre{margin:0 0 10px;padding:11px 13px;background:#010409;border-radius:6px;font-size:12px;
     line-height:1.5;color:#c9d1d9;overflow:auto;white-space:pre;
     border:1px solid #21262d;max-height:330px}
 pre.wrapln{white-space:pre-wrap}
 pre.bug{border-left:3px solid #b3261e} pre.fix{border-left:3px solid #1a7f37}
 .fn{font-family:ui-monospace,monospace;font-size:11px;color:#6e7681;margin:0 0 3px}
 .x{float:right;color:#6e7681;cursor:pointer;font-size:20px;line-height:1}
 .x:hover{color:#e6edf3}

 /* --- hover preview --- */
 #pop{display:none;position:fixed;z-index:50;width:520px;max-width:46vw;padding:13px 15px;
      background:#161b22;border:1px solid #30363d;border-radius:9px;
      box-shadow:0 14px 40px rgba(0,0,0,.6);pointer-events:none}
 #pop.on{display:block}
 #pop h4{margin:0 0 6px;font-size:13px;color:#ff7b72}
 #pop pre{max-height:150px;font-size:11px;margin-bottom:7px}
 #pop .more{color:#6e7681;font-size:11px}

 /* --- fixer rounds --- */
 .r{background:#0d1117;border:1px solid #30363d;border-radius:9px;padding:14px 17px;
    margin-bottom:12px;animation:in .3s ease-out}
 .r .rh{display:flex;align-items:center;gap:11px;flex-wrap:wrap;margin-bottom:9px}
 .r .rn{font-size:15px;font-weight:700}
 .r .meta{color:#8b949e;font-size:12px;font-family:ui-monospace,monospace}
 .r h5{margin:11px 0 4px}
 .r pre{max-height:220px}
 .chips{display:flex;gap:5px;flex-wrap:wrap;margin-top:5px}
 .chip{font-size:11px;font-weight:600;padding:3px 8px;border-radius:4px;color:#fff}
 .ck{font-size:12px;font-family:ui-monospace,monospace;margin-top:7px}
 .ck .p{color:#3fb950} .ck .fl{color:#ff7b72}
 .note{padding:16px 20px;background:#161b22;border-left:3px solid #b3261e;border-radius:8px;
       max-width:940px}

 /* --- sponsor scoreboard --- */
 .score{display:none;gap:10px;flex-wrap:wrap;margin:0 0 22px}
 .score.on{display:flex}
 .sc{flex:1 1 260px;background:#161b22;border:1px solid #21262d;border-radius:10px;
     padding:11px 15px;border-left:3px solid #30363d}
 .sc.nos{border-left-color:#8957e5} .sc.day{border-left-color:#1f6feb}
 .sc h4{margin:0 0 5px;font-size:11px;letter-spacing:.09em;text-transform:uppercase;
        color:#8b949e;font-weight:700}
 .sc .v{font-size:13px;font-family:ui-monospace,monospace;color:#c9d1d9}
 .sc .v b{color:#fff} .sc .v span{color:#484f58;padding:0 2px}

 /* --- unified diff --- */
 pre.diff{white-space:pre;padding:7px 0;max-height:300px}
 .dl{display:block;padding:0 13px}
 .dl.a{background:#0c2a14;color:#7ee787} .dl.d{background:#3a1417;color:#ff9492}
 .dl.h{background:#161b22;color:#a371f7} .dl.c{color:#8b949e}
 .reveal .dl{animation:dlin .2s ease-out both}
 @keyframes dlin{from{opacity:0;transform:translateX(-7px)}to{opacity:1;transform:none}}
 .stat{font-family:ui-monospace,monospace;font-size:12px;color:#8b949e;margin:0 0 6px}
 .stat b.a{color:#3fb950} .stat b.d{color:#ff7b72} .stat i{color:#6e7681;font-style:normal}
 details.full{margin:0 0 10px}
 details.full summary{cursor:pointer;color:#6e7681;font-size:12px;padding:3px 0}
 details.full summary:hover{color:#c9d1d9}

 /* --- the interaction cell: the whole argument, so mark it --- */
 td.inter{box-shadow:inset 0 0 0 2px #d29922}
 .legend{font-size:11px;color:#6e7681;margin-top:7px}
 .legend i{display:inline-block;width:10px;height:10px;border-radius:3px;
           box-shadow:inset 0 0 0 2px #d29922;vertical-align:-1px;margin-right:5px}

 /* --- projector mode (?big, or the corner button) --- */
 body.big{font-size:19px}
 body.big h1{font-size:34px}
 body.big td{font-size:17px;padding:21px 4px}
 body.big th,body.big th.rl{font-size:14px}
 body.big pre{font-size:15px;max-height:none}
 body.big pre.diff{max-height:none}
 body.big .feedcol{max-height:none}
 body.big .sc .v{font-size:15px} body.big .sub,body.big .tally{font-size:14px}
 #bigbtn{position:fixed;right:15px;bottom:15px;z-index:60;background:#21262d;color:#8b949e;
         border:1px solid #30363d;border-radius:7px;padding:7px 13px;font-size:12px;
         cursor:pointer;font-family:inherit}
 #bigbtn:hover{color:#e6edf3;border-color:#8b949e}
"""

JS = r"""
const MACHINE=__MACHINE__, TRANSPORT=__TRANSPORT__, NCELLS=__NCELLS__;
const RECORD_LABEL=__RECORD_LABEL__, INTERACTION=__INTERACTION__;
const INTER_KEY=INTERACTION[0]+'|'+INTERACTION[1];
const COL={green:'#1a7f37',yellow:'#9a6700',red:'#b3261e'};
let findings=[], sel=-1, lastSig='', findSig='', roundSig={};

const $=i=>document.getElementById(i);
const key=(m,t)=>m+'|'+t;
const esc=s=>String(s==null?'':s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const clip=(s,n)=>{const l=String(s||'').split('\n');
  return l.length<=n?String(s||''):l.slice(0,n).join('\n')+'\n… '+(l.length-n)+' more lines'};

/* ---------- the two boards ---------- */
function fillGrid(pfx,cells,was){
  const by={}; (cells||[]).forEach(c=>by[key(c.machine,c.transport)]=c);
  const wasBy={}; (was||[]).forEach(c=>wasBy[key(c.machine,c.transport)]=c);
  let red=0,green=0,done=0;
  MACHINE.forEach(m=>TRANSPORT.forEach(t=>{
    const k=key(m,t), ring=(k===INTER_KEY?' inter':'');
    const td=$(pfx+'_'+k); if(!td) return;
    const c=by[k];
    if(!c){ td.className='pending'+ring; td.style.background=''; td.innerHTML='&middot;'; return }
    if(c.status==='done'||c.verdict){
      done++; if(c.colour==='green') green++; else red++;
      td.className=ring.trim(); td.style.background=COL[c.colour]||'#21262d';
      // on the AFTER board a flipped cell says what it USED to be, so the repair is
      // legible without looking back and forth between the two grids
      const w=wasBy[k], flip=w&&w.verdict&&w.verdict!==c.verdict;
      td.innerHTML=esc(c.verdict)+'<br><small>'+
        (flip?'was '+esc(w.verdict):(c.refunds||0)+' '+RECORD_LABEL)+'</small>';
    } else { td.className='running'+ring; td.style.background=''; td.innerHTML='running&hellip;' }
  }));
  return {red,green,done};
}

function renderBoards(s){
  const before=s.before||null;
  $('phase').textContent=s.phase||'';
  const a=fillGrid('a',s.cells,before);
  const p=$('pill');
  p.textContent=a.done+' / '+NCELLS+' sandboxes reported';
  p.className='pill '+(s.done?'fin':'run');
  $('alab').textContent = before?'After — the patched agent':'Live sweep';
  $('atag').textContent = before?'after':'live';
  $('atag').className   = 'tag '+(before?'a':'b');
  $('aboard').className = 'board '+(before&&a.red===0&&a.done===NCELLS?'after':'');
  $('a_tally').innerHTML='<b class=g>'+a.green+' green</b> · <b class=r>'+a.red+' broken</b>';
  if(!before){ $('bboard').style.display='none'; $('delta').className='delta'; return }
  $('bboard').style.display='';
  const b=fillGrid('b',before,null);
  $('b_tally').innerHTML='<b class=g>'+b.green+' green</b> · <b class=r>'+b.red+' broken</b>';
  const d=$('delta'); d.className='delta on';
  d.innerHTML='same machine states, same transport faults &nbsp;→&nbsp; '+
    'broken cells <b>'+b.red+'</b> before, <b>'+a.red+'</b> after'+
    (a.done<NCELLS?' &nbsp;(<b>'+a.done+'/'+NCELLS+'</b> reported)':'');
}

/* ---------- sponsor scoreboard: what the two sponsors actually did, in numbers ---------- */
function renderScore(s){
  const fx=s.fix||{}, R=fx.rounds||[];
  const tok=R.reduce((a,r)=>a+(r.tokens||0),0);
  const lat=R.reduce((a,r)=>a+(r.latency||0),0);
  // Sandboxes actually created: the before matrix (or the live one), plus every round's
  // re-sweep, plus the box serving this page. s.cells duplicates the newest round, so it
  // is not added. The breakdown is spelled out because two of those groups -- the before
  // sweep and this page's own box -- are never drawn as boxes further down the page.
  const swept=s.before?s.before.length:(s.cells||[]).filter(c=>c.verdict).length;
  const resw=R.reduce((a,r)=>a+(r.cells||[]).length,0);
  const boxes=swept+resw+1;
  $('score').className='score on';
  $('sc_nos').innerHTML = fx.author==='nosana'
    ? '<b>'+esc(fx.model||'model')+'</b><span>·</span>'+R.length+' round'+(R.length===1?'':'s')+
      '<span>·</span><b>'+tok+'</b> tokens<span>·</span><b>'+lat.toFixed(1)+'s</b> generating'
    : fx.author==='rule'
      ? '<b>rule template</b><span>·</span>model unreachable, the floor held'
      : '<span>no repair run yet</span>';
  $('sc_day').innerHTML=
    '<b>'+swept+'</b> before'+
    (resw?'<span>+</span><b>'+resw+'</b> re-swept ('+R.length+'&times;'+NCELLS+')':'')+
    '<span>+</span><b>1</b> serving this page<span>=</span><b>'+boxes+'</b> sandboxes'+
    '<span>·</span>'+NCELLS+' at a time, in parallel';
}

/* ---------- findings ---------- */
function renderFindings(s){
  const f=s.findings||[], sig=JSON.stringify(f.map(x=>[x.flag,x.machine,x.transport,!!x.code]));
  if(sig===findSig) return;
  if(f.length<findings.length) closeDetail();
  findSig=sig; findings=f;
  const box=$('feed'); box.innerHTML='';
  f.forEach((x,i)=>{
    const d=document.createElement('div');
    d.className='f'+(i===sel?' sel':'');
    d.onclick=()=>select(i);
    d.onmouseenter=e=>showPop(i,d); d.onmouseleave=hidePop;
    d.innerHTML='<b>'+esc(x.flag)+'</b> &mdash; '+esc(x.title)+
      '<div class=where>'+esc(x.machine)+' &times; '+esc(x.transport)+'</div>';
    box.appendChild(d);
  });
  $('nofind').style.display=f.length?'none':'';
  $('fcount').textContent=f.length?f.length+' defects':'nothing found yet';
  $('fcount').className='pill '+(f.length?'bad':'');
  if(sel>=0&&findings[sel]) select(sel);
}

function codeHtml(c,kind,label){
  if(!c||!c.length) return '';
  return '<h5>'+label+'</h5>'+c.map(d=>'<div class=fn>def '+esc(d.name)+'()</div>'+
    '<pre class='+kind+'>'+esc(d.code)+'</pre>').join('');
}

function select(i){
  const x=findings[i]; if(!x) return;
  sel=i;
  document.querySelectorAll('.f').forEach((e,j)=>e.classList.toggle('sel',j===i));
  $('detail').innerHTML=
    '<span class=x onclick="closeDetail()">&times;</span>'+
    '<h4>'+esc(x.flag)+' &mdash; '+esc(x.title)+'</h4>'+
    '<div class=kv>machine='+esc(x.machine)+'   transport='+esc(x.transport)+
      '   exit_code='+esc(x.exit_code)+'   '+RECORD_LABEL+' records='+esc(x.refunds)+'</div>'+
    '<p class=why>'+esc(x.why)+'</p>'+
    '<div class=panes><div>'+
      '<h5><span class=n>1.</span> first output &mdash; files on disk after the run</h5>'+
      '<pre class="bug wrapln">'+esc(x.evidence||'(no autopsy captured)')+'</pre>'+
      codeHtml(x.code&&x.code.bug,'bug','<span class=n>2.</span> the code that did it')+
    '</div><div>'+
      (x.code&&x.code.fix&&x.code.fix.length
        ? codeHtml(x.code.fix,'fix','<span class=n>3.</span> what the fix changed')
        : '<h5><span class=n>3.</span> what the fix changed</h5>'+
          '<pre class=wrapln>no patch yet &mdash; run the repair loop</pre>')+
    '</div></div>';
}
function closeDetail(){ sel=-1;
  document.querySelectorAll('.f').forEach(e=>e.classList.remove('sel'));
  $('detail').innerHTML='<span class=hint>click a defect to pin its evidence here</span>'; }

/* ---------- hover preview ---------- */
function showPop(i,el){
  const x=findings[i]; if(!x) return;
  const p=$('pop');
  p.innerHTML='<h4>'+esc(x.flag)+' &mdash; '+esc(x.title)+'</h4>'+
    '<h5>first output</h5><pre class=wrapln>'+esc(clip(x.evidence,7)||'(none)')+'</pre>'+
    (x.code&&x.code.fix&&x.code.fix.length
      ? '<h5>the fix &mdash; def '+esc(x.code.fix[0].name)+'()</h5><pre class=fix>'+
        esc(clip(x.code.fix[0].code,9))+'</pre>'
      : '')+
    '<div class=more>click to pin the full evidence and diff below</div>';
  p.className='on';
  const r=el.getBoundingClientRect(), h=p.offsetHeight;
  p.style.left=Math.min(r.right+14, innerWidth-p.offsetWidth-14)+'px';
  p.style.top=Math.max(10, Math.min(r.top, innerHeight-h-10))+'px';
}
function hidePop(){ $('pop').className='' }

/* ---------- the diff: what the model actually changed ---------- */
function diffHtml(t){
  return String(t||'').split('\n').map((l,i)=>{
    const c = l[0]==='+' ? 'a' : l[0]==='-' ? 'd' : l.slice(0,2)==='@@' ? 'h' : 'c';
    // staggered delay = the diff types itself in on first paint, capped so a long
    // rewrite does not take ten seconds to finish appearing
    return '<span class="dl '+c+'" style="animation-delay:'+Math.min(i*15,900)+'ms">'+
           (esc(l)||' ')+'</span>';
  }).join('');
}

function statHtml(d){
  const fns=[];
  if(d.fn_added&&d.fn_added.length)   fns.push('new: '+d.fn_added.map(esc).join(', '));
  if(d.fn_changed&&d.fn_changed.length)fns.push('rewritten: '+d.fn_changed.map(esc).join(', '));
  if(d.fn_removed&&d.fn_removed.length)fns.push('removed: '+d.fn_removed.map(esc).join(', '));
  return '<div class=stat><b class=a>+'+d.added+'</b> <b class=d>&minus;'+d.removed+'</b>'+
         (fns.length?'<i>  &middot;  '+fns.join('  &middot;  ')+'</i>':'')+'</div>';
}

/* ---------- fixer rounds (incremental: only a changed round repaints) ---------- */
function renderFix(s){
  const fx=s.fix; if(!fx||(!fx.rounds.length&&!fx.active)) return;
  $('fix').style.display='block';
  const p=$('fixpill');
  p.textContent = fx.fixed ? ('all '+NCELLS+' cells green in '+
                    (fx.total_rounds||fx.rounds.length)+' round(s)')
                : fx.active ? 'repairing…' : 'not fixed';
  p.className='pill '+(fx.fixed?'fin':fx.active?'run':'bad');
  $('fixsrc').textContent = fx.author==='nosana'
    ? 'patch author: '+(fx.model||'model')+' on Nosana' : 'patch author: rule template';
  const box=$('rounds');
  fx.rounds.forEach(r=>{
    const sig=JSON.stringify(r);
    let el=$('r'+r.n);
    if(el&&roundSig[r.n]===sig) return;            // unchanged frame: do not touch the DOM
    roundSig[r.n]=sig;
    const scrolls={};                              // keep the reader where they were
    if(el) el.querySelectorAll('pre').forEach((q,i)=>scrolls[i]=[q.scrollTop,
      q.scrollHeight-q.scrollTop-q.clientHeight<4]);
    const fresh=!el;                               // first paint of this round: animate it
    if(!el){ el=document.createElement('div'); el.className='r'; el.id='r'+r.n;
             box.appendChild(el) }
    const meta=[r.latency!=null?r.latency+'s':null, r.tokens!=null?r.tokens+' tokens':null]
      .filter(Boolean).join('  ·  ');
    const chips=(r.cells||[]).map(c=>'<span class=chip style="background:'+
      (COL[c.colour]||'#21262d')+'">'+esc(c.machine)+'×'+esc(c.transport)+' '+
      esc(c.verdict)+'</span>').join('');
    const checks=r.checks?Object.entries(r.checks).map(([k,v])=>
      '<div class=ck><span class="'+(v?'p':'fl')+'">'+(v?'PASS':'FAIL')+'</span>  '+esc(k)+
      '</div>').join(''):'';
    el.innerHTML='<div class=rh><span class=rn>Round '+r.n+'</span>'+
      '<span class="pill '+(r.fixed?'fin':r.status==='done'?'bad':'run')+'">'+
        esc(r.status)+'</span><span class=meta>'+meta+'</span></div>'+
      (r.evidence?'<h5><span class=n>1.</span> what the classifier sent the model</h5>'+
        '<pre class=wrapln>'+esc(r.evidence)+'</pre>':'')+
      (r.patch?'<h5><span class=n>2.</span> what the model changed &mdash; '+
        (r.n>1?'vs. round '+(r.n-1):'vs. the buggy agent')+'</h5>'+
        (r.diff
          // the diff is the answer to "what did the model change"; the whole file is
          // still one click away. A recording made before diffs existed shows the file.
          ? statHtml(r.diff)+'<pre class="diff'+(fresh?' reveal':'')+'">'+
            diffHtml(r.diff.text)+'</pre>'+
            '<details class=full><summary>show the whole file the model returned ('+
            r.patch.split('\n').length+' lines)</summary><pre>'+esc(r.patch)+'</pre></details>'
          : '<pre>'+esc(r.patch)+'</pre>'):'')+
      (r.smoke?'<h5><span class=n>3.</span> local smoke test</h5><div class=ck><span class="'+
        (r.smoke==='passed'?'p':'fl')+'">'+esc(r.smoke)+'</span></div>'+
        (r.smoke_evidence?'<pre class=wrapln>'+esc(r.smoke_evidence)+'</pre>':''):'')+
      (chips?'<h5><span class=n>4.</span> re-swept on '+NCELLS+' fresh machines</h5>'+
        '<div class=chips>'+chips+'</div>':'')+
      checks;
    el.querySelectorAll('pre').forEach((q,i)=>{
      const st=scrolls[i];
      if(q.classList.contains('diff')) return;     // a diff reads from the top, not the tail
      if(!st) q.scrollTop=q.scrollHeight;          // new pane: newest output at the bottom
      else q.scrollTop=st[1]?q.scrollHeight:st[0]; // was following the tail? keep following
    });
  });
}

async function poll(){
  try{
    const r=await fetch('state.json?t='+Date.now(),{cache:'no-store'});
    if(r.ok){
      const txt=await r.text();
      if(txt!==lastSig){                           // identical frame: repaint nothing
        lastSig=txt; const s=JSON.parse(txt);
        $('live').style.display='block'; $('findcard').style.display='block';
        renderScore(s); renderBoards(s); renderFindings(s); renderFix(s);
      }
    }
  }catch(e){}
  setTimeout(poll,700);
}
/* ---------- projector mode: 12px monospace is unreadable from the back of a room ---- */
function big(on){
  document.body.classList.toggle('big',on);
  $('bigbtn').textContent=on?'normal size':'projector size';
  try{ localStorage.setItem('kp_big',on?'1':'0') }catch(e){}
}
function toggleBig(){ big(!document.body.classList.contains('big')) }
big(new URLSearchParams(location.search).has('big') ||
    (function(){ try{ return localStorage.getItem('kp_big')==='1' }catch(e){ return false } })());

closeDetail(); poll();
"""


def live_grid(f, pfx):
    """Empty grid the feed fills in. pfx: 'b' (before) or 'a' (after/live)."""
    h = ['<table>', "<tr><th></th>" + "".join(f"<th>{t}</th>" for t in f.transports) + "</tr>"]
    for m in f.machines:
        h.append(f"<tr><th class='rl'>{row_label(f, m)}</th>")
        for t in f.transports:
            ring = " inter" if (m, t) == f.interaction else ""
            h.append(f'<td class="pending{ring}" id="{pfx}_{m}|{t}">&middot;</td>')
        h.append("</tr>")
    h.append("</table>")
    return "".join(h)


def build(before, f=None):
    f = f or fx.default()
    im, it = f.interaction
    by = {(r["machine"], r["transport"]): r for r in before}
    inter = by[(im, it)]
    n = len(f.machines) * len(f.transports)
    js = (JS.replace("__MACHINE__", json.dumps(f.machines))
            .replace("__TRANSPORT__", json.dumps(f.transports))
            .replace("__NCELLS__", str(n))
            .replace("__RECORD_LABEL__", json.dumps(f.record_label))
            .replace("__INTERACTION__", json.dumps([im, it])))
    return f"""<!doctype html><meta charset=utf-8><title>Kill Point</title>
<style>{CSS}</style>
<h1>Kill Point</h1>
<p class=lede>Agent reliability across the <b>transport &times; machine</b> fault matrix.
Rows are machine states a previous run left behind &mdash; each needs a real, disposable
machine. Columns are transport faults. One disposable Daytona sandbox per cell, all in parallel.</p>

<div class=score id=score>
  <div class="sc nos"><h4>Nosana &mdash; the fixer</h4><div class=v id=sc_nos></div></div>
  <div class="sc day"><h4>Daytona &mdash; the machines</h4><div class=v id=sc_day></div></div>
</div>

<div class=card id=live style="display:none">
  <div class=head><h2 id=phase></h2><span class=pill id=pill></span></div>
  <div class=boards>
    <div class=board id=bboard style="display:none">
      <div class=bh><h3>Before &mdash; the buggy agent</h3><span class="tag b">before</span></div>
      {live_grid(f, 'b')}
      <div class=tally id=b_tally></div>
      <div class=legend><i></i>{im} &times; {it} &mdash; the cross-product cell nobody tests</div>
    </div>
    <div class=board id=aboard>
      <div class=bh><h3 id=alab>Live sweep</h3><span class="tag b" id=atag>live</span></div>
      {live_grid(f, 'a')}
      <div class=tally id=a_tally></div>
      <div class=legend><i></i>{im} &times; {it} &mdash; the cross-product cell nobody tests</div>
    </div>
  </div>
  <div class=delta id=delta></div>
</div>

<div class=card id=findcard style="display:none">
  <div class=head><h2>What it found</h2><span class=pill id=fcount></span>
    <span class=sub>hover a defect for a preview &middot; click to pin it</span></div>
  <div class=wrap>
    <div class=feedcol>
      <p class=none id=nofind>no defects reported yet</p>
      <div id=feed></div>
    </div>
    <div id=detail></div>
  </div>
</div>

<div class=card id=fix style="display:none">
  <div class=head><h2>Repairing it</h2><span class=pill id=fixpill></span>
    <span class=sub id=fixsrc></span></div>
  <p class=none style="margin:-6px 0 12px">The deterministic classifier hands the model the
  wreckage plus what each verdict requires; the model returns a whole file; it is smoke-tested
  locally, then re-swept on nine fresh machines. That result is the next round's input.</p>
  <div id=rounds></div>
</div>

<div class=note><b>The interaction cell &mdash;
<code>{im} &times; {it}</code>:</b> a torn record left by the previous crash, plus a
malformed receipt from the tool. The retry appends onto the torn record, so the log ends
up with <b>{inter.get('refunds',0)} {f.record_label} records</b> and a receipt that is not
a URL &mdash; and nothing is written to <code>{f.err_file}</code>. Verdict
<code>{inter['verdict']}</code>. Neither axis alone produces it:
<code>clean &times; {it}</code> is <code>{by[('clean', it)]['verdict']}</code> only,
<code>{im} &times; {f.transports[0]}</code> never sees the bad receipt
(<code>{by[(im, f.transports[0])]['verdict']}</code>).</div>

<button id=bigbtn onclick="toggleBig()">projector size</button>
<div id=pop></div>
<script>{js}</script>
"""


def deploy(html, refresh=True):
    """Put the page online and return (url, sandbox).

    refresh: re-upload onto the box named in report_url.json and re-sign its URL (the
    signature dies after 3600s). Falls through to creating a box when there is none.
    Callers get the sandbox back so they can push state.json into the same machine."""
    d = Daytona()
    if refresh and os.path.exists("report_url.json"):
        sb = d.get(json.load(open("report_url.json"))["sandbox_id"])
    else:
        # auto_stop_interval=0 -> never idle-stop. This box must outlive the demo.
        sb = d.create(CreateSandboxFromSnapshotParams(auto_stop_interval=0))
        print("report sandbox:", sb.id)
        sb.process.create_session("web")
        sb.process.execute_session_command(
            "web", SessionExecuteRequest(command="python -m http.server 3000",
                                         run_async=True))
    sb.fs.upload_file(html.encode(), "index.html")   # index.html: http.server would
    time.sleep(3)                                    # otherwise serve a directory listing
    url = sb.create_signed_preview_url(3000, expires_in_seconds=3600).url
    json.dump({"sandbox_id": sb.id, "url": url, "generated": time.strftime("%H:%M:%S")},
              open("report_url.json", "w"), indent=2)
    return url, sb


if __name__ == "__main__":
    ap = __import__("argparse").ArgumentParser()
    ap.add_argument("--fixture", default="refund_bot")
    ap.add_argument("--results", default=None,
                    help="before-matrix results file (default: the fixture's own)")
    ap.add_argument("--local", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    a, _ = ap.parse_known_args()

    f = fx.get(a.fixture)
    results = a.results or ("results.json" if f.name == "refund_bot"
                            else f"results_{f.name}.json")
    html = build(json.load(open(results)), f)
    print(f"fixture: {f.name}   before-matrix: {results}")
    open("report.html", "w").write(html)
    print(f"wrote report.html ({len(html)} bytes)")

    if "--local" in sys.argv:
        sys.exit(0)

    refresh = "--refresh" in sys.argv
    url, sb = deploy(html, refresh=refresh)
    print(("\nREFRESHED URL: " if refresh else "\nPREVIEW URL: ") + url)
    print("expires in 3600s -- regenerate with: python report.py --refresh")
