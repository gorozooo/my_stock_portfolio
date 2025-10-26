// static/advisor/watch.js
const $ = (s)=>document.querySelector(s);
const $$ = (s)=>document.querySelectorAll(s);

console.log("[watch.js] v2025-10-26 copy-board-card");

// ==== 共通ヘルパ ====
function abs(path){ return new URL(path, location.origin).toString(); }
function fmtDate(d){
  const y=d.getFullYear(), m=String(d.getMonth()+1).padStart(2,"0"), da=String(d.getDate()).padStart(2,"0");
  const hh=String(d.getHours()).padStart(2,"0"), mm=String(d.getMinutes()).padStart(2,"0");
  return `${y}/${m}/${da} ${hh}:${mm}`;
}
function stars(prob01){
  const s = Math.round((prob01 ?? 0) * 5);
  return "★★★★★".slice(0,Math.min(5,Math.max(0,s))) + "☆☆☆☆☆".slice(0,5-Math.min(5,Math.max(0,s)));
}
function weeklyFrom(themeScore, winProb){
  const score = 0.7*(winProb||0) + 0.3*(themeScore||0);
  if(score>=0.62) return {icon:"↗️", label:"上向き", cls:"wk-up"};
  if(score>=0.48) return {icon:"➡️", label:"横ばい", cls:"wk-flat"};
  return {icon:"↘️", label:"下向き", cls:"wk-down"};
}
function overall(themeScore, winProb){
  return Math.round((0.7*(winProb||0) + 0.3*(themeScore||0))*100);
}
function computeToastBottomPx(){
  let inset=0;
  if(window.visualViewport){
    inset = Math.max(0, Math.round(window.innerHeight - window.visualViewport.height));
  }
  return inset + 140;
}
function toast(msg){
  const t=document.createElement("div");
  t.style.position="fixed"; t.style.left="50%"; t.style.transform="translateX(-50%)";
  t.style.bottom=computeToastBottomPx()+"px";
  t.style.background="rgba(0,0,0,.85)"; t.style.color="#fff";
  t.style.padding="10px 16px"; t.style.borderRadius="14px"; t.style.zIndex="9999";
  t.style.opacity="0"; t.style.transition="opacity .25s";
  t.textContent = msg; document.body.appendChild(t);
  requestAnimationFrame(()=>t.style.opacity="1");
  const onVV=()=> t.style.bottom=computeToastBottomPx()+"px";
  if(window.visualViewport) window.visualViewport.addEventListener("resize", onVV);
  setTimeout(()=>{ t.style.opacity="0"; setTimeout(()=>{ if(window.visualViewport) window.visualViewport.removeEventListener("resize", onVV); t.remove(); },250); },2000);
}
async function postJSON(url, body){
  const r = await fetch(abs(url), {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
  if(!r.ok) throw new Error(`HTTP ${r.status} ${await r.text().catch(()=> "")}`);
  return r.json();
}

// ==== リスト読み込み ====
let cursor=0, limit=20, q="";
let moreToken = null;
let items = [];

async function load(reset=false){
  const url = new URL(abs("/advisor/api/watch/list/"));
  url.searchParams.set("limit", String(limit));
  url.searchParams.set("cursor", String(reset? 0 : (cursor||0)));
  if(q) url.searchParams.set("q", q);

  const res = await fetch(url.toString(), {headers:{"Cache-Control":"no-store"}});
  const data = await res.json();

  if(reset){ items=[]; $("#list").innerHTML=""; }
  const got = data.items || [];
  items.push(...got);
  $("#hit").textContent = `${items.length}件`;

  for(const w of got){
    const li = document.createElement("div");
    li.className = "watch-row";
    li.innerHTML = `
      <div class="row-title">
        <span class="name">${w.name || w.ticker}</span>
        <span class="code">${w.ticker}</span>
      </div>
      <div class="row-sub">${(w.reason_summary||"").replace(/\s*\/\s*/g," / ")}</div>
      <button class="toggle ${w.in_position? "in":"out"}" aria-label="IN/OUT">${w.in_position? "IN":"OUT"}</button>
    `;
    li.addEventListener("click", ()=>{
      openSheet(w);
    });
    $("#list").appendChild(li);
  }

  moreToken = data.next_cursor;
  const moreBtn = $("#more");
  if(moreToken!=null){
    moreBtn.hidden = false;
    cursor = moreToken;
  }else{
    moreBtn.hidden = true;
  }
}

// ==== Board と同じカードHTMLを作る（できる限り同じクラス・構造）====
function buildBoardLikeCard(w){
  const themeScore = Math.round((w.theme_score||0)*100);
  const wk = weeklyFrom(w.theme_score||0, w.ai_win_prob||0);
  const o = overall(w.theme_score||0, w.ai_win_prob||0);
  const aiStars = stars(w.ai_win_prob||0);
  const themeLabel = w.theme_label || "テーマ";

  // Board の .card 構造をほぼコピー
  return `
    <article class="card">
      <div class="title">${w.name || ""} <span class="code">(${w.ticker})</span></div>
      <div class="segment">週足：<span class="chip ${wk.cls}">${wk.icon} ${wk.label}</span></div>

      <div class="overall-block">
        <div class="overall">総合評価：<strong>${o}点</strong></div>
        <div class="ai-confidence">AI信頼度：${aiStars}</div>
      </div>

      <div class="action good">行動：ウォッチ中</div>

      <ul class="reasons">
        ${(w.reason_details && w.reason_details.length
            ? w.reason_details
            : (w.reason_summary||"").split("/").map(s=>s.trim()).filter(Boolean)
          ).map(r=>`<li>・${r}</li>`).join("")}
      </ul>

      <div class="targets">
        <div class="target">🎯 ${w.target_tp || "目標 —"}</div>
        <div class="target">🛑 ${w.target_sl || "損切り —"}</div>
      </div>

      <div class="theme-tag">🏷️ ${themeLabel} ${themeScore}点</div>
    </article>
  `;
}

// ==== シート（Boardカード + 追加情報 + メモ + アクション）====
let currentItem = null;

function openSheet(w){
  currentItem = w;
  $("#sh-card").innerHTML = buildBoardLikeCard(w);
  $("#sh-added").textContent = w.updated_at ? `追加: ${fmtDate(new Date(w.updated_at))}` : "";
  $("#sh-note").value = w.note || "";

  const sheet = $("#sheet");
  sheet.hidden = false;
  sheet.setAttribute("aria-hidden", "false");
  document.body.style.overflow = "hidden";
}

function closeSheet(){
  const sheet = $("#sheet");
  sheet.hidden = true;
  sheet.setAttribute("aria-hidden", "true");
  document.body.style.overflow = "";
  currentItem = null;
}

// ==== 事件 ====
window.addEventListener("DOMContentLoaded", ()=>{
  load(true).catch(e=>{ console.error(e); toast("読み込みに失敗しました"); });

  $("#more").addEventListener("click", ()=> load(false).catch(e=>{ console.error(e); toast("読み込みに失敗しました"); }));
  $("#q").addEventListener("input", (e)=>{
    q = e.target.value.trim();
    load(true).catch(e=>{ console.error(e); toast("検索に失敗しました"); });
  });

  // sheet_buttons
  $$("#sh-close").forEach(el=> el.addEventListener("click", closeSheet));
  $("#sh-save").addEventListener("click", async ()=>{
    if(!currentItem) return;
    try{
      const payload = {
        ticker: currentItem.ticker,
        note: $("#sh-note").value || "",
        name: currentItem.name || "",
      };
      await postJSON("/advisor/api/watch/upsert/", payload);
      toast("保存しました");
      closeSheet();
    }catch(e){
      console.error(e);
      toast("保存に失敗しました");
    }
  });

  $("#sh-hide").addEventListener("click", async ()=>{
    if(!currentItem) return;
    try{
      const r = await postJSON("/advisor/api/watch/archive/", { ticker: currentItem.ticker });
      if(r?.status === "archived"){
        toast("非表示にしました");
        // 画面から消す
        $("#list").innerHTML = "";
        items = [];
        cursor = 0;
        await load(true);
        closeSheet();
      }else{
        toast("すでに非表示でした");
      }
    }catch(e){
      console.error(e);
      toast("非表示に失敗しました");
    }
  });
});

// ==== スタイルの微調整（ボトムタブに被らないよう内側スクロール）====
(function tuneSheet(){
  const style = document.createElement("style");
  style.textContent = `
    .sheet-body{ max-height: calc(100vh - 24px); overflow:auto; padding-bottom: 120px; }
    .snapshot-card .card{ margin-bottom: 12px; }
    .added-at{ opacity:.8; font-size:12px; margin: 4px 0 12px; }
  `;
  document.head.appendChild(style);
})();