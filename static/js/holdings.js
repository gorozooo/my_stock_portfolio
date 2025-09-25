// static/js/holdings.js  v12  (fix: re-open stability & post-swipe click guard)
(function(){
  const STATE = new WeakMap();
  const px = n => n + 'px';
  const getOpenW = (a) => {
    const v = getComputedStyle(a).getPropertyValue('--open-w').trim().replace('px','');
    return parseFloat(v || '220');
  };

  // スワイプ直後クリック抑止用
  let lastSwipeEndedAt = 0;
  const justSwiped = () => (performance.now() - lastSwipeEndedAt) < 300;

  function initRow(row){
    if (!row) return;
    // 既存stateがあっても、DOMが差し替わってる可能性があるので actions 存在チェック
    let s = STATE.get(row);
    const actions = row.querySelector('.actions');
    const track   = row.querySelector('.track');
    const detail  = row.querySelector('[data-action="detail"]');
    if (!actions || !track) return;

    if (!s){
      s = { actions, track, detail, openW: getOpenW(actions), opened:false, dragging:false, horiz:false, sx:0, sy:0, baseRight:0 };
      STATE.set(row, s);
    }else{
      s.actions = actions; s.track = track; s.detail = detail; s.openW = getOpenW(actions);
    }

    // 初期位置を必ず閉じ状態に
    actions.style.right = px(-s.openW);
    actions.style.pointerEvents = 'none';
    row.classList.remove('is-open');
    s.opened = false;

    // パネル内クリックはバブリング阻止（選択できない問題を排除）
    actions.addEventListener('click', e => e.stopPropagation());
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
    row.classList.add('is-open');
    s.opened = true;
    s.actions.style.right = '0px';
    s.actions.style.pointerEvents = 'auto';
  }
  function closeSwipe(row){
    const s = STATE.get(row); if(!s) return;
    s.actions.style.transition = 'right .18s ease-out';
    row.classList.remove('is-open');
    s.opened = false;
    s.actions.style.right = px(-s.openW);
    s.actions.style.pointerEvents = 'none';
  }
  function closeAll(except){
    document.querySelectorAll('[data-swipe].is-open').forEach(r=>{ if(r!==except) closeSwipe(r); });
  }

  // ---- Delegated handlers ----
  let movingRow = null;

  function onStart(e){
    const row = e.target.closest?.('[data-swipe]');
    if (!row) return;

    initRow(row); // 冪等
    const s = STATE.get(row); if (!s) return;

    if (e.target.closest('.actions')) return; // アクションから開始しない
    if (row.classList.contains('show-detail')) row.classList.remove('show-detail');

    // ★ 毎回“今開いているか”をDOMから再評価（古いstate参照しない）
    s.opened = row.classList.contains('is-open');
    s.openW  = getOpenW(s.actions);

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
    if (willOpen) {
      openSwipe(row);
      lastSwipeEndedAt = performance.now(); // ★ 直後のクリック無視
    } else {
      closeSwipe(row);
      lastSwipeEndedAt = performance.now();
    }
  }

  // 本体クリック：スワイプ直後は無視。詳細が開いていれば閉じる。スワイプ開いていれば閉じる。
  document.addEventListener('click', (e)=>{
    if (justSwiped()) return; // ★ 直後の誤タップ抑止
    const row = e.target.closest?.('[data-swipe]');
    if (!row) { closeAll(null); return; }
    initRow(row);
    if (row.classList.contains('show-detail')) { row.classList.remove('show-detail'); return; }
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

  // 初期化
  document.querySelectorAll('[data-swipe]').forEach(initRow);

  // HTMX差し替え後も初期化
  document.body.addEventListener('htmx:load', () => {
    document.querySelectorAll('[data-swipe]').forEach(initRow);
  });

  // iOS安定化
  const style = document.createElement('style');
  style.textContent = `.track{touch-action:pan-y;-webkit-user-select:none;user-select:none}`;
  document.head.appendChild(style);

  console.log('[holdings.js v12] ready');
})();