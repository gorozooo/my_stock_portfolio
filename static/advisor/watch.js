const $ = (s)=>document.querySelector(s);
const $$ = (s)=>Array.from(document.querySelectorAll(s));

console.log("[watch.js] v2025-10-26 cards-copy-from-board");

function abs(path){ return new URL(path, window.location.origin).toString(); }
function fmtN(n){ return (n==null || Number.isNaN(n))? "-" : Number(n).toLocaleString(); }
function stars(p01){ const s=Math.round((p01||0)*5); return "★★★★★".slice(0,s)+"☆☆☆☆☆".slice(0,5-s); }
function wkChip(w){
  if(w==="up")   return {txt:"↗️ 上向き", cls:"wk-up"};
  if(w==="down") return {txt:"↘️ 下向き", cls:"wk-down"};
  return {txt:"➡️ 横ばい", cls:"wk-flat"};
}
function toast(msg){
  const t=document.createElement("div");
  t.className="toast";
  t.textContent=msg;
  document.body.appendChild(t);
  requestAnimationFrame(()=>t.classList.add("show"));
  setTimeout(()=>{ t.classList.remove("show"); setTimeout(()=>t.remove(),300); }, 1800);
}

// ---- API ----
async function getList({q="", cursor=0, limit=20}={}){
  const url = new URL(abs("/advisor/api/watch/list/"));
  if(q) url.searchParams.set("q", q);
  url.searchParams.set("cursor", cursor);
  url.searchParams.set("limit", limit);
  const res = await fetch(url.toString(), {headers:{"Cache-Control":"no-cache"}});
  if(!res.ok) throw new Error(await res.text());
  return await res.json();
}
async function saveNote(ticker, note){
  const res = await fetch(abs("/advisor/api/watch/upsert/"), {
    method:"POST", headers:{ "Content-Type":"application/json" },
    body: JSON.stringify({ ticker, note })
  });
  if(!res.ok) throw new Error(await res.text());
  return await res.json();
}
async function archiveById(id){
  const res = await fetch(abs(`/advisor/api/watch/archive/id/${id}/`));
  if(!res.ok) throw new Error(await res.text());
  return await res.json();
}

// ---- UI ----
function makeCard(item){
  const wk = wkChip(item.weekly_trend || "");
  const overall = (item.overall_score ?? 0);
  const themeScore = Math.round((item.theme_score||0)*100);
  const aiProb = item.ai_win_prob || 0;
  const tpPct = item.tp_pct!=null ? Math.round(item.tp_pct*100) : null;
  const slPct = item.sl_pct!=null ? Math.round(item.sl_pct*100) : null;

  const el = document.createElement("article");
  el.className = "card";
  el.dataset.id = item.id;
  el.dataset.ticker = item.ticker;

  el.innerHTML = `
    <div class="head">
      <div class="title">${item.name} <span class="code">(${item.ticker})</span></div>
      <div class="chips">
        <span class="chip ${wk.cls}">${wk.txt}</span>
        <span class="chip theme-chip">#${item.theme_label||"-"} ${themeScore}点</span>
      </div>
    </div>

    <div class="overall">
      <span>総合評価：<strong>${overall}</strong>点</span>
      <span class="ai">AI信頼度：${stars(aiProb)}</span>
    </div>

    <div class="action good">行動：ウォッチ中</div>

    <ul class="reasons">
      ${(item.reason_details && item.reason_details.length? item.reason_details : (item.reason_summary? item.reason_summary.split(" / "):[]))
        .map(r=>`<li>・${r}</li>`).join("")}
    </ul>

    <div class="targets">
      <div class="target">🎯 目標 ${tpPct ?? 0}% → <b>${fmtN(item.tp_price)}</b>円</div>
      <div class="target">🛑 損切 ${slPct ?? 0}% → <b>${fmtN(item.sl_price)}</b>円</div>
    </div>

    <div class="entry-size">
      <div>IN目安：<b>${fmtN(item.entry_price_hint)}</b>円</div>
      ${item.position_size_hint? `<div>数量目安：<b>${fmtN(item.position_size_hint)}</b> 株</div>`:""}
    </div>

    <div class="meter-wrap">
      <div class="meter-bar"><i style="width:${Math.max(8, Math.round((aiProb||0)*100))}%"></i></div>
      <div class="meter-caption">TP到達:—% / SL到達:—%</div>
    </div>

    <div class="buttons">
      <button class="btn outline" data-act="memo">📝 メモ</button>
      <button class="btn danger" data-act="hide">❌ 非表示</button>
    </div>
  `;
  return el;
}

