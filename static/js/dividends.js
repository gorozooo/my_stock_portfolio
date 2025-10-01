/* dividends.js — swipe-to-reveal Edit/Delete (iOS Safari 安定版)
   - カードを左スワイプで actions を表示
   - 画面外タップでクローズ
   - 削除二度押しガード
*/
(() => {
  const START_SLOP = 8;     // 水平判定の遊び
  const THRESHOLD  = 0.35;  // 開閉の確定割合（幅の何割以上で確定）
  const GUARD_MS   = 260;
  const now = () => Date.now();

  const openClass = 'is-open';

  function closeAll(except){
    document.querySelectorAll('[data-swipe].' + openClass).forEach(row=>{
      if (row !== except) {
        row.classList.remove(openClass);
        const acts = row.querySelector('.actions');
        if (acts){ acts.style.pointerEvents = 'none'; acts.style.transform=''; acts.style.transition=''; }
      }
    });
  }

  function widthOf(el){
    const r = el.getBoundingClientRect?.() || {};
    return (r.width && r.width > 0) ? r.width : 220;
  }

  // --- 画面外タップで全閉（1回だけバインド） ---
  let outsideBound = false;
  function bindOutsideCloseOnce(){
    if (outsideBound) return; outsideBound = true;
    document.addEventListener('click', (e)=>{
      if (!e.target.closest('[data-swipe]')) closeAll();
    });
    document.addEventListener('touchstart', (e)=>{
      if (!e.target.closest('[data-swipe]')) closeAll();
    }, {passive:true});
  }

  function bindRow(row){
    if (!row || row.__bound_div_swipe) return;
    row.__bound_div_swipe = true;

    const actions = row.querySelector('.actions');
    const track   = row.querySelector('.track');
    if (!actions || !track) return;

    // 初期は操作不可（CSS 依存を補強）
    actions.style.pointerEvents = 'none';

    // actions 内の操作はバブリング停止（リンク/フォームが素直に動くように）
    ['click','mousedown','mouseup'].forEach(ev=>{
      actions.addEventListener(ev, e => e.stopPropagation());
    });
    actions.addEventListener('touchstart', e => e.stopPropagation(), {passive:true});

    // 削除二度押しガード（最初の form に対して）
    const delForm = actions.querySelector('form');
    if (delForm){
      delForm.addEventListener('submit', (e)=>{
        if (delForm.dataset.busy === '1'){ e.preventDefault(); return; }
        delForm.dataset.busy = '1';
        const reset = ()=>{ delForm.dataset.busy = '0'; };
        document.addEventListener('htmx:afterOnLoad', reset, {once:true});
        document.addEventListener('htmx:responseError', reset, {once:true});
      });
    }

    // カードタップ → オープン中は閉じる（誤タップ抑止のクールダウン付き）
    let guardUntil = 0;
    track.addEventListener('click', ()=>{
      if (row.classList.contains(openClass)){
        if (now() < guardUntil) return;
        row.classList.remove(openClass);
        actions.style.pointerEvents = 'none';
        actions.style.transform = '';
        guardUntil = now() + GUARD_MS;
      }
    });

    // タッチスワイプ
    let sx=0, sy=0, dragging=false, horiz=false, baseOpen=false;
    let pullOpen=0, pushClose=0;

    function follow(dist){
      // dist: 0=閉、-w=全開
      const w = widthOf(actions);
      const clamped = Math.max(-w, Math.min(0, dist));
      actions.style.transition = 'none';
      const pct = 100 + (clamped / w) * 100; // [-w..0] → [0..100]
      actions.style.transform = `translateX(${pct}%)`;

      // 20% 以上開いていればクリック可能に（iOS Safari 対策）
      const openedEnough = (-clamped) >= (w * 0.2);
      actions.style.pointerEvents = openedEnough ? 'auto' : 'none';
    }

    function snap(open){
      actions.style.transition = '';
      actions.style.transform  = '';
      if (open){
        closeAll(row);
        row.classList.add(openClass);
        actions.style.pointerEvents = 'auto';
      }else{
        row.classList.remove(openClass);
        actions.style.pointerEvents = 'none';
      }
      guardUntil = now() + GUARD_MS;
    }

    track.addEventListener('touchstart', (e)=>{
      if (e.target.closest('.actions')) return; // ボタン操作はスルー
      bindOutsideCloseOnce();
      if (!row.classList.contains(openClass)) closeAll(row);
      const t = e.touches[0]; sx=t.clientX; sy=t.clientY;
      dragging=true; horiz=false; baseOpen=row.classList.contains(openClass);
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
        pullOpen = Math.max(0, -dx);      // 左へ引っ張ると増える
        follow(-pullOpen);
      }else{
        pushClose = Math.max(0, dx);      // 右へ戻すと増える
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

    // マウスでも横ドラッグ対応（PCデバッグ時便利）
    let md=false, mx=0, my=0;
    track.addEventListener('mousedown',(e)=>{
      if (e.button!==0) return;
      md=true; mx=e.clientX; my=e.clientY; baseOpen=row.classList.contains(openClass);
      pullOpen=0; pushClose=0;
      document.body.style.userSelect='none';
    });
    window.addEventListener('mousemove',(e)=>{
      if (!md) return;
      const dx=e.clientX-mx, dy=e.clientY-my;
      if (Math.abs(dx) < START_SLOP || Math.abs(dx) <= Math.abs(dy)) return;
      const w = widthOf(actions);
      if (!baseOpen){
        pullOpen = Math.max(0, -dx);
        follow(-pullOpen);
      }else{
        pushClose = Math.max(0, dx);
        follow(-w + pushClose);
      }
    });
    window.addEventListener('mouseup',()=>{
      if (!md) return; md=false; document.body.style.userSelect='';
      const w = widthOf(actions);
      if (!baseOpen){ snap(pullOpen > w * THRESHOLD); }
      else { snap(!(pushClose > w * THRESHOLD)); }
    });
  }

  function boot(){
    document.querySelectorAll('[data-swipe]').forEach(bindRow);
    bindOutsideCloseOnce();
  }

  window.addEventListener('load', boot);
  document.body.addEventListener('htmx:load', boot);

  console.log('[dividends.js] swipe ready');
})();