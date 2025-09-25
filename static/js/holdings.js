// holdings.js v9 (delegation + htmx-aware, stable swipe & detail)
(function () {
  const STATE = new WeakMap(); // row -> {openW, opened, baseRight, dragging, horiz, sx, sy}

  const px = n => n + 'px';
  const getOpenW = (actions) => {
    const v = getComputedStyle(actions).getPropertyValue('--open-w').trim().replace('px','');
    return parseFloat(v || '220');
  };

  function initRow(row){
    if (!row || STATE.has(row)) return;
    const actions = row.querySelector('.actions');
    const track   = row.querySelector('.track');
    const detail  = row.querySelector('[data-action="detail"]');
    if (!actions || !track) return;

    const s = {
      actions, track, detail,
      openW: getOpenW(actions),
      opened: row.classList.contains('is-open'),
      dragging: false, horiz: false, sx:0, sy:0, baseRight:0,
    };
    STATE.set(row, s);

    // 初期位置
    actions.style.right = px(-s.openW);
    actions.style.pointerEvents = 'none';

    // アクション領域のクリックは行クリックに伝播させない（固定されない問題の原因）
    actions.addEventListener('click', e => e.stopPropagation());

    // ディテール
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
    s.actions.style.right = '0px'; s.actions.style.pointerEvents='auto';
  }
  function closeSwipe(row){
    const s = STATE.get(row); if(!s) return;
    s.actions.style.transition = 'right .18s ease-out';
    row.classList.remove('is-open'); s.opened = false;
    s.actions.style.right = px(-s.openW); s.actions.style.pointerEvents='none';
  }
  function closeAll(except){
    document.querySelectorAll('[data-swipe].is-open').forEach(r=>{
      if (r!==except) closeSwipe(r);
    });
  }

  // ---- Delegated handlers -------------------------------------------------
  function onStart(e){
    const row = e.target.closest('[data-swipe]'); if(!row) return;
    const s = STATE.get(row); if(!s) return;

    // actions上で開始したスワイプは無効化（ボタンを押せるように）
    if (e.target.closest('.actions')) return;

    // 詳細が開いていたら先に閉じる（2回目以降の固着を防止）
    if (row.classList.contains('show-detail')) row.classList.remove('show-detail');

    s.dragging = true; s.horiz = false;
    s.sx = ('touches' in e ? e.touches[0].clientX : e.clientX);
    s.sy = ('touches' in e ? e.touches[0].clientY : e.clientY);
    s.actions.style.transition = 'none';
    s.baseRight = s.opened ? 0 : -s.openW;

    if (!s.opened) closeAll(row);
  }
  function onMove(e){
    // ドキュメント委譲では対象 row を毎回再取得
    const t = ('touches' in e ? e.touches[0] : e);
    const row = document.elementFromPoint(t.clientX, t.clientY)?.closest('[data-swipe]');
    // 直近開始した行がわかるように、is-movingな行を優先
    const moving = document.querySelector('[data-swipe].__moving') || row;
    if (!moving) return;
    const s = STATE.get(moving); if(!s || !s.dragging) return;

    const dx = t.clientX - s.sx, dy = t.clientY - s.sy;
    if (!s.horiz){
      if (Math.abs(dx) < 8) return;
      if (Math.abs(dx) > Math.abs(dy)) s.horiz = true;
      else { s.dragging=false; s.actions.style.transition=''; return; }
      moving.classList.add('__moving');
    }
    // 横スクロールはJS管理
    if (e.cancelable) e.preventDefault();
    let nr = s.baseRight - dx; if (nr > 0) nr = 0; if (nr < -s.openW) nr = -s.openW;
    s.actions.style.right = px(nr);
  }
  function onEnd(){
    const moving = document.querySelector('[data-swipe].__moving');
    if (!moving) {
      // すべての行を確認してdragging終端処理
      document.querySelectorAll('[data-swipe]').forEach(row=>{
        const s = STATE.get(row); if (!s || !s.dragging) return;
        s.dragging=false; s.actions.style.transition='right .18s ease-out';
        const cur = parseFloat(getComputedStyle(s.actions).right) || -s.openW;
        const willOpen = (cur > -s.openW/2);
        willOpen ? openSwipe(row) : closeSwipe(row);
      });
      return;
    }
    const s = STATE.get(moving); if(!s) return;
    s.dragging=false; s.actions.style.transition='right .18s ease-out';
    const cur = parseFloat(getComputedStyle(s.actions).right) || -s.openW;
    const willOpen = (cur > -s.openW/2);
    willOpen ? openSwipe(moving) : closeSwipe(moving);
    moving.classList.remove('__moving');
  }

  // カード本体タップで「詳細を閉じる → スワイプも閉じる」
  document.addEventListener('click', (e)=>{
    const row = e.target.closest('[data-swipe]');
    if (!row) { closeAll(null); return; }
    if (row.classList.contains('show-detail')) {
      row.classList.remove('show-detail');
      return; // ここで止める：次のタップでスワイプ操作に入れる
    }
    if (row.classList.contains('is-open')) closeSwipe(row);
  });

  // 委譲でスワイプ（touch + mouse）
  document.addEventListener('touchstart', onStart, {passive:true});
  document.addEventListener('touchmove',  onMove,  {passive:false});
  document.addEventListener('touchend',   onEnd);
  document.addEventListener('touchcancel',onEnd);
  document.addEventListener('mousedown',  onStart);
  document.addEventListener('mousemove',  onMove);
  document.addEventListener('mouseup',    onEnd);

  // 初期化（初期DOM）
  document.querySelectorAll('[data-swipe]').forEach(initRow);

  // HTMX で差し替わったときに再初期化
  document.body.addEventListener('htmx:load', ()=>{
    document.querySelectorAll('[data-swipe]').forEach(initRow);
  });

  // 安定化：横スワイプはJSで扱い、縦スクロールはブラウザに任せる
  const style = document.createElement('style');
  style.textContent = `.track{touch-action:pan-y;-webkit-user-select:none;user-select:none}`;
  document.head.appendChild(style);

  // デバッグ用（本当に読まれているか確認）
  console.log('[holdings.js v9] initialized', document.querySelectorAll('[data-swipe]').length);
})();