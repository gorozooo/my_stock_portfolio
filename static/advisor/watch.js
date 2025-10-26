/* watch.js v26 — board内容の表示を再利用しつつ
   1) IN目安（entry_price_hint）を表示
   2) ボトムシートをスクロール可能に（操作ボタン常時利用可）
*/
const $  = (s)=>document.querySelector(s);
const $$ = (s)=>document.querySelectorAll(s);

function abs(path){ return new URL(path, window.location.origin).toString(); }

// ---- トースト（boardと同仕様） ----
function computeToastBottomPx(){
  let insetBottom = 0;
  if (window.visualViewport){
    const diff = window.innerHeight - window.visualViewport.height;
    insetBottom = Math.max(0, Math.round(diff));
  }
  return insetBottom + 140;
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

// ---- API ----
async function getJSON(url){ const r = await fetch(abs(url)); if(!r.ok) throw new Error(await r.text()); return await r.json(); }
async function postJSON(url, body){
  const r = await fetch(abs(url), {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body||{})});
  if(!r.ok) throw new Error(`HTTP ${r.status} ${await r.text().catch(()=> '')}`);
  return await r.json();
}

// ---- カードHTML（boardの見た目をコピー） ----
function star5(prob01){ const s = Math.round((prob01??0)*5); return '★★★★★'.slice(0,s)+'☆☆☆☆☆'.slice(0,5-s); }
function wkChip(code){
  if(code==='up') return {icon:'↗️', label:'上向き'};
  if(code==='down') return {icon:'↘️', label:'下向き'};
  return {icon:'➡️', label:'横ばい'};
}

function cardHTML(item){
  const themeScore = Math.round((item.theme_score??0)*100);
  const wk = wkChip(item.weekly_trend||'flat');
  const aiStars = star5(item.ai_win_prob);
  const tpPct = Math.round((item.tp_pct??0)*100);
  const slPct = Math.round((item.sl_pct??0)*100);

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
        ${(item.reason_details||[]).map(s=>`<li>・${s}</li>`).join('')}
      </ul>

      <div class="w-targets">
        <div class="w-target">🎯 目標 ${isNaN(tpPct)?'-':tpPct}% → <b>${item.tp_price?.toLocaleString?.()??'-'}</b>円</div>
        <div class="w-target">🛑 損切 ${isNaN(slPct)?'-':slPct}% → <b>${item.sl_price?.toLocaleString?.()??'-'}</b>円</div>
      </div>

      <!-- ★ IN目安を表示 -->
      <div style="margin:6px 0 4px">IN目安：<b>${item.entry_price_hint?.toLocaleString?.() ?? '-'}</b> 円</div>

      <div class="w-meter-wrap">
        <div class="w-meter"><i style="width:${Math.max(8, Math.round((item.ai_win_prob??0)*100))}%"></i></div>
        <div class="w-meter-cap">TP到達:${Math.round((item.ai_tp_prob??0)*100)}% / SL到達:${Math.round((item.ai_sl_prob??0)*100)}%</div>
      </div>
    </article>
  `;
}

// ---- レンダリング ----
async function loadList(){
  const data = await getJSON('/advisor/api/watch/list/');
  const list = $('#list'); list.innerHTML = '';
  $('#hit').textContent = `${data.items.length}件`;

  // コンパクト行（タップでシートを開く）
  data.items.forEach((it)=>{
    const row = document.createElement('article');
    row.className = 'wcard';
    row.innerHTML = `
      <div class="w-title">${it.name} <span class="code">(${it.ticker})</span></div>
      <div class="w-badges">
        <span class="badge-chip">${wkChip(it.weekly_trend||'flat').icon} ${wkChip(it.weekly_trend||'flat').label}</span>
        <span class="badge-chip blue">#${it.theme_label||'-'} ${Math.round((it.theme_score??0)*100)}点</span>
      </div>
      <div class="w-action" style="margin-top:8px">行動：ウォッチ中</div>
      <ul class="w-list">${(it.reason_details||[]).slice(0,2).map(s=>`<li>・${s}</li>`).join('')}</ul>
      <div class="w-targets">
        <div class="w-target">🎯 ${it.target_tp||'-'}</div>
        <div class="w-target">🛑 ${it.target_sl||'-'}</div>
      </div>
    `;
    row.addEventListener('click', ()=> openSheet(it));
    list.appendChild(row);
  });
}

// ---- シート（スクロール可能） ----
function openSheet(item){
  const sheet = $('#sheet');
  $('#sh-card').innerHTML = cardHTML(item);
  $('#sh-note').value = item.note || '';
  sheet.hidden = false; sheet.setAttribute('aria-hidden','false');

  // 操作
  const close = ()=>{ sheet.hidden = true; sheet.setAttribute('aria-hidden','true'); };
  $('#sh-backdrop').onclick = close;
  $('#sh-close-btn').onclick = close;

  $('#sh-save').onclick = async ()=>{
    try{
      const body = { id: item.id, ticker: item.ticker, note: $('#sh-note').value };
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

// ---- 検索（ローカルフィルタ） ----
function wireSearch(){
  const q = $('#q');
  q.addEventListener('input', ()=>{
    const key = q.value.trim();
    $$('#list .wcard').forEach(card=>{
      const txt = card.textContent || '';
      card.style.display = (key==='' || txt.includes(key)) ? '' : 'none';
    });
    const visible = [...$$('#list .wcard')].filter(n=>n.style.display!=='none').length;
    $('#hit').textContent = `${visible}件`;
  });
}

// ---- init ----
(async function(){
  try{
    await loadList();
    wireSearch();
  }catch(e){
    console.error(e);
    toast('読み込みに失敗しました');
  }
})();