console.log("[watch.js] v2025-10-26-ok-only-check loaded");
const $ = s => document.querySelector(s);
let state = { q:"", items:[], next:null, busy:false, current:null };
let __sheetViewportHandler = null;
let __hiding = false;

/* ================= 共通ユーティリティ ================= */
function csrf(){
  const m = document.cookie.match(/csrftoken=([^;]+)/);
  return m ? m[1] : "";
}
function toast(msg){
  const t=document.createElement("div");
  t.className="toast";
  t.textContent=msg;
  document.body.appendChild(t);
  requestAnimationFrame(()=> t.style.opacity="1");
  setTimeout(()=>{ t.style.opacity="0"; setTimeout(()=>t.remove(),250); },1800);
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
        method:"POST",
        credentials:"same-origin",
        headers:{
          "Content-Type":"application/json",
          "X-CSRFToken": csrf()
        },
        body: JSON.stringify(body)
      });
      if(res.status === 404) continue;
      const data = await res.json();
      if(!res.ok) throw new Error(`HTTP ${res.status}`);
      return data;
    }catch(e){ console.warn("[postJSON err]", e); }
  }
  throw new Error("all failed");
}

/* ================= APIエンドポイント ================= */
const API_LIST    = ["/advisor/api/watch/list/",    "/advisor/watch/list/"];
const API_UPSERT  = ["/advisor/api/watch/upsert/",  "/advisor/watch/upsert/"];
const API_ARCHIVE = ["/advisor/api/watch/archive/", "/advisor/watch/archive/"];

/* ================= メイン処理 ================= */
async function archiveTicker(ticker){
  const res = await postJSON(API_ARCHIVE, {ticker});
  // okだけ見てUI更新
  if(res.ok){
    toast(res.status === "archived" ? "非表示にしました" : "すでに非表示でした");
    await fetchList(true);
  }else{
    toast("失敗しました");
  }
}

async function toggleInPosition(ticker,on){
  const res = await postJSON(API_UPSERT, {ticker, in_position:on});
  if(res.ok){
    toast(on?"INにしました":"OUTにしました");
  }else{
    toast("失敗しました");
  }
}

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
    state.next = data.next_cursor;
    $("#hit") && ($("#hit").textContent=`${state.items.length}${state.next!=null?"+":""}件`);
    paint(data.items);
    $("#more").hidden = (state.next==null);
  }catch(e){
    console.error(e);
    toast("読み込みに失敗しました");
  }finally{ state.busy=false; }
}

function paint(items){
  const list=$("#list");
  for(const it of items){
    const cell=document.createElement("article");
    cell.className="cell"; cell.dataset.ticker=it.ticker;
    cell.innerHTML=`
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
    attachSwipe(cell,it.ticker);
    list.appendChild(cell);
  }
}

/* スワイプで非表示 */
function attachSwipe(cell,ticker){
  let sx=0,dx=0,drag=false;
  cell.addEventListener("touchstart",e=>{drag=true;sx=e.touches[0].clientX;dx=0;},{passive:true});
  cell.addEventListener("touchmove",e=>{
    if(!drag)return;
    dx=e.touches[0].clientX-sx;
    cell.style.transform=`translateX(${Math.max(-80,Math.min(80,dx))}px)`;
  },{passive:true});
  cell.addEventListener("touchend",async()=>{
    if(!drag)return;drag=false;
    if(dx<-60){ await archiveTicker(ticker); }
    cell.style.transform="translateX(0)";
  });
}

/* 詳細シート */
function openSheet(it){
  state.current=it;
  const sh=$("#sheet"),body=sh.querySelector(".sheet-body");
  body.style.bottom=computeBottomOffsetPx()+"px";
  body.style.height="62vh";
  $("#sh-title").textContent=`${it.name||it.ticker}（${it.ticker}）`;
  $("#sh-theme").textContent=it.theme_label?`#${it.theme_label} ${Math.round(it.theme_score*100)}点`:"";
  $("#sh-ai").textContent=it.ai_win_prob?`AI ${Math.round(it.ai_win_prob*100)}%`:"";
  $("#sh-reasons").innerHTML=(it.reason_details||[]).map(r=>`<li>・${r}</li>`).join("")||"<li>理由なし</li>";
  $("#sh-tp").textContent=it.target_tp?`🎯 ${it.target_tp}`:"🎯 —";
  $("#sh-sl").textContent=it.target_sl?`🛑 ${it.target_sl}`:"🛑 —";
  $("#sh-note").value=it.note||"";
  sh.hidden=false;
}

/* 閉じる */
function closeSheet(){
  $("#sheet").hidden=true;
  state.current=null;
}

/* イベント */
document.addEventListener("click",async e=>{
  const sw=e.target.closest(".switch");
  const row=e.target.closest(".row");
  if(sw){
    const cell=sw.closest(".cell");const t=cell.dataset.ticker;
    const next=!sw.classList.contains("on");
    sw.classList.toggle("on",next);
    sw.querySelector("span").textContent=next?"IN":"OUT";
    await toggleInPosition(t,next);
    return;
  }
  if(row){
    const t=row.closest(".cell").dataset.ticker;
    const it=state.items.find(x=>x.ticker===t);
    if(it) openSheet(it);
    return;
  }
  if(e.target.id==="sh-close"){closeSheet();return;}
  if(e.target.id==="sh-hide"&&state.current){await archiveTicker(state.current.ticker);closeSheet();return;}
  if(e.target.id==="sh-save"&&state.current){
    try{
      const note=$("#sh-note").value;
      await postJSON(API_UPSERT,{ticker:state.current.ticker,note});
      toast("保存しました");
    }catch(e){toast("保存失敗");}
  }
});

/* 検索 */
$("#q").addEventListener("input",()=>{
  state.q=$("#q").value.trim();
  clearTimeout(window.__qtimer);
  window.__qtimer=setTimeout(()=>fetchList(true),250);
});
$("#more").addEventListener("click",()=>fetchList(false));
document.addEventListener("DOMContentLoaded",()=>fetchList(true));