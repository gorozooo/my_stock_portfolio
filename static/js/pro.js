(function(){
  const $  = (s,r=document)=>r.querySelector(s);
  const $$ = (s,r=document)=>[...r.querySelectorAll(s)];
  const fmtP = v => (v==null? '—' : (v>=0? '+' : '') + (v*100).toFixed(2) + '%');
  const fmtJPY = v => '¥' + Math.round(v||0).toLocaleString('ja-JP');

  // LIVE clock
  function tickLive(){
    const el = $('#liveTs'); if(!el) return;
    const d = new Date();
    el.textContent = `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}:${String(d.getSeconds()).padStart(2,'0')}`;
  }
  setInterval(tickLive, 1000); tickLive();

  // ① ベンチ比較 + 最大DD
  async function loadBench(){
    const r = await fetch('/api/bench-dd/');
    const j = await r.json();
    const box = $('#benchCards'); if(!box) return;
    box.innerHTML = '';

    // Portfolio
    const me = document.createElement('div');
    me.className = 'bench-card';
    me.innerHTML = `
      <div><div class="muted">Portfolio TWR</div><strong>${fmtP(j.portfolio.twr)}</strong></div>
      <div><div class="muted">Max DD</div><strong>${fmtP(j.portfolio.maxdd)}</strong></div>`;
    box.appendChild(me);

    // Benches
    Object.entries(j.bench||{}).forEach(([name, m])=>{
      const el = document.createElement('div');
      el.className = 'bench-card';
      el.innerHTML = `
        <div><div class="muted">${name} TWR</div><strong>${fmtP(m.twr)}</strong></div>
        <div><div class="muted">Max DD</div><strong>${fmtP(m.maxdd)}</strong></div>`;
      box.appendChild(el);
    });
  }

  // ② セクター乖離
  async function loadDrift(){
    const r = await fetch('/api/sector-drift/');
    const j = await r.json();
    const box = $('#sectorDrift'); if(!box) return;
    box.innerHTML = '';
    (j.items||[]).slice(0,10).forEach(it=>{
      const el = document.createElement('div');
      el.className = 'drift-item';
      const diff = it.diff || 0;
      const width = Math.min(100, Math.abs(diff));
      const color = diff >= 0 ? 'linear-gradient(90deg,var(--accent),var(--primary))'
                              : 'linear-gradient(90deg,var(--danger),#ff9aa6)';
      el.innerHTML = `
        <div><strong>${it.sector}</strong> <span class="muted">current ${it.current.toFixed(1)}% / target ${it.target.toFixed(1)}%</span></div>
        <div class="bar"><div class="fill" style="width:${width}%;background:${color}"></div></div>
        <div class="${diff>=0?'pos':'neg'}" style="margin-top:4px">${diff>=0?'+':''}${diff.toFixed(2)}%</div>
      `;
      box.appendChild(el);
    });
  }

  // ③ 日次アトリビューション
  async function loadAttr(){
    const r = await fetch('/api/attr-daily/');
    const j = await r.json();
    const box = $('#attrList'); if(!box) return;
    box.innerHTML = '';
    (j.items||[]).forEach(it=>{
      const el = document.createElement('div');
      el.className = 'attr-item';
      const v = it.contribution || 0;
      el.innerHTML = `
        <div><strong>${it.sector}</strong></div>
        <div class="${v>=0?'pos':'neg'}">${v>=0?'+':''}${fmtJPY(v)}</div>
      `;
      box.appendChild(el);
    });
  }

  async function init(){
    await Promise.all([loadBench(), loadDrift(), loadAttr()]);
  }
  document.addEventListener('DOMContentLoaded', init);
})();