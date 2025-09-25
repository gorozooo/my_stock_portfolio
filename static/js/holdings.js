// static/js/holdings.js  v10  (delegated + htmx-aware, stable)
(function(){
  const STATE = new WeakMap();
  const px = n => n + 'px';
  const getOpenW = (a) => {
    const v = getComputedStyle(a).getPropertyValue('--open-w').trim().replace('px','');
    return parseFloat(v || '220');
  };

  // 初期スタイルだけ与える（HTMX置換で新規行が来てもOK）
  function initRow(row){
    if (!row || STATE.has(row)) {
      // 既に状態がある行はスキップ（重複バインド防止）
      return;
    }
    const actions = row.querySelector('.actions');
    const track   = row.querySelector('.track');
    const detail  = row.querySelector('[data-action="detail"]');
    if (!actions || !track) return;

    const s = {
      actions, track, detail,
      openW: getOpenW(actions),
      opened: row.classList.contains('is-open'),
      dragging:false, horiz:false, sx:0, sy:0, baseRight:0
    };
    STATE.set(row, s);

    actions.style.right = px(-s.openW);
    actions.style.pointerEvents = 'none';
    actions.addEventListener('click', e => e.stopPropagation()); // ボタン押しやすく
    if (detail){
      detail.addEventListener('click', (e)=>{
        e.stopPropagation();
        closeSwipe(row);
        row.classList.toggle('show-detail');
      });
    }
  }

  function openSwipe(row){
    const s = STATE.get(row); if(!s) return;
    s.actions.style.transition = 'right .18s ease-out';
    row.classList.add('is-open'); s.opened = true;
    s.actions.style.right = '0px'; s.actions.style.pointerEvents = 'auto';
  }
  function closeSwipe(row){
    const s = STATE.get(row); if(!s) return;
    s.actions.style.transition = 'right .18s ease-out';
    row.classList.remove('is-open'); s.opened = false;
    s.actions.style.right = px(-s.openW); s.actions.style.pointerEvents = 'none';
  }
  function closeAll(except){
    document.querySelectorAll('[data-swipe].is-open').forEach(r=>{
      if (r !== except) closeSwipe(r);
    });
  }

  // ---- Delegated swipe handlers（全体に1回だけ） ----
  let movingRow = null;

  function onStart(e){
    const row = e.target.closest?.('[data-swipe]');
    if (!row) return;

    const s = STATE.get(row) || (initRow(row), STATE.get(row));
    if (!s) return;

    if (e.target.closest('.actions')) return; // アクション領域からは開始しない
    if (row.classList.contains('show-detail')) row.classList.remove('show-detail');

    s.dragging = true; s.horiz = false;
    const t = e.touches ? e.touches[0] : e;
    s.sx = t.clientX; s.sy = t.clientY;
    s.actions.style.transition = 'none';
    s.baseRight = s.opened ? 0 : -s.openW;

    if (!s.opened) closeAll(row);
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
      else { s.dragging=false; s.actions.style.transition=''; movingRow=null; return; }
    }
    if (e.cancelable) e.preventDefault();

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
    const willOpen = cur > -s.openW/2;
    willOpen ? openSwipe(row) : closeSwipe(row);
  }

  // カード本体タップ：詳細→閉じる。次にスワイプが開いていれば閉じる
  document.addEventListener('click', (e)=>{
    const row = e.target.closest?.('[data-swipe]');
    if (!row) { closeAll(null); return; }
    const s = STATE.get(row) || (initRow(row), STATE.get(row));
    if (!s) return;
    if (row.classList.contains('show-detail')) {
      row.classList.remove('show-detail');
      return;
    }
    if (row.classList.contains('is-open')) closeSwipe(row);
  });

  // スワイプ（委譲）
  document.addEventListener('touchstart', onStart, {passive:true});
  document.addEventListener('touchmove',  onMove,  {passive:false});
  document.addEventListener('touchend',   onEnd);
  document.addEventListener('touchcancel',onEnd);
  document.addEventListener('mousedown',  onStart);
  document.addEventListener('mousemove',  onMove);
  document.addEventListener('mouseup',    onEnd);

  // 初期行をセット
  document.querySelectorAll('[data-swipe]').forEach(initRow);

  // HTMX差し替え後に新行を初期化
  document.body.addEventListener('htmx:load', () => {
    document.querySelectorAll('[data-swipe]').forEach(initRow);
  });

  // 安定化（iOS）
  const style = document.createElement('style');
  style.textContent = `.track{touch-action:pan-y;-webkit-user-select:none;user-select:none}`;
  document.head.appendChild(style);

  console.log('[holdings.js v10] ready');
})();