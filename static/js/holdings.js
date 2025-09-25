// holdings.js v13 — Swipe固定を最優先で安定化
(function(){
  const STATE = new WeakMap();
  const px = n => n + 'px';
  const OPEN_MS_CLICK_GUARD = 600; // スワイプ直後のクリック抑止を少し長めに
  let lastSwipeEndedAt = 0;

  const getOpenW = (a) => {
    const v = getComputedStyle(a).getPropertyValue('--open-w').trim().replace('px','');
    return parseFloat(v || '220');
  };
  const justSwiped = () => (performance.now() - lastSwipeEndedAt) < OPEN_MS_CLICK_GUARD;

  function initRow(row){
    if (!row) return;
    let s = STATE.get(row);
    const actions = row.querySelector('.actions');
    const track   = row.querySelector('.track');
    const detail  = row.querySelector('[data-action="detail"]');
    if (!actions || !track) return;

    if (!s){
      s = { actions, track, detail, openW:getOpenW(actions), dragging:false, horiz:false, sx:0, sy:0, baseRight:0, opened:false, sticky:true };
      STATE.set(row, s);
    }else{
      s.actions = actions; s.track = track; s.detail = detail; s.openW = getOpenW(actions);
    }

    // 初期状態：必ず閉じる（描画直後のブレ防止）
    hardClose(row, s);

    // パネル内クリックはバブリングさせない（操作可能に）
    actions.addEventListener('click', e => e.stopPropagation(), {capture:true});

    // 詳細トグルはパネルを閉じてから
    if (detail){
      detail.addEventListener('click', (e)=>{
        e.stopPropagation();
        hardClose(row, s);
        row.classList.toggle('show-detail');
      });
    }

    // トラック単体タップで「閉じるだけ」
    track.addEventListener('click', (e)=>{
      // 直後の誤クリックは無視
      if (justSwiped()) return;
      if (row.classList.contains('is-open')) {
        hardClose(row, s);
      } else if (row.classList.contains('show-detail')) {
        row.classList.remove('show-detail');
      }
    });
  }

  function hardOpen(row, s){
    s.actions.style.transition = 'right .18s ease-out';
    row.classList.add('is-open');
    s.opened = true;
    s.actions.style.right = '0px';
    s.actions.style.pointerEvents = 'auto';
    lastSwipeEndedAt = performance.now();
  }
  function hardClose(row, s){
    s.actions.style.transition = 'right .18s ease-out';
    row.classList.remove('is-open');
    s.opened = false;
    s.actions.style.right = px(-s.openW);
    s.actions.style.pointerEvents = 'none';
    lastSwipeEndedAt = performance.now();
  }
  function closeAll(except){
    document.querySelectorAll('[data-swipe].is-open').forEach(r=>{
      if (r===except) return;
      const st = STATE.get(r); if (!st) return;
      hardClose(r, st);
    });
  }

  // ===== スワイプ（委譲） =====
  let movingRow = null;

  function onStart(e){
    const row = e.target.closest?.('[data-swipe]');
    if (!row) return;

    initRow(row); // 冪等
    const s = STATE.get(row); if (!s) return;

    // アクション上からのドラッグ開始はしない
    if (e.target.closest('.actions')) return;

    // 詳細が開いてたら閉じる（スワイプ優先）
    if (row.classList.contains('show-detail')) row.classList.remove('show-detail');

    // ★ 毎回 DOM から再評価して基準更新（初回含め確実に固定）
    s.openW  = getOpenW(s.actions);
    s.opened = row.classList.contains('is-open');

    s.dragging = true; s.horiz = false;
    const t = e.touches ? e.touches[0] : e;
    s.sx = t.clientX; s.sy = t.clientY;
    s.actions.style.transition = 'none';
    s.baseRight = s.opened ? 0 : -s.openW;

    if (!s.opened) closeAll(row); // 他を閉じる（自分が開いてる時はそのまま）
    movingRow = row;
  }

  function onMove(e){
    if (!movingRow) return;
    const s = STATE.get(movingRow); if (!s || !s.dragging) return;

    const t = e.touches ? e.touches[0] : e;
    const dx = t.clientX - s.sx, dy = t.clientY - s.sy;

    if (!s.horiz){
      if (Math.abs(dx) < 8) return;
      if (Math.abs(dx) > Math.abs(dy)) s.horiz = true;
      else { s.dragging = false; s.actions.style.transition=''; movingRow = null; return; }
    }
    if (e.cancelable) e.preventDefault();

    // 右プロパティで位置を管理（0〜-openW にクランプ）
    let nr = s.baseRight - dx;
    if (nr > 0) nr = 0;
    if (nr < -s.openW) nr = -s.openW;
    s.actions.style.right = px(nr);
  }

  function onEnd(){
    if (!movingRow) return;
    const row = movingRow; movingRow = null;
    const s = STATE.get(row); if (!s) return;

    s.dragging = false;
    s.actions.style.transition = 'right .18s ease-out';
    const cur = parseFloat(getComputedStyle(s.actions).right) || -s.openW;
    const willOpen = cur > -s.openW / 2;

    if (willOpen){
      hardOpen(row, s);  // ← 開いたら“固定”状態に
    }else{
      hardClose(row, s);
    }
  }

  // 外側タップでだけ全閉（行内のクリックでは閉じない＝勝手に戻らない）
  document.addEventListener('click', (e)=>{
    if (justSwiped()) return; // スワイプ直後の誤クリック無視
    if (!e.target.closest('[data-swipe]')) closeAll(null);
  });

  // リスナー（委譲）
  document.addEventListener('touchstart', onStart, {passive:true});
  document.addEventListener('touchmove',  onMove,  {passive:false});
  document.addEventListener('touchend',   onEnd);
  document.addEventListener('touchcancel',onEnd);
  document.addEventListener('mousedown',  onStart);
  document.addEventListener('mousemove',  onMove);
  document.addEventListener('mouseup',    onEnd);

  // 初期化 & HTMX差し替え対応
  document.querySelectorAll('[data-swipe]').forEach(initRow);
  document.body.addEventListener('htmx:load', ()=>{
    document.querySelectorAll('[data-swipe]').forEach(initRow);
  });

  // iOSの誤スクロール対策
  const style = document.createElement('style');
  style.textContent = `.track{touch-action:pan-y;-webkit-user-select:none;user-select:none}`;
  document.head.appendChild(style);

  console.log('[holdings.js v13] ready');
})();