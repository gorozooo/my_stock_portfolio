/* holdings.js v201
   - 横スワイプ（translateX 固定方式）
   - is-open: 開いた行を固定
   - ボタン（詳細/編集/削除）すべて正常動作
   - 詳細開いたあとにカードタップで閉じる
   - スパーク描画 & モーダル（7/30/90 × 指数/実値）
*/

(() => {
  const NS='__swipe_v201__';
  const START_SLOP=8;
  const GUARD_MS=280;
  const now = () => Date.now();

  /* ---------- スワイプ ---------- */
  function closeAll(except){
    document.querySelectorAll('[data-swipe].is-open').forEach(r=>{
      if (r===except) return;
      r.classList.remove('is-open');
    });
  }

  function bindRow(row){
    if (!row || row[NS]) return; row[NS]=true;

    const actions = row.querySelector('.actions');
    const track   = row.querySelector('.track');
    const detail  = row.querySelector('[data-action="detail"]');
    if (!actions || !track) return;

    let guardUntil = 0;

    // パネル内イベントは伝播させない（削除/編集/詳細ボタンが効くように）
    actions.addEventListener('click', e => e.stopPropagation(), {capture:true});
    actions.addEventListener('touchstart', e => e.stopPropagation(), {passive:true});

    // 詳細トグル
    if (detail){
      detail.addEventListener('click', (e)=>{
        e.stopPropagation();
        row.classList.toggle('show-detail');
        row.classList.remove('is-open');
        guardUntil = now() + GUARD_MS;
      });
    }

    // カード本体タップ → パネル/詳細を閉じる
    track.addEventListener('click', ()=>{
      if (now() < guardUntil) return;
      if (row.classList.contains('is-open')){
        row.classList.remove('is-open');
        guardUntil = now() + GUARD_MS;
      } else if (row.classList.contains('show-detail')){
        row.classList.remove('show-detail');
      }
    });

    // 外側タップで全閉
    document.addEventListener('click', (e)=>{
      if (now() < guardUntil) return;
      if (!e.target.closest('[data-swipe]')) closeAll();
    });

    let sx=0, sy=0, drag=false, horiz=false, dx=0;

    const follow = (dist) => {
      const w = actions.getBoundingClientRect().width || 220;
      const clamped = Math.max(-w, Math.min(0, dist));
      actions.style.transition = 'none';
      actions.style.transform  = `translateX(${100 + (clamped / w) * 100}%)`;
      if (clamped < -12) actions.style.pointerEvents = 'auto';
    };

    const snap = (open) => {
      actions.style.transition = '';
      if (open){ closeAll(row); row.classList.add('is-open'); }
      else { row.classList.remove('is-open'); }
      actions.style.transform = '';
      guardUntil = now() + GUARD_MS;
    };

    track.addEventListener('touchstart', (e)=>{
      if (e.target.closest('.actions')) return;
      if (!row.classList.contains('is-open')) closeAll(row);
      const t=e.touches[0]; sx=t.clientX; sy=t.clientY; dx=0;
      drag=true; horiz=false;
    }, {passive:true});

    track.addEventListener('touchmove', (e)=>{
      if(!drag) return;
      const t=e.touches[0];
      const mx=t.clientX-sx, my=t.clientY-sy;
      if(!horiz){
        if(Math.abs(mx)<START_SLOP) return;
        if(Math.abs(mx)>Math.abs(my)) horiz=true; else { drag=false; return; }
      }
      e.preventDefault();
      dx = row.classList.contains('is-open') ? mx : -mx;
      follow(-dx);
    }, {passive:false});

    track.addEventListener('touchend', ()=>{
      if(!drag) return; drag=false;
      const w=actions.getBoundingClientRect().width||220;
      const open = (-dx) > (w*0.35);
      snap(open);
    });
  }

  function bindAllRows(){ document.querySelectorAll('[data-swipe]').forEach(bindRow); }

  /* ---------- スパーク描画（カード内） ---------- */
  function drawInlineSpark(svg){
    let arr = [];
    try{ arr = JSON.parse(svg.getAttribute('data-s30i')||'[]'); }catch(_){}
    if(!arr || arr.length < 2){ svg.replaceChildren(); return; }
    const rate = parseFloat(svg.getAttribute('data-rate')||'0');
    const stroke = (isFinite(rate) && rate<0) ? '#f87171' : '#34d399';
    const vb = svg.viewBox.baseVal;
    const W = vb && vb.width ? vb.width : 96;
    const H = vb && vb.height ? vb.height : 24;
    const pad=1;
    const min = Math.min(...arr), max = Math.max(...arr);
    const nx=i => pad + (i*(W-2*pad)/(arr.length-1));
    const ny=v => H - pad - ((v-min)/(max-min||1e-6))*(H-2*pad);
    let d=`M${nx(0)},${ny(arr[0])}`;
    for(let i=1;i<arr.length;i++){ d+=` L${nx(i)},${ny(arr[i])}`; }
    svg.innerHTML = `<path d="${d}" fill="none" stroke="${stroke}" stroke-width="1.6" stroke-linecap="round" />`;
  }
  function drawAllInlineSparks(){ document.querySelectorAll('svg.spark').forEach(drawInlineSpark); }

  /* ---------- スパークモーダル ---------- */
  const modal = {
    root: null, canvas: null, ctx: null, title: null,
    span: 7, mode: 'idx',
    data: null, color: '#34d399'
  };

  function openSparkModal(svg){
    modal.root   = document.getElementById('sparkModal');
    modal.canvas = document.getElementById('sparkCanvas');
    modal.title  = document.getElementById('sparkTitle');
    modal.ctx    = modal.canvas.getContext('2d');

    const rate = parseFloat(svg.getAttribute('data-rate')||'0');
    modal.color = (isFinite(rate) && rate<0) ? '#f87171' : '#34d399';

    const reads = (k)=>{ try{return JSON.parse(svg.getAttribute(k)||'[]')}catch(_){return[]} };
    modal.data = {
      '7':  { idx: reads('data-s7i'),  raw: reads('data-s7r')  },
      '30': { idx: reads('data-s30i'), raw: reads('data-s30r') },
      '90': { idx: reads('data-s90i'), raw: reads('data-s90r') },
    };
    modal.span = (modal.data['7'].idx?.length ? 7 : (modal.data['30'].idx?.length ? 30 : 90));
    modal.mode = 'idx';

    modal.root.classList.add('is-open');
    renderSparkModal();
  }

  function renderSparkModal(){
    const d = modal.data[String(modal.span)][modal.mode] || [];
    const cvs = modal.canvas, ctx = modal.ctx;
    if (!ctx){ return; }
    cvs.width = cvs.clientWidth; cvs.height = cvs.clientHeight;
    ctx.clearRect(0,0,cvs.width,cvs.height);

    if (!d || d.length < 2){ return; }
    let min=Math.min(...d), max=Math.max(...d);
    if (min===max){ min-=1e-6; max+=1e-6; }
    const pad=8, W=cvs.width, H=cvs.height;
    const nx=i => pad + (i*(W-2*pad)/(d.length-1));
    const ny=v => H - pad - ((v-min)/(max-min))*(H-2*pad);

    ctx.strokeStyle = 'rgba(255,255,255,.25)';
    ctx.setLineDash([3,3]); ctx.beginPath();
    ctx.moveTo(pad, ny((modal.mode==='idx')?1:(min+(max-min)/2)));
    ctx.lineTo(W-pad, ny((modal.mode==='idx')?1:(min+(max-min)/2)));
    ctx.stroke(); ctx.setLineDash([]);

    ctx.strokeStyle = modal.color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(nx(0), ny(d[0]));
    for(let i=1;i<d.length;i++){ ctx.lineTo(nx(i), ny(d[i])); }
    ctx.stroke();

    modal.title.textContent = `${modal.span}日 / ${modal.mode==='idx'?'指数':'実値'}`;
  }

  function bindSparkModal(){
    const root = document.getElementById('sparkModal');
    if (!root) return;

    root.addEventListener('click', (e)=>{
      if (e.target.id === 'sparkClose' || e.target.id === 'sparkModal'){
        root.classList.remove('is-open');
      }
    });
    root.querySelectorAll('.spark-ctrl .btn[data-span]').forEach(btn=>{
      btn.addEventListener('click', ()=>{
        root.querySelectorAll('.spark-ctrl .btn[data-span]').forEach(b=>b.classList.remove('is-on'));
        btn.classList.add('is-on');
        modal.span = parseInt(btn.getAttribute('data-span'),10);
        renderSparkModal();
      });
    });
    root.querySelectorAll('.spark-ctrl .btn[data-mode]').forEach(btn=>{
      btn.addEventListener('click', ()=>{
        root.querySelectorAll('.spark-ctrl .btn[data-mode]').forEach(b=>b.classList.remove('is-on'));
        btn.classList.add('is-on');
        modal.mode = btn.getAttribute('data-mode');
        renderSparkModal();
      });
    });

    document.addEventListener('click', (e)=>{
      const svg = e.target.closest && e.target.closest('svg.spark');
      if (svg){ openSparkModal(svg); }
    });
  }

  /* ---------- Boot ---------- */
  function boot(){
    bindAllRows();
    drawAllInlineSparks();
    bindSparkModal();
  }

  window.addEventListener('load', boot);
  document.body.addEventListener('htmx:load', boot);
  window.addEventListener('resize', ()=>{ drawAllInlineSparks(); });

  console.log('[holdings.js v201] ready');
})();