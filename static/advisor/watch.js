console.log("[watch.js] v3 loaded");
const $ = s => document.querySelector(s);
let state = { q:"", items:[], next:null, busy:false, current:null };
let __sheetViewportHandler = null;

function csrf(){
  const m = document.cookie.match(/csrftoken=([^;]+)/); return m? m[1] : "";
}
function toast(msg){
  const t=document.createElement("div"); t.className="toast"; t.textContent=msg; document.body.appendChild(t);
  requestAnimationFrame(()=> t.style.opacity="1"); setTimeout(()=>{ t.style.opacity="0"; setTimeout(()=>t.remove(),250); },1800);
}

/* ===== 端末の下UIを避けるための安全オフセットを計算 ===== */
function computeBottomOffsetPx(){
  let inset = 0;
  if (window.visualViewport){
    const diff = window.innerHeight - window.visualViewport.height; // キーボード/ホームバー等の食い込み
    inset = Math.max(0, Math.round(diff));
  }
  // 固定タブぶん + 余白（iOSのSafariのUIも考慮）
  return inset + 120; // ←必要に応じて 120〜140 の間で調整可
}

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
  }catch(e){ console.error(e); toast("読み込みに失敗しました"); }
  finally{ state.busy=false; }
}

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
      await archiveTicker(ticker);
      cell.remove(); toast("非表示にしました");
    }else{
      cell.style.transform = "translateX(0)";
    }
  });
}

async function archiveTicker(ticker){
  const res = await fetch("/advisor/api/watch/archive/", {
    method:"POST",
    headers: {"Content-Type":"application/json","X-CSRFToken": csrf()},
    body: JSON.stringify({ticker})
  });
  const data = await res.json();
  if(!data.ok) throw new Error(data.error || "archive error");
}

async function toggleInPosition(ticker, on){
  const res = await fetch("/advisor/api/watch/upsert/", {
    method:"POST",
    headers: {"Content-Type":"application/json","X-CSRFToken": csrf()},
    body: JSON.stringify({ticker, in_position:on})
  });
  const data = await res.json();
  if(!data.ok) throw new Error(data.error || "toggle error");
}

/* ===== bottom-sheet 開閉 ===== */
function openSheet(item){
  state.current = item;
  const sheet = $("#sheet");
  const body  = sheet.querySelector(".sheet-body");

  // 下UIに被らないように bottom を実機値で上書き
  const applyBottom = ()=>{ body.style.bottom = computeBottomOffsetPx() + "px"; };
  applyBottom();

  // 真ん中寄せの高さ（CSSの 62vh のままでOK。必要ならJSで上書き可）
  body.style.height = "62vh";

  // 回転/キーボード出現にも追従
  __sheetViewportHandler = ()=> applyBottom();
  if (window.visualViewport){
    window.visualViewport.addEventListener("resize", __sheetViewportHandler);
  }

  // データ挿入
  $("#sh-title").textContent = `${item.name||item.ticker}（${item.ticker}）`;
  $("#sh-theme").textContent = item.theme_label ? `#${item.theme_label} ${Math.round(item.theme_score*100)}点` : "";
  $("#sh-ai").textContent = item.ai_win_prob ? `AI ${Math.round(item.ai_win_prob*100)}%` : "";
  $("#sh-reasons").innerHTML = (item.reason_details||[]).map(r=>`<li>・${r}</li>`).join("") || "<li>理由なし</li>";
  $("#sh-tp").textContent = item.target_tp ? `🎯 ${item.target_tp}` : "🎯 —";
  $("#sh-sl").textContent = item.target_sl ? `🛑 ${item.target_sl}` : "🛑 —";
  $("#sh-note").value = item.note || "";

  sheet.hidden = false; sheet.setAttribute("aria-hidden","false");
}

function closeSheet(){
  const sheet = $("#sheet");
  const body  = sheet.querySelector(".sheet-body");
  if (window.visualViewport && __sheetViewportHandler){
    window.visualViewport.removeEventListener("resize", __sheetViewportHandler);
  }
  __sheetViewportHandler = null;
  body.style.bottom = ""; // クリア
  sheet.hidden = true; sheet.setAttribute("aria-hidden","true");
  state.current = null;
}

/* ===== クリック ===== */
document.addEventListener("click", async (e)=>{
  const row = e.target.closest(".row"); 
  const sw  = e.target.closest(".switch");

  if(sw){
    const cell = sw.closest(".cell"); const t = cell.dataset.ticker;
    const next = !sw.classList.contains("on");
    sw.classList.toggle("on", next); sw.querySelector("span").textContent = next? "IN":"OUT";
    try{ await toggleInPosition(t, next); toast(next?"INにしました":"OUTにしました"); }
    catch(err){ sw.classList.toggle("on", !next); sw.querySelector("span").textContent = (!next?"IN":"OUT"); toast("失敗しました"); }
    return;
  }

  if(row){
    const t = row.closest(".cell").dataset.ticker;
    const item = state.items.find(x=>x.ticker===t);
    if(item) openSheet(item);
    return;
  }

  if(e.target.id==="sh-close"){ closeSheet(); return; }

  if(e.target.id==="sh-hide" && state.current){
    try{
      await archiveTicker(state.current.ticker);
      toast("非表示にしました");
      closeSheet();
      const cell = document.querySelector(`.cell[data-ticker="${state.current.ticker}"]`);
      cell && cell.remove();
    }catch(err){ toast("失敗しました"); }
    return;
  }

  if(e.target.id==="sh-save" && state.current){
    try{
      const note = $("#sh-note").value;
      const res = await fetch("/advisor/api/watch/upsert/", {
        method:"POST", headers: {"Content-Type":"application/json","X-CSRFToken": csrf()},
        body: JSON.stringify({ticker: state.current.ticker, note})
      });
      const data = await res.json(); if(!data.ok) throw new Error(data.error || "save error");
      state.current.note = note; toast("メモを保存しました");
    }catch(err){ toast("保存に失敗しました"); }
    return;
  }
});

/* ===== 検索・ページング ===== */
$("#q").addEventListener("input", ()=>{
  state.q = $("#q").value.trim();
  clearTimeout(window.__qtimer); window.__qtimer = setTimeout(()=> fetchList(true), 250);
});
$("#more").addEventListener("click", ()=> fetchList(false));

document.addEventListener("DOMContentLoaded", ()=> fetchList(true));