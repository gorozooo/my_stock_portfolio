// static/advisor/watch.js
console.log("[watch.js] v2025-10-26-EmbedBoardCard");
const $ = s => document.querySelector(s);

let state = { q:"", items:[], next:null, busy:false, current:null };
let __sheetViewportHandler = null;
let __hiding = false;

function csrf(){
  const m = document.cookie.match(/(?:^|;)\s*csrftoken=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}
function toast(msg){
  const t = document.createElement("div");
  t.className = "toast";
  t.textContent = msg;
  document.body.appendChild(t);
  requestAnimationFrame(()=> t.style.opacity="1");
  setTimeout(()=>{ t.style.opacity="0"; setTimeout(()=>t.remove(), 220); }, 1800);
}
function computeBottomOffsetPx(){
  let inset = 0;
  if (window.visualViewport){
    const diff = window.innerHeight - window.visualViewport.height;
    inset = Math.max(0, Math.round(diff));
  }
  return inset + 120;
}
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
        method:"POST", credentials:"same-origin",
        headers: {"Content-Type":"application/json","X-CSRFToken": csrf()},
        body: JSON.stringify(body)
      });
      if(res.status === 404) continue;
      const data = await res.json();
      if(!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      return data;
    }catch(e){ /* try next url */ }
  }
  throw new Error("all failed");
}
function postJSONWithTimeout(urls, body, ms=2500){
  return Promise.race([
    postJSON(urls, body),
    new Promise((_,rej)=> setTimeout(()=> rej(new Error("timeout")), ms))
  ]);
}

const API_LIST    = ["/advisor/api/watch/list/",    "/advisor/watch/list/"];
const API_UPSERT  = ["/advisor/api/watch/upsert/",  "/advisor/watch/upsert/"];
const API_ARCHIVE = ["/advisor/api/watch/archive/", "/advisor/watch/archive/"];

/* ----------- list ----------- */
async function fetchList(reset=false){
  if(state.busy) return; state.busy=true;
  try{
    const params = new URLSearchParams();
    if(state.q) params.set("q", state.q);
    if(!reset && state.next!=null) params.set("cursor", state.next);
    params.set("limit","20");
    const data = await getJSON(API_LIST.map(u=>`${u}?${params.toString()}`));

    if(reset){ state.items=[]; $("#list").innerHTML=""; }
    state.items = state.items.concat(data.items);
    state.next  = data.next_cursor;

    const hit=$("#hit");
    if(hit) hit.textContent = `${state.items.length}${state.next!=null?"+":""}件`;

    paint(data.items);
    $("#more").hidden = (state.next==null);
  }catch(e){
    console.error(e);
    toast("読み込みに失敗しました");
  }finally{ state.busy=false; }
}

function firstReasonLineFromHTML(html){
  if(!html) return "";
  const tmp = document.createElement("div"); tmp.innerHTML = html;
  const li = tmp.querySelector("li");
  if(li) return li.textContent.trim();
  return tmp.textContent.trim();
}

function paint(items){
  const list=$("#list");
  for(const it of items){
    const cell=document.createElement("article");
    cell.className="cell";
    cell.dataset.id = it.id;
    cell.dataset.ticker = it.ticker;

    const line2 = firstReasonLineFromHTML(it.reason_summary) || "理由メモなし";
    const themeTag = it.theme_label ? ` / #${it.theme_label} ${Math.round((it.theme_score||0)*100)}点` : "";

    cell.innerHTML = `
      <div class="row" data-act="open" role="button" tabindex="0" aria-label="${it.name||it.ticker}の詳細を開く">
        <div class="name">
          <div class="line1">${(it.name||it.ticker)}（${it.ticker}）</div>
          <div class="line2">${line2}${themeTag}</div>
        </div>
        <div class="actions">
          <div class="switch ${it.in_position?"on":""}" data-act="toggle">
            <span>${it.in_position?"IN":"OUT"}</span><i></i>
          </div>
        </div>
      </div>`;
    attachSwipe(cell, it.id);
    list.appendChild(cell);
  }
}

