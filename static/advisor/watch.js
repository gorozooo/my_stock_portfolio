console.log("[watch.js] v1 loaded");

const $ = s => document.querySelector(s);
let state = { q:"", items:[], next:null, busy:false };

function csrf() {
  // DjangoのcsrftokenをCookieから取得
  const m = document.cookie.match(/csrftoken=([^;]+)/);
  return m ? m[1] : "";
}

function toast(msg){
  const t = document.createElement("div");
  t.className = "toast"; t.textContent = msg; document.body.appendChild(t);
  requestAnimationFrame(()=> t.style.opacity = "1");
  setTimeout(()=>{ t.style.opacity = "0"; setTimeout(()=>t.remove(), 250); }, 1800);
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

    paint(data.items, !reset);
    $("#more").hidden = (state.next == null);
  }catch(e){
    console.error(e); toast("読み込みに失敗しました");
  }finally{
    state.busy = false;
  }
}

function paint(items, append){
  const list = $("#list");
  for(const it of items){
    const cell = document.createElement("article");
    cell.className = "cell";
    cell.dataset.ticker = it.ticker;
    cell.innerHTML = `
      <div class="row">
        <div class="name">
          <div class="tkr">${it.name ? it.name : it.ticker}</div>
          <div class="meta">${it.ticker}${it.name ? " / "+it.name : ""}</div>
        </div>
        <div class="actions">
          <button class="btn warn" data-act="archive">アーカイブ</button>
          <div class="switch ${it.in_position ? "on":""}" data-act="toggle"><i></i></div>
        </div>
      </div>
    `;
    attachSwipe(cell);
    list.appendChild(cell);
  }
}

function attachSwipe(cell){
  let sx=0, dx=0, dragging=false;
  cell.addEventListener("touchstart",(e)=>{ dragging=true; sx = e.touches[0].clientX; dx=0; },{passive:true});
  cell.addEventListener("touchmove",(e)=>{
    if(!dragging) return;
    dx = e.touches[0].clientX - sx;
    cell.style.transform = `translateX(${Math.max(-80, Math.min(80, dx))}px)`;
  },{passive:true});
  cell.addEventListener("touchend", async ()=>{
    if(!dragging) return; dragging=false;
    if(dx < -60){ // 左スワイプでアーカイブ
      await archiveTicker(cell.dataset.ticker);
      cell.remove(); toast("アーカイブしました");
    }else{
      cell.style.transform = "translateX(0)";
    }
  });
}

async function archiveTicker(ticker){
  const res = await fetch("/advisor/api/watch/archive/", {
    method:"POST",
    headers: {"Content-Type":"application/json", "X-CSRFToken": csrf()},
    body: JSON.stringify({ticker})
  });
  const data = await res.json();
  if(!data.ok) throw new Error(data.error || "archive error");
}

async function toggleInPosition(ticker, on){
  const res = await fetch("/advisor/api/watch/upsert/", {
    method:"POST",
    headers: {"Content-Type":"application/json", "X-CSRFToken": csrf()},
    body: JSON.stringify({ticker, in_position:on})
  });
  const data = await res.json();
  if(!data.ok) throw new Error(data.error || "toggle error");
}

document.addEventListener("click", async (e)=>{
  const btn = e.target.closest("button.btn"); 
  const sw = e.target.closest(".switch");
  if(btn){
    const cell = btn.closest(".cell"); const t = cell.dataset.ticker;
    if(btn.dataset.act==="archive"){
      try{ await archiveTicker(t); cell.remove(); toast("アーカイブしました"); }catch(err){ toast("失敗しました"); }
    }
  }else if(sw){
    const cell = sw.closest(".cell"); const t = cell.dataset.ticker;
    const next = !sw.classList.contains("on");
    sw.classList.toggle("on", next);
    try{ await toggleInPosition(t, next); toast(next?"INにしました":"OUTにしました"); }
    catch(err){ sw.classList.toggle("on", !next); toast("失敗しました"); }
  }
});

$("#q").addEventListener("input", ()=>{
  state.q = $("#q").value.trim();
  // 入力停止 250ms で検索
  clearTimeout(window.__qtimer);
  window.__qtimer = setTimeout(()=> fetchList(true), 250);
});

$("#more").addEventListener("click", ()=> fetchList(false));

document.addEventListener("DOMContentLoaded", ()=> fetchList(true));