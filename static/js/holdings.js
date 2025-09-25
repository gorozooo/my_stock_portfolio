/* holdings.js v106 — translateX方式で“確実固定” + HTMX対応
   - スワイプで .row に is-open を付与 → パネル固定（iOS Safari安定）
   - 外側タップで閉じる / パネル内は行クリックへバブリングさせない
   - actions内のclickは capture=false で stopPropagation のみに変更（HTMXのhx-postが効く）
   - 削除ボタンの二度押し防止＆ローディング表示を追加
   - スパークライン描画も同梱（data-spark='[...]', data-rate）
*/
(() => {
  const START_SLOP = 8;           // スワイプ判定のしきい値(px)
  const GUARD_MS   = 320;         // 開閉直後のゴーストタップ抑止
  const now = () => Date.now();

  /* ========== 共通ユーティリティ ========== */
  function closeAll(except){
    document.querySelectorAll('[data-swipe].is-open').forEach(r=>{
      if (r === except) return;
      r.classList.remove('is-open');
    });
  }

  /* ========== 1行にバインド ========== */
  function bindRow(row){
    if (!row || row.__bound_v106) return;
    row.__bound_v106 = true;

    const actions = row.querySelector('.actions');
    const track   = row.querySelector('.track');
    const detail  = row.querySelector('[data-action="detail"]');
    const delBtn  = row.querySelector('.item.delete');

    if (!actions || !track) return;

    let guardUntil = 0;   // ゴーストタップ抑止タイムスタンプ
    let sx=0, sy=0, drag=false, horiz=false, dx=0;

    // パネル内のクリックは「行のクリック」へは伝えない（BUT: HTMXには届くように capture=false）
    actions.addEventListener('click', (e) => {
      e.stopPropagation(); // 行(track)のclickは止める
      // ここでは preventDefault しない → a/hx-post はそのまま動く
    }, { capture:false });

    // 詳細トグル
    if (detail){
      detail.addEventListener('click', (e)=>{
        e.stopPropagation();                 // 行clickへは伝えない
        row.classList.toggle('show-detail'); // 詳細表示/非表示
        row.classList.remove('is-open');     // パネルは閉じる
        guardUntil = now() + GUARD_MS;
      });
    }

    // 削除：二度押しガード＋ローディング表示（HTMXは通常通り発火）
    if (delBtn){
      delBtn.addEventListener('click', (e)=>{
        // ここでは stopPropagation 済み（actionsのリスナー）だが、HTMXは要素自身で拾うのでOK
        if (delBtn.dataset.busy === '1') { e.preventDefault(); return; }
        delBtn.dataset.busy = '1';
        const prev = delBtn.innerHTML;
        delBtn.innerHTML = '⏳<span>削除</span>';

        // HTMX完了/エラーで元に戻す
        const restore = () => { delBtn.dataset.busy = '0'; delBtn.innerHTML = prev; };
        delBtn.addEventListener('htmx:afterOnLoad', restore, { once:true });
        delBtn.addEventListener('htmx:responseError', restore, { once:true });
        delBtn.addEventListener('htmx:sendError', restore, { once:true });
      });
    }

    // 本文クリック：開いていたら閉じる／詳細開いてたら閉じる
    track.addEventListener('click', () => {
      if (now() < guardUntil) return;
      if (row.classList.contains('is-open')){
        row.classList.remove('is-open');
        guardUntil = now() + GUARD_MS;
      } else if (row.classList.contains('show-detail')){
        row.classList.remove('show-detail');
      }
    });

    // ドキュメント外側タップで全閉
    document.addEventListener('click', (e)=>{
      if (now() < guardUntil) return;
      if (!e.target.closest('[data-swipe]')) closeAll();
    });

    // ===== タッチスワイプ（本文(track)上のみ） =====
    const follow = (dist) => {
      // dist: 正→右 / 負→左。右側から出すUIなので負方向のみ引き出し
      const w = actions.getBoundingClientRect().width || 220;
      const clamped = Math.max(-w, Math.min(0, dist)); // [-w, 0]
      actions.style.transition = 'none';
      // 初期は translateX(100%) → clamped=-w で 0%, clamped=0 で 100%
      actions.style.transform  = `translateX(${100 + (clamped / w) * 100}%)`;
      if (clamped < -12) actions.style.pointerEvents = 'auto';
    };

    const snap = (open) => {
      actions.style.transition = '';
      if (open){
        closeAll(row);
        row.classList.add('is-open');
      }else{
        row.classList.remove('is-open');
      }
      // CSSに任せる
      actions.style.transform = '';
      guardUntil = now() + GUARD_MS;
    };

    track.addEventListener('touchstart', (e)=>{
      if (e.target.closest('.actions')) return;     // パネル内からの開始は無視
      if (!row.classList.contains('is-open')) closeAll(row);
      const t = e.touches[0]; sx = t.clientX; sy = t.clientY; dx = 0;
      drag = true; horiz = false;
    }, { passive:true });

    track.addEventListener('touchmove', (e)=>{
      if (!drag) return;
      const t = e.touches[0];
      const mx = t.clientX - sx;
      const my = t.clientY - sy;
      if (!horiz){
        if (Math.abs(mx) < START_SLOP) return;
        if (Math.abs(mx) > Math.abs(my)) horiz = true; else { drag=false; return; }
      }
      e.preventDefault(); // iOSの縦スクロールを抑止
      dx = row.classList.contains('is-open') ? mx : -mx;
      follow(-dx); // “負方向で開く”に統一
    }, { passive:false });

    track.addEventListener('touchend', ()=>{
      if (!drag) return; drag=false;
      const w = actions.getBoundingClientRect().width || 220;
      const openedEnough = (-dx) > (w * 0.35); // 35%で確定
      snap(openedEnough);
    });
  }

  function bindAll(){
    document.querySelectorAll('[data-swipe]').forEach(bindRow);
  }

  /* ========== スパークライン描画（svg.spark + data-spark='[...]'） ========== */
  function drawSpark(svg){
    try{
      const raw = svg.getAttribute('data-spark') || '[]';
      const arr = JSON.parse(raw);
      if (!Array.isArray(arr) || arr.length < 2){ svg.replaceChildren(); return; }

      const rate = parseFloat(svg.getAttribute('data-rate')||'0');
      const stroke = (isFinite(rate) && rate < 0) ? '#f87171' : '#34d399';

      const vb = svg.viewBox.baseVal;
      const W = vb && vb.width  ? vb.width  : 96;
      const H = vb && vb.height ? vb.height : 24;
      const pad = 1;

      let min = Math.min(...arr), max = Math.max(...arr);
      if (min === max){ min -= 1e-6; max += 1e-6; }

      const nx = i => pad + (i * (W - 2*pad) / (arr.length - 1));
      const ny = v => H - pad - ((v - min) / (max - min)) * (H - 2*pad);

      let d = `M${nx(0)},${ny(arr[0])}`;
      for (let i=1;i<arr.length;i++){ d += ` L${nx(i)},${ny(arr[i])}`; }

      svg.innerHTML =
        `<path d="${d}" fill="none" stroke="${stroke}" stroke-width="1.6" stroke-linecap="round" />`;
    }catch(_){
      svg.replaceChildren();
    }
  }
  function drawAllSparks(){
    document.querySelectorAll('svg.spark[data-spark]').forEach(drawSpark);
  }

  /* ========== Boot ========== */
  function boot(){ bindAll(); drawAllSparks(); }

  window.addEventListener('load', boot);
  document.body.addEventListener('htmx:load', boot);
  window.addEventListener('resize', ()=>{ requestAnimationFrame(drawAllSparks); });

  console.log('[holdings.js v106] ready');
})();