/* ----------- swipe-to-archive ----------- */
function attachSwipe(cell, id){
  let sx=0, dx=0, dragging=false;
  cell.addEventListener("touchstart",(e)=>{dragging=true;sx=e.touches[0].clientX;dx=0;},{passive:true});
  cell.addEventListener("touchmove",(e)=>{
    if(!dragging) return;
    dx=e.touches[0].clientX-sx;
    cell.style.transform=`translateX(${Math.max(-80,Math.min(80,dx))}px)`;
  },{passive:true});
  cell.addEventListener("touchend",async()=>{
    if(!dragging) return; dragging=false;
    if(dx<-60){ await archiveById(id); }
    cell.style.transform="translateX(0)";
  });
}

/* ----------- archive / toggle ----------- */
function removeCellById(id){
  const cell = document.querySelector(`.cell[data-id="${id}"]`);
  if(cell){
    cell.style.transition="transform .18s ease, opacity .18s ease";
    cell.style.transform="translateX(-16px)";
    cell.style.opacity="0";
    setTimeout(()=> cell.remove(), 180);
  }
  state.items = state.items.filter(x=> x.id !== id);
  const hit=$("#hit"); if(hit) hit.textContent = `${state.items.length}${state.next!=null?"+":""}件`;
}
async function archiveById(id){
  removeCellById(id);
  toast("整理しています…");
  try{
    const res = await postJSONWithTimeout(API_ARCHIVE, {id}, 2500);
    toast(res && res.ok ? (res.status==="archived"?"非表示にしました":"すでに非表示でした") : "処理に失敗しました");
  }catch(e){
    console.warn("[archiveById]", e);
    toast("すでに非表示の可能性があります");
  }
  await fetchList(true);
}
async function toggleInPosition(id, on){
  try{
    const res = await postJSON(API_UPSERT, {id, in_position:on});
    toast(res.ok ? (on?"INにしました":"OUTにしました") : "失敗しました");
  }catch(e){ console.error(e); toast("通信に失敗しました"); }
}

/* ----------- Sheet：Boardカードをそのまま埋め込む ----------- */
function buildCardFromSavedHTML(item){
  // reason_summary には Boardカードの「中身HTML」を入れてある前提
  // wrapperだけこちらで付与して完全再現
  const article = document.createElement("article");
  article.className = "card card--embed";
  article.innerHTML = item.reason_summary || "";
  return article;
}

function fallbackBuildCard(item){
  // 万一、旧データ（理由だけ）だった場合の保険
  const themeScore = Math.round((item.theme_score||0)*100);
  const actionText = item.action_text || "";
  const seg = item.segment || "";
  const reasonsHTML = item.reason_summary || (item.reason_details||[]).map(r=>`<li>・${r}</li>`).join("");
  const aiStar = (()=> {
    const w = Math.round((item.ai_win_prob||0)*5);
    return "★★★★★☆☆☆☆☆".slice(5-w,10-w);
  })();
  const wrap = document.createElement("article");
  wrap.className = "card card--embed";
  wrap.innerHTML = `
    <div class="title">${item.name||item.ticker} <span class="code">(${item.ticker})</span></div>
    <div class="segment">${seg}</div>
    <div class="action">${actionText}</div>
    <ul class="reasons">${reasonsHTML}</ul>
    <div class="targets">
      <div class="target">🎯 ${item.target_tp||"—"}</div>
      <div class="target">🛑 ${item.target_sl||"—"}</div>
    </div>
    <div class="ai-meter">
      <div class="meter-bar"><i style="width:${Math.max(8, Math.round((item.ai_win_prob||0)*100))}%"></i></div>
      <div>AI信頼度：${aiStar}</div>
    </div>
    <div class="theme-tag">🏷️ ${(item.theme_label||"") || "テーマ"} ${themeScore}点</div>`;
  return wrap;
}

