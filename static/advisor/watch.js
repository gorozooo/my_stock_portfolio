/* watch.js v2025-10-26 r6：コンパクト→タップで詳細カード、トースト復活 */
const $ = (s)=>document.querySelector(s);

console.log("[watch.js] loaded r6");

/* ===== Toast（下タブ回避あり） ===== */
function computeToastBottomPx(){
  let insetBottom = 0;
  if (window.visualViewport){
    const diff = window.innerHeight - window.visualViewport.height;
    insetBottom = Math.max(0, Math.round(diff));
  }
  return insetBottom + 140; // 下タブ/ホームバーを避ける
}
function showToast(msg){
  const t = document.createElement("div");
  t.className = "toast";
  t.textContent = msg;
  t.style.bottom = computeToastBottomPx() + "px";
  document.body.appendChild(t);
  requestAnimationFrame(()=> t.style.opacity = "1");
  const onViewport = ()=> { t.style.bottom = computeToastBottomPx() + "px"; };
  if (window.visualViewport) window.visualViewport.addEventListener("resize", onViewport);
  setTimeout(()=>{
    t.style.opacity = "0";
    setTimeout(()=>{
      if (window.visualViewport) window.visualViewport.removeEventListener("resize", onViewport);
      t.remove();
    }, 250);
  }, 1800);
}

/* ===== ユーティリティ ===== */
function abs(path){ return new URL(path, window.location.origin).toString(); }
async function getJSON(url){
  const res = await fetch(abs(url), { headers:{ "Cache-Control":"no-store" } });
  if (!res.ok) throw new Error(`HTTP ${res.status} ${await res.text().catch(()=> "")}`);
  return await res.json();
}
async function postJSON(url, body){
  const res = await fetch(abs(url), { method:"POST", headers:{ "Content-Type":"application/json" }, body: JSON.stringify(body||{}) });
  if (!res.ok) throw new Error(`HTTP ${res.status} ${await res.text().catch(()=> "")}`);
  return await res.json();
}

/* ===== board表記との互換レンダリングのための補助 ===== */
function starsFromProb(p01){
  const s = Math.round((p01 ?? 0) * 5);
  const f = "★★★★★".slice(0, Math.max(0, Math.min(5, s)));
  const e = "☆☆☆☆☆".slice(0, 5 - Math.max(0, Math.min(5, s)));
  return f + e;
}
function wkChip(code){
  if(code === "up") return {text:"↗️ 上向き", cls:"wk-up"};
  if(code === "down") return {text:"↘️ 下向き", cls:"wk-down"};
  return {text:"➡️ 横ばい", cls:"wk-flat"};
}

/* ===== 画面状態 ===== */
let state = {
  q: "",
  cursor: 0,
  limit: 20,
  loading: false,
  items: [],
  current: null, // シートで開いている item
};

