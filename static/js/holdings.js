/* holdings.js v110
   - iOS/Safari 安定動作のため「しきい値超えで即スナップ」
   - 初回から固定、2回目以降も固定、詳細開閉後も再固定
   - スパークラインは JSON でも "1,2,3" でもOK
*/
(() => {
  const THRESH = 24;       // px: 左右スワイプの確定しきい値
  const GUARD_MS = 250;    // 開閉直後のゴーストタップ抑止
  const now = () => Date.now();

  function closeAll(except){
    document.querySelectorAll('[data-swipe].is-open').forEach(r=>{
      if (except && r===except) return;
      r.classList.remove('is-open');
    });
  }

  function bindRow(row){
    if (!row || row.__bound_v110) return; row.__bound_v110 = true;

    const actions = row.querySelector('.actions');
    const track   = row.querySelector('.track');
    const detail  = row.querySelector('[data-action="detail"]');
    if (!actions || !track) return;

    let guardUntil = 0;
    let sx=0, sy=0, dragging=false, horiz=false;

    // パネル内クリックは行クリックへバブリングさせない
    actions.addEventListener('click', e => e.stopPropagation(), {capture:true});

    // 詳細トグル（押したらパネルは閉じておく）
    if (detail){
      detail.addEventListener('click', e=>{
        e.stopPropagation();
        row.classList.toggle('show-detail');
        row.classList.remove('is-open');
        guardUntil = now() + GUARD_MS;
      });
    }

    // 本文タップ＝閉じるだけ（開いていたら）
    track.addEventListener('click', ()=>{
      if (now() < guardUntil) return;
      if (row.classList.contains('is-open')){
        row.classList.remove('is-open');
        guardUntil = now() + GUARD_MS;
      } else if (row.classList.contains('show-detail')){
        row.classList.remove('show-detail');
      }
    });

    // ドキュメント外側で全閉
    document.addEventListener('click', (e)=>{
      if (now() < guardUntil) return;
      if (!e.target.closest('[data-swipe]')) closeAll();
    });

    // ===== スワイプ（“即スナップ”ロジック） =====
    track.addEventListener('touchstart', (e)=>{
      if (e.target.closest('.actions')) return;
      const t = e.touches[0];
      sx = t.clientX; sy = t.clientY;
      dragging = true; horiz = false;
      // 他行は閉じる（これが“固定”の体感を上げる）
      if (!row.classList.contains('is-open')) closeAll(row);
    }, {passive:true});

    track.addEventListener('touchmove', (e)=>{
      if (!dragging) return;
      const t = e.touches[0];
      const mx = t.clientX - sx;   // 右が + / 左が -
      const my = t.clientY - sy;

      if (!horiz){
        if (Math.abs(mx) < 8) return;
        if (Math.abs(mx) > Math.abs(my)) horiz = true; else { dragging = false; return; }
      }
      e.preventDefault(); // iOSスクロール抑止

      // 状態別 “確定”
      const wasOpen = row.classList.contains('is-open');
      if (!wasOpen && mx <= -THRESH){
        row.classList.add('is-open');       // 左へしきい値超 → 開く
        guardUntil = now() + GUARD_MS;
        dragging = false;
        return;
      }
      if (wasOpen && mx >= THRESH){
        row.classList.remove('is-open');    // 右へしきい値超 → 閉じる
        guardUntil = now() + GUARD_MS;
        dragging = false;
        return;
      }
      // しきい値内は何もしない（スナップのみ）
    }, {passive:false});

    track.addEventListener('touchend', ()=>{
      dragging = false;
    });
  }

  function bindAll(){ document.querySelectorAll('[data-swipe]').forEach(bindRow); }

  // ===== スパーク描画（JSON/CSV両対応） =====
  function parseSpark(raw){
    if (!raw) return [];
    try{
      const j = JSON.parse(raw);
      if (Array.isArray(j)) return j.map(Number).filter(v=>isFinite(v));
    }catch(_){}
    // カンマ区切り
    return String(raw).split(',').map(s=>parseFloat(s)).filter(v=>isFinite(v));
  }
  function drawSpark(svg){
    const raw = svg.getAttribute('data-spark') || '';
    const arr = parseSpark(raw);
    if (arr.length < 2){ svg.replaceChildren(); return; }

    const rate = parseFloat(svg.getAttribute('data-rate')||'0');
    const stroke = (isFinite(rate) && rate < 0) ? '#f87171' : '#34d399';

    const vb = svg.viewBox.baseVal;
    const W = (vb && vb.width)  ? vb.width  : 96;
    const H = (vb && vb.height) ? vb.height : 24;
    const pad = 1;

    let min = Math.min(...arr), max = Math.max(...arr);
    if (min === max){ min -= 1e-6; max += 1e-6; }

    const nx = i => pad + (i*(W-2*pad)/(arr.length-1));
    const ny = v => H - pad - ((v-min)/(max-min))*(H-2*pad);

    let d = `M${nx(0)},${ny(arr[0])}`;
    for (let i=1;i<arr.length;i++){ d += ` L${nx(i)},${ny(arr[i])}`; }

    svg.innerHTML = `<path d="${d}" fill="none" stroke="${stroke}" stroke-width="1.6" stroke-linecap="round"/>`;
  }
  function drawAllSparks(){ document.querySelectorAll('svg.spark[data-spark]').forEach(drawSpark); }

  // ===== Boot =====
  function boot(){ bindAll(); drawAllSparks(); }

  window.addEventListener('load', boot);
  document.body.addEventListener('htmx:load', boot);
  window.addEventListener('resize', ()=>{ requestAnimationFrame(drawAllSparks); });

  console.log('[holdings.js v110] ready');
})();