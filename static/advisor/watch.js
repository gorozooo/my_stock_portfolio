/* watch.js v27 — コンパクト一覧 = 銘柄+コード / 総合 / AI / IN目安
   詳細はボトムシートで board 風カードを再利用
*/
const $  = (s)=>document.querySelector(s);
const $$ = (s)=>document.querySelectorAll(s);

function abs(path){ return new URL(path, window.location.origin).toString(); }

/* ---------------- Toast（boardと同仕様） ---------------- */
function computeToastBottomPx(){
  let insetBottom = 0;
  if (window.visualViewport){
    const diff = window.innerHeight - window.visualViewport.height;
    insetBottom = Math.max(0, Math.round(diff));
  }
  return insetBottom + 140; // 下タブ/ホームバー分を回避
}
function toast(msg){
  const t = document.createElement('div');
  t.style.position='fixed';
  t.style.left='50%'; t.style.transform='translateX(-50%)';
  t.style.bottom = computeToastBottomPx()+'px';
  t.style.background='rgba(0,0,0,.85)'; t.style.color='#fff';
  t.style.padding='10px 16px'; t.style.borderRadius='14px';
  t.style.boxShadow='0 6px 20px rgba(0,0,0,.4)'; t.style.zIndex='9999';
  t.style.opacity='0'; t.style.pointerEvents='none'; t.style.transition='opacity .25s';
  t.textContent = msg; document.body.appendChild(t);
  requestAnimationFrame(()=> t.style.opacity='1');
  const onV = ()=> t.style.bottom = computeToastBottomPx()+'px';
  if (window.visualViewport) window.visualViewport.addEventListener('resize', onV);
  setTimeout(()=>{ t.style.opacity='0'; setTimeout(()=>{ if(window.visualViewport) window.visualViewport.removeEventListener('resize', onV); t.remove();}, 250);}, 1800);
}

/* ---------------- API ---------------- */
async function getJSON(url){
  const r = await fetch(abs(url), {headers:{'Cache-Control':'no-store'}});
  if(!r.ok) throw new Error(`HTTP ${r.status} ${await r.text().catch(()=> '')}`);
  return await r.json();
}
async function postJSON(url, body){
  const r = await fetch(abs(url), {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body||{})});
  if(!r.ok) throw new Error(`HTTP ${r.status} ${await r.text().catch(()=> '')}`);
  return await r.json();
}

/* ---------------- 共通表示補助 ---------------- */
function star5(prob01){
  const s = Math.round((prob01??0)*5);
  const clamp = Math.max(0, Math.min(5, s));
  return '★★★★★'.slice(0, clamp) + '☆☆☆☆☆'.slice(0, 5 - clamp);
}
function wkChip(code){
  if(code==='up') return {icon:'↗️', label:'上向き'};
  if(code==='down') return {icon:'↘️', label:'下向き'};
  return {icon:'➡️', label:'横ばい'};
}

/* ---------------- 詳細カード（boardの見た目をコピー） ---------------- */
function cardHTML(item){
  const themeScore = Math.round((item.theme_score??0)*100);
  const wk = wkChip(item.weekly_trend||'flat');
  const aiStars = star5(item.ai_win_prob??0);
  const tpPct = Number.isFinite(item.tp_pct)? Math.round((item.tp_pct??0)*100) : null;
  const slPct = Number.isFinite(item.sl_pct)? Math.round((item.sl_pct??0)*100) : null;
  const tpProb = (item.ai_tp_prob!=null) ? Math.round((item.ai_tp_prob)*100) : '–';
  const slProb = (item.ai_sl_prob!=null) ? Math.round((item.ai_sl_prob)*100) : '–';
  const inHint = (item.entry_price_hint!=null) ? (item.entry_price_hint).toLocaleString() : '–';
  const tpPrice = (item.tp_price!=null) ? item.tp_price.toLocaleString() : '–';
  const slPrice = (item.sl_price!=null) ? item.sl_price.toLocaleString() : '–';
  const reasons = (item.reason_details && item.reason_details.length)
      ? item.reason_details
      : ((item.reason_summary||'').split('/').map(s=>s.trim()).filter(Boolean));

  return `
    <article class="wcard">
      <div class="w-title">${item.name} <span class="code">(${item.ticker})</span></div>
      <div class="w-seg">週足：${wk.icon} ${wk.label}</div>

      <div class="w-badges">
        <span class="badge-chip blue">#${item.theme_label||'-'} ${themeScore}点</span>
      </div>

      <div class="w-overall">
        <div>総合評価：<b>${item.overall_score ?? 0}</b> 点</div>
        <div>AI信頼度：${aiStars}</div>
      </div>

      <div class="w-action">行動：ウォッチ中</div>

      <ul class="w-list">
        ${reasons.map(s=>`<li>・${s}</li>`).join('')}
      </ul>

      <div class="w-targets">
        <div class="w-target">🎯 目標 ${tpPct==null?'-':tpPct}% → <b>${tpPrice}</b>円</div>
        <div class="w-target">🛑 損切 ${slPct==null?'-':slPct}% → <b>${slPrice}</b>円</div>
      </div>

      <!-- ★ IN目安 -->
      <div style="margin:6px 0 4px">IN目安：<b>${inHint}</b> 円</div>

      <div class="w-meter-wrap">
        <div class="w-meter"><i style="width:${Math.max(8, Math.round((item.ai_win_prob??0)*100))}%"></i></div>
        <div class="w-meter-cap">TP到達:${tpProb}% / SL到達:${slProb}%</div>
      </div>
    </article>
  `;
}

