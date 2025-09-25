// holdings.js v14 — 初回から確実固定 / ゴーストクリック排除 / PointerEvents化
(function(){
  const ROWS = new WeakMap();
  const now = () => performance.now();
  const px  = v => `${v}px`;
  const GUARD_MS = 500;  // スワイプ直後のクリック無視時間
  const START_SLOP = 8;  // スワイプ判定の初期スロープ
  const OPEN_FRACTION = 0.45; // これ以上開いていれば固定
  const VEL_OPEN = 0.35; // px/ms 相当の開放しきい値（素早いスワイプは距離未満でも開く）

  let lastGestureAt = 0;

  const getOpenW = (actions) => {
    const v = getComputedStyle(actions).getPropertyValue('--open-w').trim().replace('px','');
    return parseFloat(v || '220');
  };

  function stateOf(row){
    let st = ROWS.get(row);
    if (st) return st;
    const actions = row.querySelector('.actions');
    const track   = row.querySelector('.track');
    const detail  = row.querySelector('[data-action="detail"]');
    if (!actions || !track) return null;

    st = {
      actions, track, detail,
      openW: getOpenW(actions),
      // pointer gesture
      active:false, horiz:false, sx:0, sy:0, dx:0, dy:0,
      t0:0, lastX:0, lastT:0, vx:0,
      // open state
      opened:false,
      suppressClicksUntil: 0
    };
    ROWS.set(row, st);
    return st;
  }

  function hardClose(row, st){
    st.actions.style.transition = 'right .18s ease-out';
    row.classList.remove('is-open');
    st.opened = false;
    st.actions.style.right = px(-st.openW);
    st.actions.style.pointerEvents = 'none';
    st.suppressClicksUntil = now() + GUARD_MS; // 直後の誤クリック無視
    lastGestureAt = now();
  }
  function hardOpen(row, st){
    st.actions.style.transition = 'right .18s ease-out';
    row.classList.add('is-open');
    st.opened = true;
    st.actions.style.right = '0px';
    st.actions.style.pointerEvents = 'auto';
    st.suppressClicksUntil = now() + GUARD_MS;
    lastGestureAt = now();
  }
  function closeAll(except){
    document.querySelectorAll('[data-swipe].is-open').forEach(r=>{
      if (r === except) return;
      const s = stateOf(r); if (!s) return;
      hardClose(r, s);
    });
  }

  function initRow(row){
    const st = stateOf(row); if (!st) return;

    // リサイズ等で幅更新
    st.openW = getOpenW(st.actions);

    // 初期は必ず閉じる（初回ブレ防止）
    st.actions.style.transition = 'none';
    st.actions.style.right = px(-st.openW);
    st.actions.style.pointerEvents = 'none';
    row.classList.remove('is-open');
    st.opened = false;

    // パネル内のクリックはバブリングさせない（操作可能に）
    st.actions.addEventListener('click', e => e.stopPropagation(), {capture:true, passive:true});

    // 詳細は押下でトグル＆パネルは閉じる
    if (st.detail && !st._detailBound){
      st._detailBound = true;
      st.detail.addEventListener('click', (e)=>{
        e.stopPropagation();
        hardClose(row, st);
        row.classList.toggle('show-detail');
      }, {passive:true});
    }

    // トラックの「単独タップ」で閉じる（外側タップでも閉じる）
    if (!st._trackClickBound){
      st._trackClickBound = true;
      st.track.addEventListener('click', (e)=>{
        if (now() < st.suppressClicksUntil) return; // 直後は無視
        if (row.classList.contains('is-open')) { hardClose(row, st); return; }
        if (row.classList.contains('show-detail')) { row.classList.remove('show-detail'); }
      }, {passive:true});
    }

    // Pointer Events で統一
    if (!st._peBound){
      st._peBound = true;

      st.track.addEventListener('pointerdown', (e)=>{
        // アクション領域から開始しない
        if (e.target.closest('.actions')) return;

        // 既に他が開いていて、自分は閉じてるなら他を閉じる
        if (!row.classList.contains('is-open')) closeAll(row);

        st.active = true; st.horiz = false;
        st.sx = e.clientX; st.sy = e.clientY;
        st.dx = 0; st.dy = 0;
        st.t0 = e.timeStamp; st.lastX = e.clientX; st.lastT = e.timeStamp; st.vx = 0;

        // 現在の開閉状態を DOM から毎回再評価（初回ズレ対策）
        st.opened = row.classList.contains('is-open');
        st.openW  = getOpenW(st.actions);

        st.actions.style.transition = 'none';
        st.baseRight = st.opened ? 0 : -st.openW;

        // このトラックで pointer を捕捉
        st.track.setPointerCapture(e.pointerId);
      });

      st.track.addEventListener('pointermove', (e)=>{
        if (!st.active) return;

        st.dx = e.clientX - st.sx;
        st.dy = e.clientY - st.sy;

        // 方向判定（横に入ったらスワイプ成立）
        if (!st.horiz){
          const ax = Math.abs(st.dx), ay = Math.abs(st.dy);
          if (ax < START_SLOP) return;
          if (ax > ay) st.horiz = true;
          else { // 縦スクロールと判断
            st.active = false;
            st.actions.style.transition = '';
            try { st.track.releasePointerCapture(e.pointerId); } catch(_){}
            return;
          }
        }

        e.preventDefault(); // スクロール抑止

        // 右（マイナス方向）プロパティで位置管理：0..-openW にクランプ
        let nr = st.baseRight - st.dx;     // 左へスワイプで 0 に近づく
        if (nr > 0) nr = 0;
        if (nr < -st.openW) nr = -st.openW;
        st.actions.style.right = px(nr);

        // 速度（簡易）
        const dt = e.timeStamp - st.lastT;
        if (dt > 0){
          st.vx = (e.clientX - st.lastX) / dt; // px/ms
          st.lastX = e.clientX; st.lastT = e.timeStamp;
        }
      }, {passive:false});

      const finish = (e)=>{
        if (!st.active) return;
        st.active = false;
        try { st.track.releasePointerCapture(e.pointerId); } catch(_){}

        st.actions.style.transition = 'right .18s ease-out';
        const curRight = parseFloat(getComputedStyle(st.actions).right) || -st.openW;

        const fraction = 1 - Math.abs(curRight / st.openW); // 開き具合（0..1）
        const fastOpen = (-st.vx) > VEL_OPEN;               // 左方向に速ければ開く
        const willOpen = fastOpen || (fraction > OPEN_FRACTION);

        if (willOpen) hardOpen(row, st);
        else          hardClose(row, st);
      };

      st.track.addEventListener('pointerup', finish);
      st.track.addEventListener('pointercancel', finish);
      st.track.addEventListener('pointerleave', (e)=>{ if(st.active) finish(e); });
    }
  }

  function boot(){
    document.querySelectorAll('[data-swipe]').forEach(initRow);
  }

  // 初期化
  boot();

  // HTMX差し替え後も再初期化
  document.body.addEventListener('htmx:load', boot);

  // 外側タップでだけ全閉（行内は閉じない＝勝手に戻らない）
  document.addEventListener('click', (e)=>{
    if (now() - lastGestureAt < GUARD_MS) return; // 直後の誤クリック無視（全体）
    if (!e.target.closest('[data-swipe]')) {
      document.querySelectorAll('[data-swipe].is-open').forEach(row=>{
        const st = stateOf(row); if (st) hardClose(row, st);
      });
    }
  });

  // iOS 誤スクロール対策
  const style = document.createElement('style');
  style.textContent = `.track{touch-action:pan-y;-webkit-user-select:none;user-select:none}`;
  document.head.appendChild(style);

  console.log('[holdings.js v14] ready');
})();