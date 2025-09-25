/* holdings.js v21 — iOS Safari安定版
   - 左スワイプでアクション固定（初回/以降とも安定）
   - ゴーストタップ抑止（suppressUntil）
   - スパークライン描画（.spark-wrap[data-spark]）
   - HTMX後の再バインド対応
*/

(() => {
  const NS = '__swipe_v21__';
  const GUARD_MS = 500;         // 開閉直後のゴーストタップ無視
  const START_SLOP = 8;         // スワイプ開始閾値(px)

  const px = v => `${v}px`;
  const now = () => Date.now();

  function getOpenW(a){
    const v = getComputedStyle(a).getPropertyValue('--open-w').trim().replace('px','');
    return parseFloat(v || '220');
  }

  /* ========= スワイプ（touchベース） ========= */
  function bindRow(row){
    if (!row || row[NS]) return;
    row[NS] = true;

    const actions = row.querySelector('.actions');
    const track   = row.querySelector('.track');
    const detail  = row.querySelector('[data-action="detail"]');
    if (!actions || !track) return;

    let openW = getOpenW(actions);
    let suppressUntil = 0;

    // 初期は閉じる
    actions.style.right = px(-openW);
    actions.style.pointerEvents = 'none';
    row.classList.remove('is-open');

    const openHard = () => {
      actions.style.transition = 'right .18s ease-out';
      row.classList.add('is-open');
      actions.style.right = '0px';
      actions.style.pointerEvents = 'auto';
      suppressUntil = now() + GUARD_MS;
    };
    const closeHard = () => {
      actions.style.transition = 'right .18s ease-out';
      row.classList.remove('is-open');
      actions.style.right = px(-getOpenW(actions));   // 常に最新幅
      actions.style.pointerEvents = 'none';
      suppressUntil = now() + GUARD_MS;
    };
    const closeAll = (except) => {
      document.querySelectorAll('[data-swipe].is-open').forEach(r=>{
        if (r===except) return;
        const a=r.querySelector('.actions'); if(!a) return;
        a.style.transition = 'right .18s ease-out';
        r.classList.remove('is-open');
        a.style.right = px(-getOpenW(a));
        a.style.pointerEvents = 'none';
      });
    };

    // パネル内クリックは伝播させない（ボタン操作可能に）
    actions.addEventListener('click', e => e.stopPropagation(), {capture:true});

    // 詳細ボタン：トグル後にパネルは閉じる
    if (detail){
      detail.addEventListener('click',(e)=>{
        e.stopPropagation();
        row.classList.toggle('show-detail');
        closeHard();
      });
    }

    // 行外クリックで全閉
    document.addEventListener('click', (e)=>{
      if (now() < suppressUntil) return;
      if (!e.target.closest('[data-swipe]')) closeAll();
    });

    // 行本体タップで閉じる（詳細が開いていれば閉じる）
    track.addEventListener('click',(e)=>{
      if (now() < suppressUntil) return;
      if (e.target.closest('.actions,.item,a,button')) return;
      if (row.classList.contains('show-detail')) row.classList.remove('show-detail');
      if (row.classList.contains('is-open')) closeHard();
    });

    // ====== touch スワイプ ======
    let startX=0, startY=0, drag=false, horiz=false, baseRight=0;

    track.addEventListener('touchstart',(e)=>{
      if (e.target.closest('.actions')) return;
      // 他が開いていて自分が閉じているなら他を閉じる
      if (!row.classList.contains('is-open')) closeAll(row);

      const t = e.touches[0];
      startX=t.clientX; startY=t.clientY;
      drag=true; horiz=false;
      openW = getOpenW(actions);
      baseRight = row.classList.contains('is-open') ? 0 : -openW;
      actions.style.transition = 'none';
    }, {passive:true});

    track.addEventListener('touchmove',(e)=>{
      if (!drag) return;
      const t = e.touches[0];
      const dx = t.clientX - startX;
      const dy = t.clientY - startY;

      if (!horiz){
        if (Math.abs(dx) < START_SLOP) return;
        if (Math.abs(dx) > Math.abs(dy)) horiz = true;
        else { // 縦スクロール扱い→キャンセル
          drag=false; actions.style.transition='';
          return;
        }
      }
      // 横スワイプ中はスクロールを止める
      e.preventDefault();
      let nr = baseRight - dx;        // 左へで 0 に近づく
      if (nr > 0) nr = 0;
      if (nr < -openW) nr = -openW;
      actions.style.right = px(nr);
    }, {passive:false});

    track.addEventListener('touchend',()=>{
      if (!drag) return;
      drag=false;
      const cur = parseFloat(getComputedStyle(actions).right) || -openW;
      const willOpen = (cur > -openW/2);
      if (willOpen) openHard(); else closeHard();
    });
  }

  function bindAll(){
    document.querySelectorAll('[data-swipe]').forEach(bindRow);
  }

  /* ========= スパークライン描画 =========
     .spark-wrap[data-spark="1,1.1,0.9,..."][data-pos="1|0"]
  */
  function drawSpark(el){
    const raw = (el.getAttribute('data-spark')||'').trim();
    if (!raw) { el.innerHTML=''; return; }
    const arr = raw.split(',').map(s=>parseFloat(s)).filter(v=>isFinite(v));
    if (arr.length < 2){ el.innerHTML=''; return; }

    const w = el.clientWidth  || 64;
    const h = el.clientHeight || 20;
    const pad = 1;

    let min = Math.min(...arr), max = Math.max(...arr);
    if (min === max){ min -= 1e-6; max += 1e-6; }

    const nx = i => pad + (i * (w - 2*pad) / (arr.length - 1));
    const ny = v => h - pad - ((v - min) / (max - min)) * (h - 2*pad);

    let d = `M${nx(0)},${ny(arr[0])}`;
    for (let i=1;i<arr.length;i++){ d += ` L${nx(i)},${ny(arr[i])}`; }

    const pos = el.getAttribute('data-pos') === '1';
    const stroke = pos ? '#34d399' : '#f87171';

    el.innerHTML = `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">
      <path d="${d}" fill="none" stroke="${stroke}" stroke-width="1.5"/>
    </svg>`;
  }

  function drawAllSparks(){
    document.querySelectorAll('.spark-wrap[data-spark]').forEach(drawSpark);
  }

  // ===== 起動 & HTMX再描画対応 & リサイズ =====
  function boot(){
    bindAll();
    drawAllSparks();
  }

  let rT=null;
  window.addEventListener('resize', ()=>{
    if (rT) cancelAnimationFrame(rT);
    rT = requestAnimationFrame(drawAllSparks);
  });

  window.addEventListener('load', boot);
  document.body.addEventListener('htmx:load', boot);

  console.log('[holdings.js v21] ready');
})();