/* ---------------- 一覧（コンパクト行だけにする） ---------------- */
async function loadList(){
  const data = await getJSON('/advisor/api/watch/list/');
  const list = $('#list'); list.innerHTML = '';
  $('#hit').textContent = `${data.items.length}件`;

  data.items.forEach((it)=>{
    const overall = it.overall_score ?? 0;
    const ai = star5(it.ai_win_prob ?? 0);
    const entry = (it.entry_price_hint!=null) ? it.entry_price_hint.toLocaleString() : '-';

    const row = document.createElement('div');
    row.className = 'item';
    row.dataset.id = it.id;

    row.innerHTML = `
      <div class="item-line1">
        <div class="item-title">${it.name} <span class="item-code">(${it.ticker})</span></div>
        <div class="item-kpis">
          <span class="item-kpi kpi-overall">総合 <b>${overall}</b>点</span>
          <span class="item-kpi kpi-ai">AI ${ai}</span>
          <span class="item-kpi kpi-entry">IN目安 <b>${entry}</b>円</span>
        </div>
      </div>
    `;
    row.addEventListener('click', ()=> openSheet(it));
    list.appendChild(row);
  });
}

/* ---------------- シート（スクロール可能） ---------------- */
function openSheet(item){
  const sheet = $('#sheet');
  $('#sh-card').innerHTML = cardHTML(item);
  $('#sh-note').value = item.note || '';
  sheet.hidden = false; sheet.setAttribute('aria-hidden','false');

  const close = ()=>{ sheet.hidden = true; sheet.setAttribute('aria-hidden','true'); };
  $('#sh-backdrop').onclick = close;
  $('#sh-close-btn').onclick = close;

  $('#sh-save').onclick = async ()=>{
    try{
      const body = { ticker: item.ticker, name: item.name || '', note: $('#sh-note').value || '' };
      await postJSON('/advisor/api/watch/upsert/', body);
      toast('保存しました'); await loadList(); close();
    }catch(e){ console.error(e); toast('通信に失敗しました'); }
  };
  $('#sh-hide').onclick = async ()=>{
    try{
      await postJSON('/advisor/api/watch/archive/', { id: item.id });
      toast('非表示にしました'); await loadList(); close();
    }catch(e){ console.error(e); toast('通信に失敗しました'); }
  };
}

/* ---------------- 検索（ローカルフィルタ） ---------------- */
function wireSearch(){
  const q = $('#q');
  q.addEventListener('input', ()=>{
    const key = q.value.trim();
    $$('#list .item').forEach(card=>{
      const txt = card.textContent || '';
      card.style.display = (key==='' || txt.includes(key)) ? '' : 'none';
    });
    const visible = [...$$('#list .item')].filter(n=>n.style.display!=='none').length;
    $('#hit').textContent = `${visible}件`;
  });
}

/* ---------------- init ---------------- */
(async function(){
  try{
    await loadList();
    wireSearch();
  }catch(e){
    console.error(e);
    toast('読み込みに失敗しました');
  }
})();