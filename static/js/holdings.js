/* holdings.js v103 — translateX方式で“確実に固定”
   - スワイプで .row に is-open を付与 → パネルを固定
   - 外側タップで閉じる / パネル内はバブリング停止
   - iOS Safari のゴーストタップを抑制
   - スパークライン描画も同梱
*/
(() => {
  const NS='__swipe_v103__';
  const START_SLOP=8;          // スワイプ判定のしきい値
  const GUARD_MS=320;          // 開閉直後のゴーストタップ抑止
  const now = () => Date.now();

  /* ===== 共通 ===== */
  function closeAll(except){
    document.querySelectorAll('[data-swipe].is-open').forEach(r=>{
      if (r===except) return;
      r.classList.remove('is-open');
    });
  }

  /* ===== スワイプ行 ===== */
  function bindRow(row){
    if (!row || row[NS]) return; row[NS]=true;

    const actions = row.querySelector('.actions');
    const track   = row.querySelector('.track');
    const detail  = row.querySelector('[data-action="detail"]');
    if (!actions || !track) return;

    let guardUntil = 0;

    // パネル内クリックはバブリング停止（ボタン押下を確実に）
    actions.addEventListener('click', e => e.stopPropagation(), {capture:true});

    // 詳細トグル → パネルは閉じてロック解除
    if (detail){
      detail.addEventListener('click', e=>{
        e.stopPropagation();
        row.classList.toggle('show-detail');
        row.classList.remove('is-open');
        guardUntil = now() + GUARD_MS;
      });
    }

    // 本文タップで閉じるだけ（開いてるとき）
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

    // ===== タッチスワイプ（translateXで追従 → 判定後に class を決定） =====
    let sx=0, sy=0, drag=false, horiz=false, dx=0;

    const follow = (dist) => {
      // dist: 正→右/ 負→左。右に出すUIなので、負方向のみ追従
      const w = actions.getBoundingClientRect().width || 220;
      const clamped = Math.max(-w, Math.min(0, dist)); // [-w, 0]
      // 追従は本文(track)を動かさず、パネル(actions)を一時的に引き出す
      actions.style.transition = 'none';
      actions.style.transform  = `translateX(${100 + (clamped / w) * 100}%)`;
      // pointer-eventsは “十分開いたら” 有効化（誤タップ防止）
      if (clamped < -12) actions.style.pointerEvents = 'auto';
    };

    const snap = (open) => {
      actions.style.transition = '';
      // 最終状態は class だけで管理（CSSが translateX(0/100%) を適用）
      if (open) {
        closeAll(row);
        row.classList.add('is-open');
      } else {
        row.classList.remove('is-open');
      }
      // 一旦 transform をCSSに任せ直す
      actions.style.transform = '';
      // ゴーストタップ抑止
      guardUntil = now() + GUARD_MS;
    };

    track.addEventListener('touchstart', (e)=>{
      if (e.target.closest('.actions')) return;              // パネル内から開始しない
      if (!row.classList.contains('is-open')) closeAll(row); // 別行を閉じる
      const t = e.touches[0]; sx=t.clientX; sy=t.clientY; dx=0;
      drag=true; horiz=false;
    }, {passive:true});

    track.addEventListener('touchmove', (e)=>{
      if (!drag) return;
      const t = e.touches[0];
      const mx = t.clientX - sx;
      const my = t.clientY - sy;
      if (!horiz){
        if (Math.abs(mx) < START_SLOP) return;
        if (Math.abs(mx) > Math.abs(my)) horiz=true; else { drag=false; return; }
      }
      e.preventDefault(); // iOS Safari スクロール抑止
      // 右から出すUIなので、左方向（mx<0）で引き出す
      dx = row.classList.contains('is-open') ? mx : -mx;
      follow(-dx); // “負方向で開く” として統一
    }, {passive:false});

    track.addEventListener('touchend', ()=>{
      if (!drag) return; drag=false;
      const w = actions.getBoundingClientRect().width || 220;
      const openedEnough = (-dx) > (w * 0.35); // 35%引き出しで確定
      snap(openedEnough);
    });
  }

  function bindAll(){ document.querySelectorAll('[data-swipe]').forEach(bindRow); }

  /* ===== スパーク（svg.spark + data-spark='[...]'） ===== */
  function drawSpark(svg){
    try{
      const raw = svg.getAttribute('data-spark')||'[]';
      const arr = JSON.parse(raw);
      if(!Array.isArray(arr) || arr.length < 2){ svg.replaceChildren(); return; }
      const rate = parseFloat(svg.getAttribute('data-rate')||'0');
      const stroke = (isFinite(rate) && rate<0) ? '#f87171' : '#34d399';
      const vb = svg.viewBox.baseVal;
      const W = vb && vb.width  ? vb.width  : 96;
      const H = vb && vb.height ? vb.height : 24;
      const pad=1;
      let min=Math.min(...arr), max=Math.max(...arr);
      if (min===max){ min-=1e-6; max+=1e-6; }
      const nx=i => pad + (i*(W-2*pad)/(arr.length-1));
      const ny=v => H - pad - ((v-min)/(max-min))*(H-2*pad);
      let d=`M${nx(0)},${ny(arr[0])}`;
      for(let i=1;i<arr.length;i++){ d+=` L${nx(i)},${ny(arr[i])}`; }
      svg.innerHTML = `<path d="${d}" fill="none" stroke="${stroke}" stroke-width="1.6" stroke-linecap="round" />`;
    }catch(_){ svg.replaceChildren(); }
  }
  function drawAllSparks(){ document.querySelectorAll('svg.spark[data-spark]').forEach(drawSpark); }

  /* ===== Boot ===== */
  function boot(){ bindAll(); drawAllSparks(); }

  // 画面更新時も再バインド
  window.addEventListener('load', boot);
  document.body.addEventListener('htmx:load', boot);
  window.addEventListener('resize', ()=>{ requestAnimationFrame(drawAllSparks); });

  console.log('[holdings.js v103] ready');
})();