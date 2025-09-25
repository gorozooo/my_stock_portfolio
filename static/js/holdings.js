// holdings.js v15 — 単一実装/競合排除/初回から確実固定
(() => {
  const NS = '__swipe_v15_bound__';     // 二重バインド防止フラグ
  const GUARD_MS = 500;                 // スワイプ直後のゴーストクリック無視
  const START_SLOP = 8;                 // スワイプ開始閾値
  const OPEN_FRACTION = 0.45;           // これ以上開いていれば固定
  const VEL_OPEN = 0.35;                // px/ms 左向き速度で開く判定
  const px = v => `${v}px`;
  const now = () => performance.now();

  const getOpenW = (a) => {
    const v = getComputedStyle(a).getPropertyValue('--open-w').trim().replace('px','');
    return parseFloat(v || '220');
  };

  function bindRow(row){
    if (!row || row[NS]) return; // 既にバインド済みなら何もしない
    const actions = row.querySelector('.actions');
    const track   = row.querySelector('.track');
    const detail  = row.querySelector('[data-action="detail"]');
    if (!actions || !track) return;

    row[NS] = true; // 二重バインド防止
    let openW = getOpenW(actions);
    let opened = false;
    let suppressClicksUntil = 0;

    // 常に初期は閉じる
    actions.style.transition = 'none';
    actions.style.right = px(-openW);
    actions.style.pointerEvents = 'none';
    row.classList.remove('is-open');
    opened = false;

    // パネル内クリックは行へ伝播させない（操作可能に）
    actions.addEventListener('click', e => e.stopPropagation(), {capture:true});

    // 詳細ボタン：トグルしてパネルは閉じる
    if (detail){
      detail.addEventListener('click', (e)=>{
        e.stopPropagation();
        closeHard();
        row.classList.toggle('show-detail');
      });
    }

    // 行外クリックでだけ全閉
    document.addEventListener('click', (e)=>{
      if (now() < suppressClicksUntil) return;
      if (!e.target.closest('[data-swipe]')) closeAll();
    });

    // トラック単体タップで閉じる（外側タップと同等）
    track.addEventListener('click', ()=>{
      if (now() < suppressClicksUntil) return;
      if (row.classList.contains('is-open')) { closeHard(); return; }
      if (row.classList.contains('show-detail')) row.classList.remove('show-detail');
    });

    // ===== Pointer Events（単一実装） =====
    let active = false, horiz = false;
    let sx=0, sy=0, dx=0, dy=0, baseRight=0;
    let lastX=0, lastT=0, vx=0;

    function openHard(){
      actions.style.transition = 'right .18s ease-out';
      row.classList.add('is-open');
      opened = true;
      actions.style.right = '0px';
      actions.style.pointerEvents = 'auto';
      suppressClicksUntil = now() + GUARD_MS;
    }
    function closeHard(){
      actions.style.transition = 'right .18s ease-out';
      row.classList.remove('is-open');
      opened = false;
      actions.style.right = px(-openW);
      actions.style.pointerEvents = 'none';
      suppressClicksUntil = now() + GUARD_MS;
    }
    function closeAll(except){
      document.querySelectorAll('[data-swipe].is-open').forEach(r=>{
        if (r===except) return;
        const a = r.querySelector('.actions');
        if (!a) return;
        const w = getOpenW(a);
        a.style.transition = 'right .18s ease-out';
        r.classList.remove('is-open');
        a.style.right = px(-w);
        a.style.pointerEvents = 'none';
      });
    }

    track.addEventListener('pointerdown', (e)=>{
      if (e.target.closest('.actions')) return; // パネル上から開始しない
      // 他が開いていて自分が閉じているなら他を閉じる
      if (!row.classList.contains('is-open')) closeAll(row);

      active = true; horiz = false;
      sx = e.clientX; sy = e.clientY; dx = 0; dy = 0;
      lastX = e.clientX; lastT = e.timeStamp; vx = 0;

      // 幅・状態を毎回取り直す（初回/再描画ズレ対策）
      openW = getOpenW(actions);
      opened = row.classList.contains('is-open');
      baseRight = opened ? 0 : -openW;

      actions.style.transition = 'none';
      track.setPointerCapture(e.pointerId);
    });

    track.addEventListener('pointermove', (e)=>{
      if (!active) return;
      dx = e.clientX - sx;
      dy = e.clientY - sy;

      if (!horiz){
        const ax=Math.abs(dx), ay=Math.abs(dy);
        if (ax < START_SLOP) return;
        if (ax > ay) horiz = true;
        else { // 縦スクロール扱い
          active = false;
          actions.style.transition = '';
          try{ track.releasePointerCapture(e.pointerId); }catch(_){}
          return;
        }
      }

      e.preventDefault();
      let nr = baseRight - dx;            // 左にスワイプで 0 に近づく
      if (nr > 0) nr = 0;
      if (nr < -openW) nr = -openW;
      actions.style.right = px(nr);

      const dt = e.timeStamp - lastT;
      if (dt > 0){
        vx = (e.clientX - lastX) / dt;    // px/ms（負＝左）
        lastX = e.clientX; lastT = e.timeStamp;
      }
    }, {passive:false});

    function finish(e){
      if (!active) return;
      active = false;
      try{ track.releasePointerCapture(e.pointerId); }catch(_){}
      actions.style.transition = 'right .18s ease-out';
      const cur = parseFloat(getComputedStyle(actions).right) || -openW;
      const fraction = 1 - Math.abs(cur / openW);
      const fastOpen  = (-vx) > VEL_OPEN;
      const willOpen  = fastOpen || (fraction > OPEN_FRACTION);
      if (willOpen) openHard(); else closeHard();
    }

    track.addEventListener('pointerup', finish);
    track.addEventListener('pointercancel', finish);
    track.addEventListener('pointerleave', (e)=>{ if (active) finish(e); });

    // iOS誤スクロール対策
    track.style.touchAction = 'pan-y';
    track.style.userSelect  = 'none';
    track.style.webkitUserSelect = 'none';
  }

  function boot(){
    document.querySelectorAll('[data-swipe]').forEach(bindRow);
  }

  // 初期化＆HTMX後
  boot();
  document.body.addEventListener('htmx:load', boot);

  console.log('[holdings.js v15] bound');
})();