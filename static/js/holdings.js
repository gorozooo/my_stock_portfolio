/* holdings.js v102 — template( svg.spark + data-spark JSON )対応
   - 左スワイプ固定（初回/2回目以降も安定）
   - iOS Safari: touchベース＆ゴーストタップ抑止
   - スパークライン: <svg class="spark" data-spark='[...]' data-rate='...'>
   - HTMX差し替え後も自動再バインド
*/
(() => {
  const NS='__swipe_v102__';
  const GUARD_MS=500;
  const START_SLOP=8;
  const px=v=>`${v}px`;
  const now=()=>Date.now();

  function getOpenW(a){
    const v=getComputedStyle(a).getPropertyValue('--open-w').trim().replace('px','');
    return parseFloat(v||'220');
  }

  /* ===== スワイプ ===== */
  function bindRow(row){
    if(!row || row[NS]) return; row[NS]=true;

    const actions=row.querySelector('.actions');
    const track  =row.querySelector('.track');
    const detail =row.querySelector('[data-action="detail"]');
    if(!actions||!track) return;

    let suppressUntil=0;

    const openHard = () => {
      actions.style.transition='right .18s ease-out';
      row.classList.add('is-open');
      actions.style.right='0px';
      actions.style.pointerEvents='auto';
      suppressUntil=now()+GUARD_MS;
    };
    const closeHard = () => {
      actions.style.transition='right .18s ease-out';
      row.classList.remove('is-open');
      actions.style.right=px(-getOpenW(actions));
      actions.style.pointerEvents='none';
      suppressUntil=now()+GUARD_MS;
    };
    const closeAll = (except) => {
      document.querySelectorAll('[data-swipe].is-open').forEach(r=>{
        if(r===except) return;
        const a=r.querySelector('.actions'); if(!a) return;
        a.style.transition='right .18s ease-out';
        r.classList.remove('is-open');
        a.style.right=px(-getOpenW(a));
        a.style.pointerEvents='none';
      });
    };

    // 初期は閉じる
    actions.style.right=px(-getOpenW(actions));
    actions.style.pointerEvents='none';
    row.classList.remove('is-open');

    // パネル内クリックはバブリング止める（ボタン押下可）
    actions.addEventListener('click',e=>e.stopPropagation(),{capture:true});

    // 詳細ボタン（ある場合）：トグル後はパネル閉じる
    if(detail){
      detail.addEventListener('click',e=>{
        e.stopPropagation();
        row.classList.toggle('show-detail');
        closeHard();
      });
    }

    // 外側タップで全閉
    document.addEventListener('click',(e)=>{
      if(now()<suppressUntil) return;
      if(!e.target.closest('[data-swipe]')) closeAll();
    });

    // 本体タップで閉じる（詳細開いてたら閉じる）
    track.addEventListener('click',(e)=>{
      if(now()<suppressUntil) return;
      if(e.target.closest('.actions,.item,a,button')) return;
      if(row.classList.contains('show-detail')) row.classList.remove('show-detail');
      if(row.classList.contains('is-open')) closeHard();
    });

    // ===== touchスワイプ =====
    let startX=0,startY=0,drag=false,horiz=false,baseRight=0,openW=getOpenW(actions);

    track.addEventListener('touchstart',(e)=>{
      if(e.target.closest('.actions')) return;
      if(!row.classList.contains('is-open')) closeAll(row);
      const t=e.touches[0]; startX=t.clientX; startY=t.clientY;
      drag=true; horiz=false; openW=getOpenW(actions);
      baseRight=row.classList.contains('is-open')?0:-openW;
      actions.style.transition='none';
    },{passive:true});

    track.addEventListener('touchmove',(e)=>{
      if(!drag) return;
      const t=e.touches[0]; const dx=t.clientX-startX; const dy=t.clientY-startY;
      if(!horiz){
        if(Math.abs(dx)<START_SLOP) return;
        if(Math.abs(dx)>Math.abs(dy)) horiz=true; else {drag=false; actions.style.transition=''; return;}
      }
      e.preventDefault();
      let nr=baseRight-dx; if(nr>0) nr=0; if(nr<-openW) nr=-openW;
      actions.style.right=px(nr);
    },{passive:false});

    track.addEventListener('touchend',()=>{
      if(!drag) return; drag=false;
      const cur=parseFloat(getComputedStyle(actions).right)||-openW;
      (cur>-openW/2)?openHard():closeHard();
    });
  }

  function bindAll(){
    document.querySelectorAll('[data-swipe]').forEach(bindRow);
  }

  /* ===== スパークライン（svg.spark + data-spark='[...]'） ===== */
  function drawSpark(svg){
    try{
      const raw = svg.getAttribute('data-spark')||'';
      const arr = JSON.parse(raw);
      if(!Array.isArray(arr) || arr.length<2){ svg.replaceChildren(); return; }
      const rate = parseFloat(svg.getAttribute('data-rate')||'0');
      const stroke = (isFinite(rate) && rate<0) ? '#f87171' : '#34d399';

      const vb = svg.viewBox.baseVal;
      const W = vb && vb.width  ? vb.width  : 96;
      const H = vb && vb.height ? vb.height : 24;
      const pad=1;

      let min=Math.min(...arr), max=Math.max(...arr);
      if(min===max){ min-=1e-6; max+=1e-6; }

      const nx=i => pad + (i*(W-2*pad)/(arr.length-1));
      const ny=v => H - pad - ((v-min)/(max-min))*(H-2*pad);

      let d=`M${nx(0)},${ny(arr[0])}`;
      for(let i=1;i<arr.length;i++){ d+=` L${nx(i)},${ny(arr[i])}`; }

      svg.innerHTML = `<path d="${d}" fill="none" stroke="${stroke}" stroke-width="1.6" stroke-linecap="round" />`;
    }catch(_){ svg.replaceChildren(); }
  }
  function drawAllSparks(){
    document.querySelectorAll('svg.spark[data-spark]').forEach(drawSpark);
  }

  /* ===== Boot / Rebind ===== */
  function boot(){ bindAll(); drawAllSparks(); }

  // リサイズでスパーク再描画（幅はviewBox基準だが、親サイズ影響を受けるケースの保険）
  let rT=null;
  window.addEventListener('resize', ()=>{
    if(rT) cancelAnimationFrame(rT);
    rT=requestAnimationFrame(drawAllSparks);
  });

  window.addEventListener('load', boot);
  document.body.addEventListener('htmx:load', boot);

  console.log('[holdings.js v102] ready');
})();