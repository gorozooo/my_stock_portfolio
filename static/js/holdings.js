/* holdings.js v121 — iOS Safari安定版
   - スワイプ固定維持（translateX + .is-open）
   - 詳細/編集/削除 全部動作
   - 「詳細開いた後、カードタップで閉じる」をガード無しで最優先
   - 削除連打防止
   - スパークライン描画同梱
*/
(() => {
  const START_SLOP = 8;
  const THRESHOLD  = 0.35;
  const GUARD_MS   = 280;
  const now = () => Date.now();

  function closeAll(except){
    document.querySelectorAll('[data-swipe].is-open').forEach(row=>{
      if (row !== except) row.classList.remove('is-open');
    });
  }
  function widthOf(el){
    const r = el.getBoundingClientRect();
    return r.width || parseFloat(getComputedStyle(el).getPropertyValue('--open-w')) || 220;
  }

  function bindRow(row){
    if (!row || row.__bound_v121) return;
    row.__bound_v121 = true;

    const actions   = row.querySelector('.actions');
    const track     = row.querySelector('.track');
    const btnDetail = row.querySelector('[data-action="detail"]');
    const btnDelete = row.querySelector('.item.delete');
    if (!actions || !track) return;

    let guardUntil = 0;

    // アクション内クリックはバブリング停止（HTMXは生かす）
    actions.addEventListener('click', e => { e.stopPropagation(); }, {capture:false});

    // 詳細トグル：開閉＋パネルは閉じる
    if (btnDetail){
      btnDetail.addEventListener('click', e=>{
        e.stopPropagation();
        row.classList.toggle('show-detail');
        row.classList.remove('is-open');
        guardUntil = now() + GUARD_MS;
      });
    }

    // 削除：二度押し防止
    if (btnDelete){
      btnDelete.addEventListener('click', e=>{
        if (btnDelete.dataset.busy === '1'){
          e.preventDefault();
          return;
        }
        btnDelete.dataset.busy = '1';
        document.body.addEventListener('htmx:afterOnLoad', function onload(){
          btnDelete.dataset.busy = '0';
          document.body.removeEventListener('htmx:afterOnLoad', onload);
        }, {once:true});
        document.body.addEventListener('htmx:responseError', ()=>{
          btnDelete.dataset.busy = '0';
        }, {once:true});
      });
    }

    // ★ カードタップで閉じる（詳細 → 無条件で最優先、パネル → ガードあり）
    track.addEventListener('click', (e)=>{
      // インタラクティブ要素は尊重
      if (e.target.closest('.actions, .item, a, button, input, select, textarea, label')) return;

      // 1) 詳細が開いていれば、ガード無視で閉じる（最優先）
      if (row.classList.contains('show-detail')){
        row.classList.remove('show-detail');
        return;
      }
      // 2) パネルが開いていれば閉じる（ガードあり）
      if (row.classList.contains('is-open')){
        if (now() < guardUntil) return;
        row.classList.remove('is-open');
        guardUntil = now() + GUARD_MS;
      }
    });

    // 画面外タップで全閉
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
      e.preventDefault();
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

  function bindAll(){ document.querySelectorAll('[data-swipe]').forEach(bindRow); }

  // ===== スパークライン =====
  function drawSpark(svg){
    try{
      let arr;
      const raw = svg.getAttribute('data-spark') || '[]';
      try{ arr = JSON.parse(raw); }catch{ arr = String(raw).split(',').map(s=>parseFloat(s)); }
      if (!Array.isArray(arr) || arr.length < 2){ svg.replaceChildren(); return; }

      const rate = parseFloat(svg.getAttribute('data-rate') || '0');
      const stroke = (isFinite(rate) && rate < 0) ? '#ef4444' : '#22c55e';

      const vb = svg.viewBox.baseVal || {width:96, height:24};
      const W = vb.width  || 96, H = vb.height || 24, pad = 1;

      let min = Math.min(...arr), max = Math.max(...arr);
      if (min === max){ min -= 1e-6; max += 1e-6; }

      const nx = i => pad + (i*(W-2*pad)/(arr.length-1));
      const ny = v => H - pad - ((v-min)/(max-min)) * (H-2*pad);

      let d = `M${nx(0)},${ny(arr[0])}`;
      for (let i=1;i<arr.length;i++) d += ` L${nx(i)},${ny(arr[i])}`;

      svg.innerHTML = `<path d="${d}" fill="none" stroke="${stroke}" stroke-width="1.6" stroke-linecap="round"/>`;
    }catch(_){ svg.replaceChildren(); }
  }
  function drawAllSparks(){ document.querySelectorAll('svg.spark[data-spark]').forEach(drawSpark); }

  function boot(){ bindAll(); drawAllSparks(); }
  window.addEventListener('load', boot);
  document.body.addEventListener('htmx:load', boot);
  window.addEventListener('resize', ()=>{ requestAnimationFrame(drawAllSparks); });

  console.log('[holdings.js v121] ready');
})();