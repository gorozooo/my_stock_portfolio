console.log("[watch.js] v4 loaded");
const $ = s => document.querySelector(s);
let state = { q:"", items:[], next:null, busy:false, current:null };
let __sheetViewportHandler = null;
let __hiding = false; // 二重押しガード

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
  setTimeout(()=>{
    t.style.opacity="0";
    setTimeout(()=>t.remove(),250);
  },1800);
}

/* ===== 端末の下UIを避けるための安全オフセットを計算 ===== */
function computeBottomOffsetPx(){
  let inset = 0;
  if (window.visualViewport){
    const diff = window.innerHeight - window.visualViewport.height; // キーボード/ホームバー等の食い込み
    inset = Math.max(0, Math.round(diff));
  }
  // 固定タブぶん + 余白（iOSのSafariのUIも考慮）
  return inset + 120; // ←必要に応じて120〜140で調整可
}

/* ===== 非表示API ===== */
async function archiveTicker(ticker){
  const res = await fetch("/advisor/api/watch/archive/", {
    method:"POST",
    headers: {"Content-Type":"application/json","X-CSRFToken": csrf()},
    body: JSON.stringify({ticker})
  });
  const text = await res.text();
  let data = {};
  try { data = JSON.parse(text); } catch(_) {}
  if(!res.ok || !data.ok){
    const msg = data.error || `HTTP ${res.status} ${text}`;
    throw new Error(msg);
  }
  return data;
}

/* ===== IN/OUT トグル ===== */
async function toggleInPosition(ticker, on){
  const res = await fetch("/advisor/api/watch/upsert/", {
    method:"POST",
    headers: {"Content-Type":"application/json","X-CSRFToken": csrf()},
    body: JSON.stringify({ticker, in_position:on})
  });
  const text = await res.text();
  let data={};
  try{data=JSON.parse(text);}catch(_){}
  if(!res.ok || !data.ok) throw new Error(data.error || `HTTP ${res.status}`);
}

/* ===== 一覧取得 ===== */
async function fetchList(reset=false){
  if(state.busy) return; state.busy = true;
  try{
    const params = new URLSearchParams();
    if(state.q) params.set("q", state.q);
    if(!reset && state.next!=null) params.set("cursor", state.next);
    params.set("limit","20");

    const res = await fetch(`/advisor/api/watch/list/?${params.toString()}`, {credentials:"same-origin"});
    const data = await res.json();
    if(!data.ok) throw new Error(data.error || "fetch error");
    if(reset){ state.items = []; $("#list").innerHTML = ""; }
    state.items = state.items.concat(data.items);
    state.next = data.next_cursor;

    $("#hit") && ($("#hit").textContent = `${state.items.length}${state.next!=null? "+":""}件`);
    paint(data.items);
    $("#more").hidden = (state.next == null);
  }catch(e){
    console.error("[fetchList]", e);
    toast("読み込みに失敗しました");
  }finally{
    state.busy=false;
  }
}

/* ===== 一覧描画 ===== */
function paint(items){
  const list = $("#list");
  for(const it of items){
    const cell = document.createElement("article");
    cell.className = "cell";
    cell.dataset.ticker = it.ticker;
    cell.innerHTML = `
      <div class="row" data-act="open">
        <div class="name">
          <div class="line1">${(it.name||it.ticker)}（${it.ticker}）</div>
          <div class="line2">${it.reason_summary || ""}</div>
        </div>
        <div class="actions">
          <div class="switch ${it.in_position? "on": ""}" data-act="toggle">
            <span>${it.in_position? "IN":"OUT"}</span><i></i>
          </div>
        </div>
      </div>
    `;
    attachSwipe(cell, it.ticker);
    list.appendChild(cell);
  }
}

/* ===== スワイプ非表示 ===== */
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
    if(dx < -60){
      try{
        await archiveTicker(ticker);
        toast("非表示にしました");
        await fetchList(true); // 最新化
      }catch(err){
        console.error("[archive swipe] error:", err);
        toast("失敗しました");
        cell.style.transform = "translateX(0)";
      }
    }else{
      cell.style.transform = "translateX(0)";
    }
  });
}