/* ===== コンパクト行を描画 ===== */
function renderCompactItem(it){
  const wk = wkChip(it.weekly_trend || "");
  const themeScore = Math.round((it.theme_score ?? 0) * 100);
  const div = document.createElement("div");
  div.className = "item";
  div.dataset.id = it.id;

  div.innerHTML = `
    <div class="item-line1">
      <div class="item-title">${it.name || ""} <span class="item-code">(${it.ticker})</span></div>
      <div class="item-chips">
        <span class="chip ${wk.cls}">${wk.text}</span>
        ${themeScore ? `<span class="chip">#${it.theme_label || ""} ${themeScore}点</span>` : ""}
      </div>
    </div>
    <div class="item-summary">${(it.reason_summary || "").replace(/\s*\n\s*/g," ")}</div>
  `;

  div.addEventListener("click", ()=> openSheet(it));
  return div;
}

/* ===== 詳細カード（boardの見た目をコピー） ===== */
function renderBoardCard(it){
  const themeScore = Math.round((it.theme_score ?? 0) * 100);
  const wk = wkChip(it.weekly_trend || "");
  const aiStars = starsFromProb(it.ai_win_prob ?? 0);
  const overall = (it.overall_score ?? 0);

  const tpPct = Math.round((it.tp_pct ?? 0) * 100);
  const slPct = Math.round((it.sl_pct ?? 0) * 100);

  const tpPrice = it.tp_price != null ? it.tp_price.toLocaleString() : "–";
  const slPrice = it.sl_price != null ? it.sl_price.toLocaleString() : "–";
  const entry = it.entry_price_hint != null ? it.entry_price_hint.toLocaleString() : "–";

  const tpProb = it.tp_prob != null ? Math.round((it.tp_prob)*100) : (it.ai_tp_prob != null ? Math.round((it.ai_tp_prob)*100) : null);
  const slProb = it.sl_prob != null ? Math.round((it.sl_prob)*100) : (it.ai_sl_prob != null ? Math.round((it.ai_sl_prob)*100) : null);

  const actionTone = /売|撤退|縮小/.test(it.action || "") ? "bad" : /様子見/.test(it.action || "") ? "warn" : "good";
  const reasons = (it.reason_details && it.reason_details.length ? it.reason_details : (it.reason_summary||"").split("/").map(s=>s.trim())).filter(Boolean);

  const card = document.createElement("div");
  card.innerHTML = `
    <span class="badge">#</span>
    <div class="title">${it.name || ""} <span class="code">(${it.ticker})</span></div>
    <div class="segment">週足：<span class="chip ${wk.cls}">${wk.text}</span></div>

    <div class="overall">
      <span>総合評価：<strong>${overall}</strong> 点</span>
      <span>AI信頼度：${aiStars}</span>
    </div>

    <div class="action ${actionTone}">行動：${it.action || "ウォッチ中"}</div>

    <ul class="reasons">${reasons.map(r=>`<li>・${r}</li>`).join("")}</ul>

    <div class="targets">
      <div class="target">🎯 目標 ${tpPct||0}% → <b>${tpPrice}</b>円</div>
      <div class="target">🛑 損切 ${slPct||0}% → <b>${slPrice}</b>円</div>
    </div>

    <div class="meter-wrap">
      <div class="meter-bar"><i style="width:${Math.max(8, Math.round((it.ai_win_prob||0)*100))}%"></i></div>
      <div class="meter-caption">TP到達：${tpProb ?? "–"}% / SL到達：${slProb ?? "–"}%</div>
    </div>

    <div class="theme-tag">🏷️ ${it.theme_label || ""} ${themeScore? themeScore+"点":""}</div>
  `;
  return card;
}

/* ===== 下シート開閉 ===== */
function openSheet(it){
  state.current = it;
  $("#sh-card").innerHTML = ""; // 初期化
  $("#sh-card").appendChild(renderBoardCard(it));
  $("#sh-note").value = it.note || "";

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
}
document.addEventListener("click",(e)=>{
  if(e.target.matches("[data-close]")) closeSheet();
});

/* ===== API 連携（一覧・保存・非表示） ===== */
async function loadList(reset=false){
  if (state.loading) return;
  state.loading = true;
  try{
    if (reset){ state.cursor = 0; state.items = []; $("#list").innerHTML = ""; }
    const url = `/advisor/api/watch/list/?q=${encodeURIComponent(state.q)}&cursor=${state.cursor}&limit=${state.limit}`;
    const js = await getJSON(url);
    const items = js.items || [];
    state.items.push(...items);

    // 検索件数
    $("#hit").textContent = `${state.items.length}件`;

    // レンダリング（コンパクト）
    const list = $("#list");
    items.forEach(it=> list.appendChild(renderCompactItem(it)));

    // ページング
    const moreBtn = $("#more");
    if (js.next_cursor != null){ state.cursor = js.next_cursor; moreBtn.hidden = false; }
    else { moreBtn.hidden = true; }
  }catch(e){
    console.error(e);
    showToast("読み込みに失敗しました");
  }finally{
    state.loading = false;
  }
}

async function saveNote(){
  const it = state.current; if (!it) return;
  const note = $("#sh-note").value || "";
  try{
    await postJSON("/advisor/api/watch/upsert/", { ticker: it.ticker, name: it.name || "", note });
    it.note = note; // ローカルも更新
    showToast("保存しました");
  }catch(e){
    console.error(e);
    showToast("保存に失敗しました");
  }
}

async function archiveCurrent(){
  const it = state.current; if (!it) return;
  try{
    // id でアーカイブ（冪等）
    const res = await getJSON(`/advisor/api/watch/archive/id/${it.id}/`);
    if (!res.ok && res.status !== "archived" && res.status !== "already_archived"){
      throw new Error("archive failed");
    }
    // 画面から取り除く
    const node = document.querySelector(`.item[data-id="${it.id}"]`); if (node) node.remove();
    state.items = state.items.filter(x=> x.id !== it.id);
    $("#hit").textContent = `${state.items.length}件`;
    closeSheet();
    showToast("非表示にしました");
  }catch(e){
    console.error(e);
    showToast("非表示に失敗しました");
  }
}

/* ===== イベント ===== */
$("#q").addEventListener("input",(e)=>{
  state.q = e.target.value.trim();
  // ライブサーチは負荷を避けるため 300ms デバウンス
  clearTimeout(window._watch_q_timer);
  window._watch_q_timer = setTimeout(()=> loadList(true), 300);
});
$("#more").addEventListener("click", ()=> loadList(false));
$("#sh-save").addEventListener("click", saveNote);
$("#sh-hide").addEventListener("click", archiveCurrent);

/* ===== 初期ロード ===== */
loadList(true);