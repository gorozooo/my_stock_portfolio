/* dividends.js — swipe-to-reveal Edit/Delete (iOS Safari 安定版)
   - 卡ードを左スワイプで actions を表示
   - 画面外タップでクローズ
   - 削除連打ガード
*/
(() => {
  const START_SLOP = 8;     // 水平判定の遊び
  const THRESHOLD  = 0.35;  // 開閉の確定割合
  const GUARD_MS   = 260;
  const now = () => Date.now();

  function closeAll(except){
    document.querySelectorAll('[data-swipe].is-open').forEach(row=>{
      if (row !== except) row.classList.remove('is-open');
    });
  }
  function widthOf(el){
    const r = el.getBoundingClientRect();
    return r.width || 220;
  }

  function bindRow(row){
    if (!row || row.__bound_div_swipe) return;
    row.__bound_div_swipe = true;

    const actions = row.querySelector('.actions');
    const track   = row.querySelector('.track');
    if (!actions || !track) return;

    // actions 内のクリックはバブリング停止（リンク/フォームが反応するように）
    actions.addEventListener('click', e => e.stopPropagation(), {capture:false});
    actions.addEventListener('touchstart', e => e.stopPropagation(), {passive:true});

    // 削除二度押し防止
    const delBtn = actions.querySelector('form.delete');
    if (delBtn){
      delBtn.addEventListener('submit', (e)=>{
        if (delBtn.dataset.busy === '1'){ e.preventDefault(); return; }
        delBtn.dataset.busy = '1';
        const reset = ()=>{ delBtn.dataset.busy = '0'; };
        document.addEventListener('htmx:afterOnLoad', reset, {once:true});
        document.addEventListener('htmx:responseError', reset, {once:true});
      });
    }

    // カードタップ → オープン時は閉じる
    let guardUntil = 0;
    track.addEventListener('click', ()=>{
      if (row.classList.contains('is-open')){
        if (now() < guardUntil) return;
        row.classList.remove('is-open');
        guardUntil = now() + GUARD_MS;
      }
    });

    // 外側タップで全閉
    document.addEventListener('click', (e)=>{
      if (!e.target.closest('[data-swipe]')) closeAll();
    });

    // タッチスワイプ
    let sx=0, sy=0, dragging=false, horiz=false, baseOpen=false;
    let pullOpen=0, pushClose=0;

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
      if (open){ closeAll(row); row.classList.add('is-open'); }
      else { row.classList.remove('is-open'); }
      guardUntil = now() + GUARD_MS;
    }

    track.addEventListener('touchstart', (e)=>{
      if (e.target.closest('.actions')) return;
      if (!row.classList.contains('is-open')) closeAll(row);
      const t = e.touches[0]; sx=t.clientX; sy=t.clientY;
      dragging=true; horiz=false; baseOpen=row.classList.contains('is-open');
      pullOpen=0; pushClose=0;
    }, {passive:true});

    track.addEventListener('touchmove', (e)=>{
      if (!dragging) return;
      const t=e.touches[0], dx=t.clientX-sx, dy=t.clientY-sy;
      if (!horiz){
        if (Math.abs(dx) < START_SLOP) return;
        if (Math.abs(dx) > Math.abs(dy)) horiz=true; else { dragging=false; return; }
      }
      e.preventDefault(); // 縦スクロール抑止
      const w = widthOf(actions);
      if (!baseOpen){
        pullOpen = Math.max(0, -dx);
        follow(-pullOpen);
      }else{
        pushClose = Math.max(0, dx);
        follow(-w + pushClose);
      }
    }, {passive:false});

    track.addEventListener('touchend', ()=>{
      if (!dragging) return; dragging=false;
      const w = widthOf(actions);
      if (!baseOpen){
        snap(pullOpen > w * THRESHOLD);
      }else{
        const shouldClose = pushClose > w * THRESHOLD;
        snap(!shouldClose);
      }
    });
  }

  function boot(){
    document.querySelectorAll('[data-swipe]').forEach(bindRow);
  }

  window.addEventListener('load', boot);
  document.body.addEventListener('htmx:load', boot);

  console.log('[dividends.js] swipe ready');
})();