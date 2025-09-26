/* holdings.js v202
   - 横スワイプ: translateX固定方式（iOS Safari 安定化）
   - is-open が残る（固定）、別行を開くと自動で他行を閉じる
   - 詳細/編集/削除 ボタンすべて動作（バブリング停止）
   - 詳細を開いた後、カードタップで閉じる
   - インラインのスパーク描画（data-rateで色分岐）
   - HTMXで差し替え後も自動再バインド
*/

(() => {
  const NS='__swipe_v202__';
  const START_SLOP=8;     // スワイプ判定のしきい値(px)
  const GUARD_MS=280;     // 開閉直後のゴーストタップ抑止
  const now = () => Date.now();

  /* ========== 共通ユーティリティ ========== */
  function closeAll(except){
    document.querySelectorAll('[data-swipe].is-open').forEach(r=>{
      if (r===except) return;
      r.classList.remove('is-open');
      const a = r.querySelector('.actions');
      if (a){
        a.style.pointerEvents = 'none'; // 閉じたら無効化
        a.style.transform = '';         // CSSに委ねる
      }
    });
  }

  /* ========== 1行分のバインド ========== */
  function bindRow(row){
    if (!row || row[NS]) return; row[NS] = true;

    const actions = row.querySelector('.actions');
    const track   = row.querySelector('.track');
    const btnDetail = row.querySelector('[data-action="detail"]');
    const btns = row.querySelectorAll('.actions .item'); // 3ボタン

    if (!track || !actions) return;

    let guardUntil = 0;     // ゴーストタップ抑止
    let sx=0, sy=0, drag=false, horiz=false, dx=0;

    // パネル内操作は確実に効かせる（伝播停止）
    actions.addEventListener('click', e => e.stopPropagation(), {capture:true});
    actions.addEventListener('touchstart', e => e.stopPropagation(), {passive:true});

    // 各ボタン押下時も閉じないように（任意だがUX的に自然）
    btns.forEach(b=>{
      b.addEventListener('click', e=>{
        e.stopPropagation();
        // HTMXの削除ボタン等はここからそのまま発火
      }, {capture:true});
    });

    // 「詳細」ボタン：トグル開閉 → パネルは閉じる
    if (btnDetail){
      btnDetail.addEventListener('click', e=>{
        e.stopPropagation();
        row.classList.toggle('show-detail');
        row.classList.remove('is-open');
        actions.style.pointerEvents = 'none';
        guardUntil = now() + GUARD_MS;
      });
    }

    // カード本体タップ：パネル or 詳細を閉じる
    track.addEventListener('click', ()=>{
      if (now() < guardUntil) return;
      if (row.classList.contains('is-open')){
        row.classList.remove('is-open');
        actions.style.pointerEvents = 'none';
        guardUntil = now() + GUARD_MS;
      } else if (row.classList.contains('show-detail')){
        row.classList.remove('show-detail');
      }
    });

    // 外側タップで全閉
    document.addEventListener('click', (e)=>{
      if (now() < guardUntil) return;
      if (!e.target.closest('[data-swipe]')) closeAll();
    });

    // 追従（引き出し中だけ transform を直指定して視覚追従）
    const follow = (dist) => {
      const w = actions.getBoundingClientRect().width || 220;
      const clamped = Math.max(-w, Math.min(0, dist)); // [-w, 0]
      actions.style.transition = 'none';
      actions.style.transform  = `translateX(${100 + (clamped / w) * 100}%)`;
      // 十分引き出されたら操作可能に（誤タップ防止）
      actions.style.pointerEvents = (clamped < -12) ? 'auto' : 'none';
    };

    // 最終状態を確定（class管理に戻す）
    const snap = (open) => {
      actions.style.transition = '';
      if (open){
        closeAll(row);
        row.classList.add('is-open');
        actions.style.pointerEvents = 'auto';
      }else{
        row.classList.remove('is-open');
        actions.style.pointerEvents = 'none';
      }
      actions.style.transform = ''; // CSS（.row.is-open .actions）に任せる
      guardUntil = now() + GUARD_MS;
    };

    // タッチ開始
    track.addEventListener('touchstart', (e)=>{
      if (e.target.closest('.actions')) return;          // パネル上で開始しない
      if (!row.classList.contains('is-open')) closeAll(row);
      const t = e.touches[0]; sx=t.clientX; sy=t.clientY; dx=0;
      drag=true; horiz=false;
    }, {passive:true});

    // タッチ移動
    track.addEventListener('touchmove', (e)=>{
      if (!drag) return;
      const t=e.touches[0];
      const mx=t.clientX-sx, my=t.clientY-sy;
      if (!horiz){
        if (Math.abs(mx) < START_SLOP) return;           // しきい値未満
        if (Math.abs(mx) > Math.abs(my)) horiz=true;     // 横方向と判断
        else { drag=false; return; }                     // 縦スクロール
      }
      e.preventDefault();                                 // iOSのスクロール抑止
      dx = row.classList.contains('is-open') ? mx : -mx;  // 左に引くと正方向
      follow(-dx);                                        // “負で開く”として統一
    }, {passive:false});

    // タッチ終了
    track.addEventListener('touchend', ()=>{
      if (!drag) return; drag=false;
      const w=actions.getBoundingClientRect().width||220;
      const open = (-dx) > (w*0.35);                      // 35%で確定
      snap(open);
    });
  }

  function bindAllRows(){
    document.querySelectorAll('[data-swipe]').forEach(bindRow);
  }

  /* ========== スパーク描画（インライン） ========== */
  function drawInlineSpark(svg){
    // data-spark='[ ... ]' or data-spark='1,1.02,0.98,...' に対応
    let arr = [];
    const raw = svg.getAttribute('data-spark') || '[]';
    try{
      arr = Array.isArray(raw) ? raw : (raw.trim().startsWith('[') ? JSON.parse(raw) : raw.split(',').map(s=>parseFloat(s)));
    }catch(_){}
    arr = (arr || []).filter(v => typeof v === 'number' && isFinite(v));
    if (arr.length < 2){ svg.replaceChildren(); return; }

    const rate = parseFloat(svg.getAttribute('data-rate')||'0');
    const stroke = (isFinite(rate) && rate < 0) ? '#f87171' : '#34d399';

    const vb = svg.viewBox.baseVal;
    const W = vb && vb.width ? vb.width : 96;
    const H = vb && vb.height ? vb.height : 24;
    const pad=1;
    let min=Math.min(...arr), max=Math.max(...arr);
    if (min===max){ min-=1e-6; max+=1e-6; }

    const nx=i => pad + (i*(W-2*pad)/(arr.length-1));
    const ny=v => H - pad - ((v-min)/(max-min))*(H-2*pad);

    let d=`M${nx(0)},${ny(arr[0])}`;
    for(let i=1;i<arr.length;i++){ d+=` L${nx(i)},${ny(arr[i])}`; }
    svg.innerHTML = `<path d="${d}" fill="none" stroke="${stroke}" stroke-width="1.6" stroke-linecap="round" />`;
  }

  function drawAllInlineSparks(){
    document.querySelectorAll('svg.spark').forEach(drawInlineSpark);
  }

  /* ========== Boot ========== */
  function boot(){
    bindAllRows();
    drawAllInlineSparks();
  }

  // 初期ロード / HTMX置換後 / リサイズ
  window.addEventListener('load', boot);
  document.body.addEventListener('htmx:load', boot);
  window.addEventListener('resize', () => { drawAllInlineSparks(); });

  console.log('[holdings.js v202] ready');
})();