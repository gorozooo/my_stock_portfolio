// static/js/holdings.js
(function () {
  const rows = Array.from(document.querySelectorAll('[data-swipe]'));
  if (!rows.length) return;

  const getOpenW = (actions) => {
    const v = getComputedStyle(actions).getPropertyValue('--open-w').trim().replace('px', '');
    return parseFloat(v || '220');
  };

  const setOpen = (row, open) => {
    const actions = row._actions;
    const OPEN = row._OPEN;
    actions.style.transition = 'right .18s ease-out';
    if (open) {
      row.classList.add('is-open');
      actions.style.right = '0px';
      actions.style.pointerEvents = 'auto';   // ← 固定（クリック可能）
      row._opened = true;
    } else {
      row.classList.remove('is-open');
      actions.style.right = (-OPEN) + 'px';
      actions.style.pointerEvents = 'none';
      row._opened = false;
    }
  };

  const closeAll = (except) => {
    rows.forEach(r => { if (r !== except) setOpen(r, false); });
  };

  rows.forEach(row => {
    const actions = row.querySelector('.actions');
    const detailBtn = row.querySelector('[data-action="detail"]');
    const track = row.querySelector('.track');

    // キャッシュ
    row._actions = actions;
    row._OPEN = getOpenW(actions);
    row._opened = false;

    // 初期位置
    actions.style.right = (-row._OPEN) + 'px';
    actions.style.pointerEvents = 'none';

    // アクション領域内のクリックは外側に伝播させない（固定解除されない）
    actions.addEventListener('click', e => e.stopPropagation());

    // -------- スワイプ実装 --------
    let sx = 0, sy = 0, dragging = false, horiz = false, baseRight = 0;

    const setRight = (px) => { actions.style.right = px + 'px'; };

    const onStart = (e) => {
      // アクション上からのドラッグ開始は無効（ボタンを押しやすく）
      if (e.target.closest('.actions')) return;
      const t = e.touches ? e.touches[0] : e;
      sx = t.clientX; sy = t.clientY;
      dragging = true; horiz = false;
      actions.style.transition = 'none';
      baseRight = row._opened ? 0 : -row._OPEN;

      // ほかが開いていたら閉じる
      if (!row._opened) closeAll(row);
    };

    const onMove = (e) => {
      if (!dragging) return;
      const t = e.touches ? e.touches[0] : e;
      const dx = t.clientX - sx, dy = t.clientY - sy;

      if (!horiz) {
        if (Math.abs(dx) < 8) return;
        if (Math.abs(dx) > Math.abs(dy)) horiz = true;
        else { dragging = false; actions.style.transition = ''; return; }
      }
      if (e.cancelable) e.preventDefault();

      let nr = baseRight - dx;      // 右→開く（dx>0）
      if (nr > 0) nr = 0;
      if (nr < -row._OPEN) nr = -row._OPEN;
      setRight(nr);
    };

    const onEnd = () => {
      if (!dragging) return;
      dragging = false;
      const cur = parseFloat(getComputedStyle(actions).right) || -row._OPEN;
      const willOpen = cur > -row._OPEN / 2;
      setOpen(row, willOpen);
    };

    row.addEventListener('touchstart', onStart, { passive: true });
    row.addEventListener('touchmove', onMove, { passive: false });
    row.addEventListener('touchend', onEnd);

    // カード本体のタップ：開いていたら閉じるだけ（固定解除の唯一のトリガ）
    track.addEventListener('click', () => {
      if (row._opened) setOpen(row, false);
    });

    // 詳細ボタン：詳細トグル＋パネルは必ず閉じる
    if (detailBtn) {
      detailBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        row.classList.toggle('show-detail');
        setOpen(row, false);
      });
    }
  });

  // 外側タップで全部閉じる（アクション領域は stopPropagation 済み）
  document.addEventListener('click', (e) => {
    if (!e.target.closest('[data-swipe]')) closeAll(null);
  });

  // HTMXで行が置き換わった時も安定させる（削除後の再初期化は不要だが保険）
  document.body.addEventListener('htmx:afterSwap', () => {
    // 何もしない（行が消えるだけ）が、将来追加時の拡張余地として残す
  });
})();