function renderSheetFrom(item){
  const card = makeCard(item);
  $("#sheetCard").innerHTML = card.innerHTML;
  $("#sh-added").textContent = (item.created_at || item.updated_at || "").replace("T"," ").slice(0,16);
  $("#sh-note").value = ""; // 初期化（必要ならサーバにnote保存して反映可）
}

(async function init(){
  // 検索と初回読み込み
  const listEl = $("#list");
  const hitEl = $("#hit");
  const moreBtn = $("#more");
  let cursor = 0, q = "", loading = false, done = false;

  async function load(reset=false){
    if(loading || done) return;
    loading = true;
    const out = await getList({q, cursor, limit:20});
    if(reset){ listEl.innerHTML=""; }
    (out.items||[]).forEach(it=> listEl.appendChild(makeCard(it)));
    hitEl.textContent = `${(out.items||[]).length}件${out.next_cursor!=null? "＋" : ""}`;
    if(out.next_cursor!=null){ cursor = out.next_cursor; moreBtn.hidden=false; } else { moreBtn.hidden=true; done = true; }
    loading = false;
  }

  $("#q").addEventListener("input", (e)=>{
    q = e.target.value.trim();
    cursor = 0; done = false;
    load(true);
  });
  moreBtn.addEventListener("click", ()=> load());

  // 一覧→シート（メモ/非表示）
  document.addEventListener("click", async (ev)=>{
    const btn = ev.target.closest("button.btn");
    if(!btn) return;

    const card = btn.closest(".card");
    const id = Number(card?.dataset?.id);
    const ticker = card?.dataset?.ticker;

    // カード→シートに反映
    const item = {
      id, ticker,
      name: card.querySelector(".title")?.textContent?.replace(/\s*\(.+\)\s*$/,"") || "",
      theme_label: card.querySelector(".theme-chip")?.textContent?.replace(/^#/,"") || "",
    };

    if(btn.dataset.act === "memo"){
      // 直近のAPI結果から正しいitemを取りたいので、最新1ページ分を取り直す
      const latest = await getList({q:"", cursor:0, limit:50});
      const found = (latest.items||[]).find(x=> x.id===id) || {};
      renderSheetFrom(found);
      document.getElementById("sheet").hidden = false;
      document.getElementById("sheet").setAttribute("aria-hidden","false");
      document.body.classList.add("no-scroll");

      // 保存
      $("#sh-save").onclick = async ()=>{
        try{
          await saveNote(found.ticker, $("#sh-note").value);
          toast("保存しました");
        }catch(e){ console.error(e); toast("保存に失敗しました"); }
      };
      // 非表示
      $("#sh-hide").onclick = async ()=>{
        try{
          await archiveById(id);
          toast("非表示にしました");
          document.getElementById("sheet").hidden = true;
          listEl.querySelector(`.card[data-id="${id}"]`)?.remove();
        }catch(e){ console.error(e); toast("操作に失敗しました"); }
      };
      // 閉じる
      $("#sh-close2").onclick = ()=>{ document.getElementById("sheet").hidden = true; document.body.classList.remove("no-scroll"); };
      $("#sh-close").onclick = $("#sh-close2").onclick;

    }else if(btn.dataset.act === "hide"){
      try{
        await archiveById(id);
        toast("非表示にしました");
        listEl.querySelector(`.card[data-id="${id}"]`)?.remove();
      }catch(e){ console.error(e); toast("操作に失敗しました"); }
    }
  });

  await load(true);
})();