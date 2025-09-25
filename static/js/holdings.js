/* holdings.js v120 — iOS Safari安定版
   - スワイプ: translateX + .is-open で確実固定（本文は動かさない）
   - アクション: 詳細/編集/削除すべて動作（HTMXと競合しない伝播制御）
   - 削除: 二度押し防止（busyフラグ）
   - スパークライン描画同梱（<svg class="spark" data-spark='[...]' data-rate='xx'>）
*/
(() => {
  const START_SLOP = 8;   // 水平判定しきい値(px)
  const THRESHOLD  = 0.35; // 開閉の確定閾値（横幅の割合）
  const GUARD_MS   = 280;  // 直後のゴーストタップ抑止
  const now = () => Date.now();

  /* ========== ユーティリティ ========== */
  function closeAll(except){
    document.querySelectorAll('[data-swipe].is-open').forEach(row=>{
      if (row !== except) row.classList.remove('is-open');
    });
  }
  function widthOf(el){
    const r = el.getBoundingClientRect();
    return r.width || parseFloat(getComputedStyle(el).getPropertyValue('--open-w')) || 220;
  }

  /* ========== 1カードにバインド ========== */
  function bindRow(row){
    if (!row || row.__bound_v120) return;
    row.__bound_v120 = true;

    const actions = row.querySelector('.actions');
    const track   = row.querySelector('.track');
    const btnDetail = row.querySelector('[data-action="detail"]');
    const btnDelete = row.querySelector('.item.delete');

    if (!actions || !track) return;

    let guardUntil = 0;

    // アクション内のクリックは行クリックへバブリングさせない（でもHTMXは効く）
    actions.addEventListener('click', e => { e.stopPropagation(); }, {capture:false});

    // 詳細トグル：押下で詳細開閉＋パネルは閉じる
    if (btnDetail){
      btnDetail.addEventListener('click', e=>{
        e.stopPropagation();
        row.classList.toggle('show-detail');
        row.classList.remove('is-open');
        guardUntil = now() + GUARD_MS;
      });
    }

    // 削除ボタン：二度押し防止（HTMXはそのまま動く）
    if (btnDelete){
      btnDelete.addEventListener('click', e=>{
        if (btnDelete.dataset.busy === '1'){
          e.preventDefault(); // 連打防止
          return;
        }
        btnDelete.dataset.busy = '1';
        // htmx:afterOnLoad でアンセット（成功時）
        document.body.addEventListener('htmx:afterOnLoad', function onload(ev){
          const tgt = ev.target;
          if (!tgt) return;
          // この行が消えた（削除成功）か、何かしら応答が返ってきたら解除
          if (!document.body.contains(row) || !document.body.contains(btnDelete)){
            document.body.removeEventListener('htmx:afterOnLoad', onload);
            return;
          }
          btnDelete.dataset.busy = '0';
          document.body.removeEventListener('htmx:afterOnLoad', onload);
        }, {once:true});
        // エラー時も解除
        document.body.addEventListener('htmx:responseError', ()=>{
          btnDelete.dataset.busy = '0';
        }, {once:true});
      });
    }

    // 本文タップ：開いていれば閉じる／詳細が開いていれば閉じる
    track.addEventListener('click', ()=>{
      if (now() < guardUntil) return;
      if (row.classList.contains('is-open')){
        row.classList.remove('is-open');
        guardUntil = now() + GUARD_MS;
      } else if (row.classList.contains('show-detail')){
        row.classList.remove('show-detail');
      }
    });

    // 画面外タップで閉じる（アクション領域は stopPropagation 済み）
    document.addEventListener('click', (e)=>{
      if (now() < guardUntil) return;
      if (!e.target.closest('[data-swipe]')) closeAll();
    });

    // ======== タッチスワイプ（translateXで追従、確定はクラス） ========
    let sx=0, sy=0, dragging=false, horiz=false, baseOpen=false, openPull=0, closePush=0;

    function follow(dist){ // dist: [-w..0] で進行
      const w = widthOf(actions);
      const clamped = Math.max(-w, Math.min(0, dist));
      actions.style.transition = 'none';
      // 0% = open（画面内）、100% = close（画面外）
      const pct = 100 + (clamped / w) * 100; // [-w..0] → [0..100]
      actions.style.transform = `translateX(${pct}%)`;
      // ボタンの誤タップ防止：ある程度開いたらだけ有効化
      actions.style.pointerEvents = (pct < 85) ? 'auto' : 'none';
    }
    function snap(open){
      actions.style.transition = '';
      actions.style.transform  = ''; // 最終状態はCSSに任せる
      if (open){
        closeAll(row);
        row.classList.add('is-open');
      }else{
        row.classList.remove('is-open');
      }
      guardUntil = now() + GUARD_MS;
    }

    track.addEventListener('touchstart', (e)=>{
      if (e.target.closest('.actions')) return; // パネル内から開始しない
      if (!row.classList.contains('is-open')) closeAll(row); // 他行を閉じる

      const t = e.touches[0];
      sx = t.clientX; sy = t.clientY;
      dragging = true; horiz = false;
      baseOpen = row.classList.contains('is-open');
      openPull = 0; closePush = 0;
    }, {passive:true});

    track.addEventListener('touchmove', (e)=>{
      if (!dragging) return;
      const t = e.touches[0];
      const dx = t.clientX - sx;
      const dy = t.clientY - sy;

      if (!horiz){
        if (Math.abs(dx) < START_SLOP) return;
        if (Math.abs(dx) > Math.abs(dy)){ horiz = true; } else { dragging = false; return; }
      }

      // 横操作をJSで扱う（iOSの縦スクロールは殺さない）
      e.preventDefault();

      const w = widthOf(actions);
      if (!baseOpen){
        // 閉 → 開（左へ引く）
        openPull = Math.max(0, -dx); // 左へ動かすと増える
        follow(-openPull);           // 0..w → 0..-w
      }else{
        // 開 → 閉（右へ押す）
        closePush = Math.max(0, dx);       // 右へ動かすと増える
        follow(-w + closePush);            // -w..0 へ戻す
      }
    }, {passive:false});

    track.addEventListener('touchend', ()=>{
      if (!dragging) return;
      dragging = false;
      const w = widthOf(actions);
      if (!baseOpen){
        // 開く判定
        snap(openPull > w * THRESHOLD);
      }else{
        // 閉じる判定
        const shouldClose = closePush > w * THRESHOLD;
        snap(!shouldClose);
      }
    });
  }

  function bindAll(){
    document.querySelectorAll('[data-swipe]').forEach(bindRow);
  }

  /* ========== スパークライン ========== */
  function drawSpark(svg){
    try{
      let arr;
      const raw = svg.getAttribute('data-spark') || '[]';
      try{ arr = JSON.parse(raw); }catch{ arr = String(raw).split(',').map(s=>parseFloat(s)); }
      if (!Array.isArray(arr) || arr.length < 2){ svg.replaceChildren(); return; }

      const rate = parseFloat(svg.getAttribute('data-rate') || '0');
      const stroke = (isFinite(rate) && rate < 0) ? '#ef4444' : '#22c55e';

      const vb = svg.viewBox.baseVal || {width:96, height:24};
      const W = vb.width  || 96;
      const H = vb.height || 24;
      const pad = 1;

      let min = Math.min(...arr), max = Math.max(...arr);
      if (min === max){ min -= 1e-6; max += 1e-6; }

      const nx = i => pad + (i * (W - 2*pad) / (arr.length - 1));
      const ny = v => H - pad - ((v - min) / (max - min)) * (H - 2*pad);

      let d = `M${nx(0)},${ny(arr[0])}`;
      for (let i=1;i<arr.length;i++) d += ` L${nx(i)},${ny(arr[i])}`;

      svg.innerHTML = `<path d="${d}" fill="none" stroke="${stroke}" stroke-width="1.6" stroke-linecap="round"/>`;
    }catch(_){
      svg.replaceChildren();
    }
  }
  function drawAllSparks(){
    document.querySelectorAll('svg.spark[data-spark]').forEach(drawSpark);
  }

  /* ========== 初期化 & 再バインド ========== */
  function boot(){
    bindAll();
    drawAllSparks();
  }

  window.addEventListener('load', boot);
  // HTMXでリスト差し替え時にも再バインド
  document.body.addEventListener('htmx:load', boot);
  window.addEventListener('resize', ()=>{ requestAnimationFrame(drawAllSparks); });

  // デバッグログ
  console.log('[holdings.js v120] ready');
})();