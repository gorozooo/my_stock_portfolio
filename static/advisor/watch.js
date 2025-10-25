console.log("[watch.js] v2025-10-26-optimistic-archive loaded");
const $ = s => document.querySelector(s);

let state = { q:"", items:[], next:null, busy:false, current:null };
let __sheetViewportHandler = null;
let __hiding = false; // 二重押しガード

/* ================= 共通ユーティリティ ================= */
function csrf(){
  const m = document.cookie.match(/(?:^|;)\s*csrftoken=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}
function toast(msg){
  const t=document.createElement("div");
  t.className="toast";
  t.textContent=msg;
  document.body.appendChild(t);
  requestAnimationFrame(()=> t.style.opacity="1");
  setTimeout(()=>{ t.style.opacity="0"; setTimeout(()=>t.remove(),220); },1800);
}
/* 下部UIを避けるオフセット */
function computeBottomOffsetPx(){
  let inset = 0;
  if (window.visualViewport){
    const diff = window.innerHeight - window.visualViewport.height;
    inset = Math.max(0, Math.round(diff));
  }
  return inset + 120;
}
/* DOMから1件削除（ティッカー一致） */
function removeCellFromDOM(ticker){
  const cell = document.querySelector(`.cell[data-ticker="${ticker}"]`);
  if(cell){
    cell.style.transition="transform .18s ease, opacity .18s ease";
    cell.style.transform="translateX(-16px)";
    cell.style.opacity="0";
    setTimeout(()=> cell.remove(), 180);
  }
  // state.items 側も消しておく
  state.items = state.items.filter(x => x.ticker !== ticker);
  const hit = $("#hit"); if(hit){ hit.textContent = `${state.items.length}${state.next!=null?"+":""}件`; }
}

/* フェッチ */
async function getJSON(urls){
  let lastErr;
  for(const url of urls){
    try{
      const res = await fetch(url, {credentials:"same-origin"});
      if(res.status === 404) continue;
      const data = await res.json();
      if(!res.ok || data.ok === false) throw new Error(data.error || `HTTP ${res.status}`);
      return data;
    }catch(e){ lastErr = e; }
  }
  throw lastErr || new Error("request failed");
}
async function postJSON(urls, body){
  for(const url of urls){
    try{
      const res = await fetch(url, {
        method:"POST",
        credentials:"same-origin",
        headers:{
          "Content-Type":"application/json",
          "X-CSRFToken": csrf()
        },
        body: JSON.stringify(body)
      });
      if(res.status === 404) continue;
      const ct = res.headers.get("content-type") || "";
      const data = ct.includes("application/json") ? await res.json() : { ok:false, error:"non-json" };
      if(!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      return data;
    }catch(e){ /* 次のURLへ */ }
  }
  throw new Error("all failed");
}
/* タイムアウト付きPOST（無反応対策） */
function postJSONWithTimeout(urls, body, ms=1800){
  return Promise.race([
    postJSON(urls, body),
    new Promise((_,rej)=> setTimeout(()=> rej(new Error("timeout")), ms))
  ]);
}

/* ================= APIエンドポイント ================= */
const API_LIST    = ["/advisor/api/watch/list/",    "/advisor/watch/list/"];
const API_UPSERT  = ["/advisor/api/watch/upsert/",  "/advisor/watch/upsert/"];
const API_ARCHIVE = ["/advisor/api/watch/archive/", "/advisor/watch/archive/"];

/* ================= メイン処理 ================= */
async function fetchList(reset=false){
  if(state.busy) return; state.busy = true;
  try{
    const params = new URLSearchParams();
    if(state.q) params.set("q", state.q);
    if(!reset && state.next!=null) params.set("cursor", state.next);
    params.set("limit","20");

    const data = await getJSON(API_LIST.map(u=>`${u}?${params.toString()}`));
    if(reset){ state.items=[]; $("#list").innerHTML=""; }
    state.items = state.items.concat(data.items);
    state.next  = data.next_cursor;

    $("#hit") && ($("#hit").textContent = `${state.items.length}${state.next!=null?"+":""}件`);
    paint(data.items);
    $("#more").hidden = (state.next==null);
  }catch(e){
    console.error("[fetchList]", e);
    toast("読み込みに失敗しました");
  }finally{ state.busy=false; }
}

/* “非表示”：楽観削除 → API → 最終同期 */
async function archiveTicker(ticker){
  // 1) UIは先に消す（ユーザー体感の「反応なし」をなくす）
  removeCellFromDOM(ticker);
  toast("整理しています…");

  // 2) APIを叩く（既に非表示でもok扱い）。無応答ならtimeoutで先に進む
  try{
    const res = await postJSONWithTimeout(API_ARCHIVE, {ticker}, 2000);
    if(res && res.ok){
      toast(res.status === "archived" ? "非表示にしました" : "すでに非表示でした");
    }else{
      toast("処理に失敗しました");
    }
  }catch(err){
    console.warn("[archiveTicker]", err);
    // 既に非表示 or タイムアウトでも UI は消しているので、最後に同期だけする
    toast("すでに非表示の可能性があります");
  }

  // 3) サーバの真の状態で整合（ACTIVEのみリストに戻す）
  await fetchList(true);
}

/* IN/OUT トグル */
async function toggleInPosition(ticker, on){
  try{
    const res = await postJSON(API_UPSERT, {ticker, in_position:on});
    if(res.ok){ toast(on?"INにしました":"OUTにしました"); }
    else{ toast("失敗しました"); }
  }catch(e){
    console.error("[toggleInPosition]", e);
    toast("通信に失敗しました");
  }
}

/* ================== 描画・UI ================== */
function paint(items){
  const list = $("#list");
  for(const it of items){
    const cell = document.createElement("article");
    cell.className = "cell";
    cell.dataset.ticker = it.ticker;
    cell.innerHTML = `
      <div class="row" data-act="open">
        <div class="name">
          <div class="line1">${it.name||it.ticker}（${it.ticker}）</div>
          <div class="line2">${it.reason_summary||""}</div>
        </div>
        <div class="actions">
          <div class="switch ${it.in_position?"on":""}" data-act="toggle">
            <span>${it.in_position?"IN":"OUT"}</span><i></i>
          </div>
        </div>
      </div>`;
    attachSwipe(cell, it.ticker);
    list.appendChild(cell);
  }
}

/* スワイプで非表示 */
function attachSwipe(cell, ticker){
  let sx=0, dx=0, dragging=false;
  cell.addEventListener("touchstart",(e)=>{ dragging=true; sx=e.touches[0].clientX; dx=0; },{passive:true});
  cell.addEventListener("touchmove",(e)=>{
    if(!dragging) return;
    dx = e.touches[0].clientX - sx;
    cell.style.transform = `translateX(${Math.max(-80, Math.min(80, dx))}px)`;
  },{passive:true});
  cell.addEventListener("touchend", async ()=>{
    if(!dragging) return; dragging=false;
    if(dx < -60){ await archiveTicker(ticker); }
    cell.style.transform = "translateX(0)";
  });
}

/* ボトムシート */
function openSheet(item){
  state.current = item;
  const sheet = $("#sheet");
  const body  = sheet.querySelector(".sheet-body");

  const applyBottom = ()=>{ body.style.bottom = computeBottomOffsetPx() + "px"; };
  applyBottom();
  body.style.height = "62vh";

  __sheetViewportHandler = ()=> applyBottom();
  if (window.visualViewport){
    window.visualViewport.addEventListener("resize", __sheetViewportHandler);
  }

  $("#sh-title").textContent = `${item.name||item.ticker}（${item.ticker}）`;
  $("#sh-theme").textContent = item.theme_label ? `#${item.theme_label} ${Math.round(item.theme_score*100)}点` : "";
  $("#sh-ai").textContent    = item.ai_win_prob ? `AI ${Math.round(item.ai_win_prob*100)}%` : "";
  $("#sh-reasons").innerHTML = (item.reason_details||[]).map(r=>`<li>・${r}</li>`).join("") || "<li>理由なし</li>";
  $("#sh-tp").textContent    = item.target_tp ? `🎯 ${item.target_tp}` : "🎯 —";
  $("#sh-sl").textContent    = item.target_sl ? `🛑 ${item.target_sl}` : "🛑 —";
  $("#sh-note").value        = item.note || "";

  sheet.hidden = false; sheet.setAttribute("aria-hidden","false");
}
function closeSheet(){
  const sheet = $("#sheet");
  const body  = sheet.querySelector(".sheet-body");
  if (window.visualViewport && __sheetViewportHandler){
    window.visualViewport.removeEventListener("resize", __sheetViewportHandler);
  }
  __sheetViewportHandler = null;
  body.style.bottom = "";
  sheet.hidden = true; sheet.setAttribute("aria-hidden","true");
  state.current = null;
}

/* クリック */
document.addEventListener("click", async (e)=>{
  const sw  = e.target.closest(".switch");
  const row = e.target.closest(".row");

  if(sw){
    const cell=sw.closest(".cell"); const t=cell.dataset.ticker;
    const next=!sw.classList.contains("on");
    sw.classList.toggle("on", next);
    sw.querySelector("span").textContent = next ? "IN" : "OUT";
    await toggleInPosition(t, next);
    return;
  }
  if(row){
    const t=row.closest(".cell").dataset.ticker;
    const it=state.items.find(x=>x.ticker===t);
    if(it) openSheet(it);
    return;
  }
  if(e.target.id==="sh-close"){ closeSheet(); return; }

  // シート：非表示 → 楽観削除＆確実に反応
  if(e.target.id==="sh-hide" && state.current){
    if(__hiding) return;
    __hiding = true;
    const t = state.current.ticker;
    closeSheet();             // 先にシートを閉じる（反応を見せる）
    await archiveTicker(t);   // 上でUIからも消す
    __hiding = false;
    return;
  }

  // シート：保存
  if(e.target.id==="sh-save" && state.current){
    try{
      const note=$("#sh-note").value;
      const res = await postJSON(API_UPSERT, {ticker: state.current.ticker, note});
      if(res.ok) toast("メモを保存しました"); else toast("保存に失敗しました");
    }catch(err){ console.error("[save note]", err); toast("保存に失敗しました"); }
    return;
  }
});

/* 検索 */
$("#q").addEventListener("input", ()=>{
  state.q = $("#q").value.trim();
  clearTimeout(window.__qtimer);
  window.__qtimer = setTimeout(()=> fetchList(true), 250);
});
$("#more").addEventListener("click", ()=> fetchList(false));
document.addEventListener("DOMContentLoaded", ()=> fetchList(true));