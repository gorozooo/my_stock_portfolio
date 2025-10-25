console.log("[watch.js] v2 loaded");
const $ = s => document.querySelector(s);
let state = { q:"", items:[], next:null, busy:false, current:null };

function csrf(){
  const m = document.cookie.match(/csrftoken=([^;]+)/); return m? m[1] : "";
}
function toast(msg){
  const t=document.createElement("div"); t.className="toast"; t.textContent=msg; document.body.appendChild(t);
  requestAnimationFrame(()=> t.style.opacity="1"); setTimeout(()=>{ t.style.opacity="0"; setTimeout(()=>t.remove(),250); },1800);
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
    if(reset){ state.items = []; $("#list").innerHTML=""; }
    state.items = state.items.concat(data.items);
    state.next = data.next_cursor;
    $("#hit").textContent = `${state.items.length}${state.next!=null? "+":""}件`;

    paint(data.items);
    $("#more").hidden = (state.next == null);
  }catch(e){ console.error(e); toast("読み込みに失敗しました"); }
  finally{ state.busy=false; }
}

function paint(items){
  const list = $("#list");
  for(const it of items){
    const cell = document.createElement("article");
    cell.className="cell"; cell.dataset.ticker = it.ticker;
    const themeTag = it.theme_label ? `#${it.theme_label} ${Math.round(it.theme_score*100)}点` : "";
    const aiTag = it.ai_win_prob ? `AI ${Math.round(it.ai_win_prob*100)}%` : "";

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

function openSheet(item){
  state.current = item;
  $("#sheet").hidden = false; $("#sheet").setAttribute("aria-hidden","false");
  $("#sh-title").textContent = `${item.name||item.ticker}（${item.ticker}）`;
  $("#sh-theme").textContent = item.theme_label ? `#${item.theme_label} ${Math.round(item.theme_score*100)}点` : "";
  $("#sh-ai").textContent = item.ai_win_prob ? `AI ${Math.round(item.ai_win_prob*100)}%` : "";
  $("#sh-reasons").innerHTML = (item.reason_details||[]).map(r=>`<li>・${r}</li>`).join("") || "<li>理由なし</li>";
  $("#sh-tp").textContent = item.target_tp ? `🎯 ${item.target_tp}` : "🎯 —";
  $("#sh-sl").textContent = item.target_sl ? `🛑 ${item.target_sl}` : "🛑 —";
  $("#sh-note").value = item.note || "";
}
function closeSheet(){ $("#sheet").hidden = true; $("#sheet").setAttribute("aria-hidden","true"); state.current = null; }

document.addEventListener("click", async (e)=>{
  const row = e.target.closest(".row"); const sw = e.target.closest(".switch");
  if(sw){
    const cell = sw.closest(".cell"); const t = cell.dataset.ticker;
    const next = !sw.classList.contains("on");
    sw.classList.toggle("on", next); sw.querySelector("span").textContent = next? "IN":"OUT";
    try{ await toggleInPosition(t, next); toast(next?"INにしました":"OUTにしました"); }
    catch(err){ sw.classList.toggle("on", !next); sw.querySelector("span").textContent = (!next?"IN":"OUT"); toast("失敗しました"); }
  }else if(row){
    // 詳細シート
    const t = row.closest(".cell").dataset.ticker;
    const item = state.items.find(x=>x.ticker===t);
    if(item) openSheet(item);
  }
  if(e.target.id==="sh-close"){ closeSheet(); }
  if(e.target.id==="sh-hide" && state.current){
    try{ await archiveTicker(state.current.ticker); toast("非表示にしました"); closeSheet();
      // 画面からも削除
      const cell = document.querySelector(`.cell[data-ticker="${state.current.ticker}"]`); cell && cell.remove();
    }catch(err){ toast("失敗しました"); }
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
  }
});

$("#q").addEventListener("input", ()=>{
  state.q = $("#q").value.trim();
  clearTimeout(window.__qtimer); window.__qtimer = setTimeout(()=> fetchList(true), 250);
});
$("#more").addEventListener("click", ()=> fetchList(false));
document.addEventListener("DOMContentLoaded", ()=> fetchList(true));