function openSheet(item){
  state.current = item;
  const sh=$("#sheet"), body=sh.querySelector(".sheet-body");
  const apply=()=> body.style.bottom = computeBottomOffsetPx()+"px";
  apply(); body.style.height="62vh";
  __sheetViewportHandler = ()=> apply();
  if(window.visualViewport){ window.visualViewport.addEventListener("resize", __sheetViewportHandler); }

  $("#sh-title").textContent = `${item.name||item.ticker}（${item.ticker}）`;
  $("#sh-theme").textContent = item.theme_label ? `#${item.theme_label} ${Math.round((item.theme_score||0)*100)}点` : "";
  $("#sh-ai").textContent = item.ai_win_prob ? `AI ${Math.round((item.ai_win_prob||0)*100)}%` : "";

  // ★ Boardカード完全再現
  const host = $("#sh-boardcard");
  host.innerHTML = ""; // clear
  let card;
  try{
    card = buildCardFromSavedHTML(item);
    // 確認用に .buttons があれば削る（シート側でボタンは別）
    const btns = card.querySelector(".buttons"); if(btns) btns.remove();
  }catch(_){
    card = fallbackBuildCard(item);
  }
  host.appendChild(card);

  // メモ欄（従来通り）
  $("#sh-tp").textContent = item.target_tp ? `🎯 ${item.target_tp}` : "🎯 —";
  $("#sh-sl").textContent = item.target_sl ? `🛑 ${item.target_sl}` : "🛑 —";
  $("#sh-note").value = item.note || "";

  sh.hidden=false; sh.setAttribute("aria-hidden","false");
}
function closeSheet(){
  const sh=$("#sheet"), body=sh.querySelector(".sheet-body");
  if(window.visualViewport && __sheetViewportHandler){ window.visualViewport.removeEventListener("resize", __sheetViewportHandler); }
  __sheetViewportHandler=null;
  body.style.bottom=""; sh.hidden=true; sh.setAttribute("aria-hidden","true");
  state.current=null;
}

/* ----------- events ----------- */
document.addEventListener("click", async (e)=>{
  const sw=e.target.closest(".switch");
  const row=e.target.closest(".row");

  if(sw){
    const cell=sw.closest(".cell"); const id=Number(cell.dataset.id);
    const next=!sw.classList.contains("on");
    sw.classList.toggle("on", next);
    sw.querySelector("span").textContent = next ? "IN" : "OUT";
    await toggleInPosition(id, next);
    return;
  }
  if(row){
    const id=Number(row.closest(".cell").dataset.id);
    const it=state.items.find(x=>x.id===id);
    if(it) openSheet(it);
    return;
  }
  if(e.target.id==="sh-close"){ closeSheet(); return; }
  if(e.target.id==="sh-hide" && state.current){
    if(__hiding) return; __hiding = true;
    const id = state.current.id;
    closeSheet();
    await archiveById(id);
    __hiding = false;
    return;
  }
  if(e.target.id==="sh-save" && state.current){
    try{
      const note=$("#sh-note").value;
      const res = await postJSON(API_UPSERT, {id: state.current.id, note});
      toast(res.ok ? "メモを保存しました" : "保存に失敗しました");
      const idx = state.items.findIndex(x=>x.id===state.current.id);
      if(idx>=0){ state.items[idx].note = note; }
    }catch(err){ console.error(err); toast("保存に失敗しました"); }
  }
});
$("#sh-note").addEventListener("blur", async ()=>{
  if(!state.current) return;
  try{ await postJSON(API_UPSERT, {id: state.current.id, note: $("#sh-note").value}); }catch(e){}
});

$("#q").addEventListener("input", ()=>{
  state.q = $("#q").value.trim();
  clearTimeout(window.__qtimer);
  window.__qtimer = setTimeout(()=> fetchList(true), 250);
});
$("#more").addEventListener("click", ()=> fetchList(false));
document.addEventListener("DOMContentLoaded", ()=> fetchList(true));