/* ===== bottom-sheet 開閉 ===== */
function openSheet(item){
  state.current = item;
  const sheet = $("#sheet");
  const body  = sheet.querySelector(".sheet-body");

  // bottom位置を調整
  const applyBottom = ()=>{ body.style.bottom = computeBottomOffsetPx() + "px"; };
  applyBottom();

  body.style.height = "62vh";
  __sheetViewportHandler = ()=> applyBottom();
  if (window.visualViewport){
    window.visualViewport.addEventListener("resize", __sheetViewportHandler);
  }

  $("#sh-title").textContent = `${item.name||item.ticker}（${item.ticker}）`;
  $("#sh-theme").textContent = item.theme_label ? `#${item.theme_label} ${Math.round(item.theme_score*100)}点` : "";
  $("#sh-ai").textContent = item.ai_win_prob ? `AI ${Math.round(item.ai_win_prob*100)}%` : "";
  $("#sh-reasons").innerHTML = (item.reason_details||[]).map(r=>`<li>・${r}</li>`).join("") || "<li>理由なし</li>";
  $("#sh-tp").textContent = item.target_tp ? `🎯 ${item.target_tp}` : "🎯 —";
  $("#sh-sl").textContent = item.target_sl ? `🛑 ${item.target_sl}` : "🛑 —";
  $("#sh-note").value = item.note || "";

  sheet.hidden = false;
  sheet.setAttribute("aria-hidden","false");
}

function closeSheet(){
  const sheet = $("#sheet");
  const body  = sheet.querySelector(".sheet-body");
  if (window.visualViewport && __sheetViewportHandler){
    window.visualViewport.removeEventListener("resize", __sheetViewportHandler);
  }
  __sheetViewportHandler = null;
  body.style.bottom = "";
  sheet.hidden = true;
  sheet.setAttribute("aria-hidden","true");
  state.current = null;
}

/* ===== クリックイベント ===== */
document.addEventListener("click", async (e)=>{
  const row = e.target.closest(".row"); 
  const sw  = e.target.closest(".switch");

  // IN/OUT トグル
  if(sw){
    const cell = sw.closest(".cell"); const t = cell.dataset.ticker;
    const next = !sw.classList.contains("on");
    sw.classList.toggle("on", next);
    sw.querySelector("span").textContent = next? "IN":"OUT";
    try{
      await toggleInPosition(t, next);
      toast(next?"INにしました":"OUTにしました");
    }catch(err){
      console.error("[toggle]", err);
      sw.classList.toggle("on", !next);
      sw.querySelector("span").textContent = !next? "IN":"OUT";
      toast("失敗しました");
    }
    return;
  }

  // rowクリック → 詳細
  if(row){
    const t = row.closest(".cell").dataset.ticker;
    const item = state.items.find(x=>x.ticker===t);
    if(item) openSheet(item);
    return;
  }

  // シート閉じる
  if(e.target.id==="sh-close"){ closeSheet(); return; }

  // シート：非表示
  if(e.target.id==="sh-hide" && state.current){
    if(__hiding) return;
    __hiding = true;
    try{
      await archiveTicker(state.current.ticker);
      toast("非表示にしました");
      closeSheet();
      await fetchList(true);
    }catch(err){
      console.error("[archive sheet] error:", err);
      toast("失敗しました");
    }finally{
      __hiding = false;
    }
    return;
  }

  // シート：保存
  if(e.target.id==="sh-save" && state.current){
    try{
      const note = $("#sh-note").value;
      const res = await fetch("/advisor/api/watch/upsert/", {
        method:"POST",
        headers: {"Content-Type":"application/json","X-CSRFToken": csrf()},
        body: JSON.stringify({ticker: state.current.ticker, note})
      });
      const data = await res.json();
      if(!data.ok) throw new Error(data.error || "save error");
      state.current.note = note;
      toast("メモを保存しました");
    }catch(err){
      console.error("[save note]", err);
      toast("保存に失敗しました");
    }
    return;
  }
});

/* ===== 検索 ===== */
$("#q").addEventListener("input", ()=>{
  state.q = $("#q").value.trim();
  clearTimeout(window.__qtimer);
  window.__qtimer = setTimeout(()=> fetchList(true), 250);
});
$("#more").addEventListener("click", ()=> fetchList(false));
document.addEventListener("DOMContentLoaded", ()=> fetchList(true));