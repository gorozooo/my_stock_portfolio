/* holdings.js v121-fix — iOS Safari安定版 + Spark Modal
   - スワイプ固定維持（translateX + .is-open）
   - 詳細/編集/削除 すべて動作（パネル内バブリング停止）
   - 「詳細開いた後、カードタップで閉じる」を最優先で実装
   - 削除連打防止
   - カード内スパーク描画
   - スパークタップでモーダル（7/30/90 × 指数/実値）
*/
(() => {
  const START_SLOP = 8;
  const THRESHOLD  = 0.35;
  const GUARD_MS   = 280;
  const now = () => Date.now();

  /* ===== 共通 ===== */
  function closeAll(except){
    document.querySelectorAll('[data-swipe].is-open').forEach(row=>{
      if (row !== except) row.classList.remove('is-open');
    });
  }
  function widthOf(el){
    const r = el.getBoundingClientRect();
    return r.width || parseFloat(getComputedStyle(el).getPropertyValue('--open-w')) || 220;
  }

  /* ===== 1行バインド ===== */
  function bindRow(row){
    if (!row || row.__bound_v121fix) return;
    row.__bound_v121fix = true;

    const actions   = row.querySelector('.actions');
    const track     = row.querySelector('.track');
    const btnDetail = row.querySelector('[data-action="detail"]');
    const btnDelete = row.querySelector('.item.delete');
    if (!actions || !track) return;

    let guardUntil = 0;

    // アクション内：クリック/タッチは確実に効かせる（HTMXを殺さない）
    actions.addEventListener('click', e => { e.stopPropagation(); }, {capture:false});
    actions.addEventListener('touchstart', e => { e.stopPropagation(); }, {passive:true});

    // 詳細トグル：開閉＋パネルは閉じる
    if (btnDetail){
      btnDetail.addEventListener('click', e=>{
        e.stopPropagation();
        row.classList.toggle('show-detail');
        row.classList.remove('is-open');
        guardUntil = now() + GUARD_MS;
      });
    }

    // 削除：二度押し防止（HTMX応答までbusy）
    if (btnDelete){
      btnDelete.addEventListener('click', e=>{
        if (btnDelete.dataset.busy === '1'){
          e.preventDefault();
          return;
        }
        btnDelete.dataset.busy = '1';
        const reset = ()=>{ btnDelete.dataset.busy = '0'; };
        document.body.addEventListener('htmx:afterOnLoad', reset, {once:true});
        document.body.addEventListener('htmx:responseError', reset, {once:true});
      });
    }

    // カードタップ：詳細 → 無条件クローズ、パネル → ガードありでクローズ
    track.addEventListener('click', (e)=>{
      if (e.target.closest('.actions, .item, a, button, input, select, textarea, label')) return;
      if (row.classList.contains('show-detail')){
        row.classList.remove('show-detail');
        return;
      }
      if (row.classList.contains('is-open')){
        if (now() < guardUntil) return;
        row.classList.remove('is-open');
        guardUntil = now() + GUARD_MS;
      }
    });

    // 外側タップで全閉
    document.addEventListener('click', (e)=>{
      if (now() < guardUntil) return;
      if (!e.target.closest('[data-swipe]')) closeAll();
    });

    // ===== タッチスワイプ（追従はactions、確定はクラス） =====
    let sx=0, sy=0, dragging=false, horiz=false, baseOpen=false, openPull=0, closePush=0;

    function follow(dist){
      const w = widthOf(actions);
      const clamped = Math.max(-w, Math.min(0, dist));
      actions.style.transition = 'none';
      const pct = 100 + (clamped / w) * 100; // [-w..0] → [0..100]
      actions.style.transform = `translateX(${pct}%)`;
      actions.style.pointerEvents = (pct < 85) ? 'auto' : 'none';
    }
    function snap(open){
      actions.style.transition = '';
      actions.style.transform  = '';
      if (open){
        closeAll(row);
        row.classList.add('is-open');
      }else{
        row.classList.remove('is-open');
      }
      guardUntil = now() + GUARD_MS;
    }

    track.addEventListener('touchstart', (e)=>{
      if (e.target.closest('.actions')) return;
      if (!row.classList.contains('is-open')) closeAll(row);
      const t = e.touches[0]; sx=t.clientX; sy=t.clientY;
      dragging=true; horiz=false; baseOpen=row.classList.contains('is-open');
      openPull=0; closePush=0;
    }, {passive:true});

    track.addEventListener('touchmove', (e)=>{
      if (!dragging) return;
      const t=e.touches[0], dx=t.clientX-sx, dy=t.clientY-sy;
      if (!horiz){
        if (Math.abs(dx) < START_SLOP) return;
        if (Math.abs(dx) > Math.abs(dy)) horiz=true; else { dragging=false; return; }
      }
      e.preventDefault(); // iOS 縦スクロール抑止
      const w = widthOf(actions);
      if (!baseOpen){
        openPull = Math.max(0, -dx);
        follow(-openPull);
      }else{
        closePush = Math.max(0, dx);
        follow(-w + closePush);
      }
    }, {passive:false});

    track.addEventListener('touchend', ()=>{
      if (!dragging) return; dragging=false;
      const w = widthOf(actions);
      if (!baseOpen){
        snap(openPull > w * THRESHOLD);
      }else{
        const shouldClose = closePush > w * THRESHOLD;
        snap(!shouldClose);
      }
    });
  }

  function bindAllRows(){ document.querySelectorAll('[data-swipe]').forEach(bindRow); }

  /* ===== カード内スパーク描画（既定：30日・指数） ===== */
  function drawInlineSpark(svg){
    let arr = [];
    try{
      arr = JSON.parse(svg.getAttribute('data-s30i')||'[]');
    }catch(_){}
    if (!Array.isArray(arr) || arr.length < 2){ svg.replaceChildren(); return; }
    const rate   = parseFloat(svg.getAttribute('data-rate')||'0');
    const stroke = (isFinite(rate) && rate < 0) ? '#ef4444' : '#22c55e';
    const vb = svg.viewBox.baseVal || {width:96, height:24};
    const W = vb.width  || 96, H = vb.height || 24, pad = 1;
    let min = Math.min(...arr), max = Math.max(...arr);
    if (min === max){ min -= 1e-6; max += 1e-6; }
    const nx = i => pad + (i*(W-2*pad)/(arr.length-1));
    const ny = v => H - pad - ((v-min)/(max-min))*(H-2*pad);
    let d = `M${nx(0)},${ny(arr[0])}`;
    for (let i=1;i<arr.length;i++){ d += ` L${nx(i)},${ny(arr[i])}`; }
    svg.innerHTML = `<path d="${d}" fill="none" stroke="${stroke}" stroke-width="1.6" stroke-linecap="round" />`;
  }
  function drawAllInlineSparks(){ document.querySelectorAll('svg.spark').forEach(drawInlineSpark); }

  /* ===== スパークモーダル ===== */
  const modal = {
    root:null, canvas:null, ctx:null, title:null,
    span:30, mode:'idx', data:null, color:'#22c55e'
  };

  function openSparkModal(svg){
    modal.root   = document.getElementById('sparkModal');
    if (!modal.root) return; // モーダルDOMがないページでは何もしない
    modal.canvas = document.getElementById('sparkCanvas');
    modal.title  = document.getElementById('sparkTitle');
    modal.ctx    = modal.canvas.getContext('2d');

    const rate = parseFloat(svg.getAttribute('data-rate')||'0');
    modal.color = (isFinite(rate) && rate < 0) ? '#ef4444' : '#22c55e';

    const reads = k => { try{return JSON.parse(svg.getAttribute(k)||'[]');}catch(_){return[];} };
    modal.data = {
      '7':  { idx:reads('data-s7i'),  raw:reads('data-s7r')  },
      '30': { idx:reads('data-s30i'), raw:reads('data-s30r') },
      '90': { idx:reads('data-s90i'), raw:reads('data-s90r') },
    };
    modal.span = modal.data['7'].idx?.length ? 7 : (modal.data['30'].idx?.length ? 30 : 90);
    modal.mode = 'idx';

    modal.root.style.display = 'flex';
    renderSparkModal();
  }

  function renderSparkModal(){
    if (!modal.canvas || !modal.ctx) return;
    const d = modal.data[String(modal.span)][modal.mode] || [];
    const cvs = modal.canvas, ctx = modal.ctx;
    cvs.width = cvs.clientWidth; cvs.height = cvs.clientHeight;
    ctx.clearRect(0,0,cvs.width,cvs.height);
    if (d.length < 2){
      modal.title && (modal.title.textContent = '—');
      return;
    }
    let min=Math.min(...d), max=Math.max(...d);
    if (min===max){ min-=1e-6; max+=1e-6; }
    const pad=8, W=cvs.width, H=cvs.height;
    const nx=i=>pad+(i*(W-2*pad)/(d.length-1));
    const baseY=(modal.mode==='idx')?1:(min+(max-min)/2);
    const ny=v=>H-pad-((v-min)/(max-min))*(H-2*pad);

    // ベース線
    ctx.strokeStyle='rgba(255,255,255,.25)';
    ctx.setLineDash([3,3]); ctx.beginPath();
    ctx.moveTo(pad, ny(baseY)); ctx.lineTo(W-pad, ny(baseY));
    ctx.stroke(); ctx.setLineDash([]);

    // 折れ線
    ctx.strokeStyle = modal.color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(nx(0), ny(d[0]));
    for (let i=1;i<d.length;i++) ctx.lineTo(nx(i), ny(d[i]));
    ctx.stroke();

    if (modal.title){
      modal.title.textContent = `${modal.span}日 / ${modal.mode==='idx'?'指数':'実値'}`;
    }
  }

  function bindSparkModal(){
    const root = document.getElementById('sparkModal');
    if (!root) return;

    // 閉じる
    root.addEventListener('click', (e)=>{
      if (e.target.id === 'sparkClose' || e.target.id === 'sparkModal'){
        root.style.display = 'none';
      }
    });

    // 期間切替
    root.querySelectorAll('.spark-ctrl .btn[data-span]').forEach(btn=>{
      btn.addEventListener('click', ()=>{
        root.querySelectorAll('.spark-ctrl .btn[data-span]').forEach(b=>b.classList.remove('is-on'));
        btn.classList.add('is-on');
        modal.span = parseInt(btn.getAttribute('data-span'), 10);
        renderSparkModal();
      });
    });

    // 表示モード切替
    root.querySelectorAll('.spark-ctrl .btn[data-mode]').forEach(btn=>{
      btn.addEventListener('click', ()=>{
        root.querySelectorAll('.spark-ctrl .btn[data-mode]').forEach(b=>b.classList.remove('is-on'));
        btn.classList.add('is-on');
        modal.mode = btn.getAttribute('data-mode');
        renderSparkModal();
      });
    });

    // カード内スパークをタップでモーダル起動
    document.addEventListener('click', (e)=>{
      const svg = e.target.closest && e.target.closest('svg.spark');
      if (svg) openSparkModal(svg);
    });
  }

  /* ===== Boot ===== */
  function boot(){
    bindAllRows();
    drawAllInlineSparks();
    bindSparkModal();
  }

  window.addEventListener('load', boot);
  document.body.addEventListener('htmx:load', boot);
  window.addEventListener('resize', ()=>{ requestAnimationFrame(drawAllInlineSparks); });

  console.log('[holdings.js v121-fix] ready');
})();