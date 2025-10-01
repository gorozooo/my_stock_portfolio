(() => {
  const $ = (s, r=document) => r.querySelector(s);
  const $all = (s, r=document) => Array.from(r.querySelectorAll(s));
  const API = "/dividends/calendar.json";

  function yen(n){ try{
    const v = Math.round(parseFloat(n||0));
    return v.toLocaleString("ja-JP",{style:"currency",currency:"JPY",maximumFractionDigits:0});
  }catch(_){ return "¥0"; } }

  function firstDow(y,m){ return new Date(y,m-1,1).getDay(); }
  function lastDay(y,m){ return new Date(y,m,0).getDate(); }

  function paintGrid(payload){
    const cal = $("#cal"); if(!cal) return;
    const y = payload.year, m = payload.month;
    const fd = firstDow(y,m), ld = lastDay(y,m);
    // 1) 全セル初期化
    $all(".cell",cal).forEach(c => { c.innerHTML = '<div class="d"></div>'; c.dataset.day=""; c.onclick=null; });
    // 2) 日付を配置（1..ld を fd オフセットして入れる）
    for(let d=1; d<=ld; d++){
      const idx = fd + (d-1);
      const cell = cal.querySelector(`.cell[data-idx="${idx}"]`);
      if(!cell) continue;
      cell.dataset.day = String(d);
      cell.querySelector(".d").textContent = d;
    }
    // 3) バッジ
    (payload.days||[]).forEach(b=>{
      const idx = fd + (b.d-1);
      const cell = cal.querySelector(`.cell[data-idx="${idx}"]`);
      if(!cell) return;
      if(b.total>0){
        const badge = document.createElement("div");
        badge.className = "badge";
        badge.innerHTML = `<small>計</small>${(Math.round(b.total)).toLocaleString()}`;
        cell.appendChild(badge);
        // 詳細モーダル
        cell.style.cursor="pointer";
        cell.onclick = ()=>openSheet(y,m,b);
      }
    });
    cal.dataset.year = y; cal.dataset.month = m;
  }

  // ボトムシート
  function openSheet(y,m,b){
    $("#sheetTitle").textContent = `${y}年${m}月${b.d}日`;
    $("#sheetSum").textContent = `合計：${yen(b.total)}`;
    const list = $("#sheetList"); list.innerHTML = "";
    (b.items||[]).forEach(it=>{
      const row = document.createElement("div");
      row.className="row";
      row.innerHTML = `<div class="name">${escapeHtml(it.name||it.ticker||"—")}</div>
                       <div class="amt">${yen(it.net)}</div>`;
      list.appendChild(row);
    });
    $("#sheet").classList.add("is-open");
  }
  function escapeHtml(s){ return (s||"").replace(/[&<>"']/g, m=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[m])); }

  function closeSheet(){ $("#sheet").classList.remove("is-open"); }
  $("#sheetClose")?.addEventListener("click", closeSheet);

  // フィルター変更→再読み込み
  function currentQuery(){
    return {
      year:   $("#fYear")?.value || new Date().getFullYear(),
      month:  $("#fMonth")?.value || (new Date().getMonth()+1),
      broker: $("#fBroker")?.value || "",
      account:$("#fAccount")?.value || "",
    };
  }
  function fetchAndRender(q){
    const p = new URLSearchParams(q);
    fetch(`${API}?${p.toString()}`, {credentials:"same-origin"})
      .then(r=>r.json()).then(json=>paintGrid(json)).catch(()=>{/*no-op*/});
  }
  ["change","input"].forEach(ev=>{
    $("#calFilter")?.addEventListener(ev, ()=>{
      closeSheet();
      fetchAndRender(currentQuery());
    });
  });

  // 初期表示：サーバが埋めた JSON を使う（なければ API に取りに行く）
  const init = window.__DIVCAL_INIT__;
  if(init && init.year && init.month){ paintGrid(init); }
  else { fetchAndRender(currentQuery()); }
})();