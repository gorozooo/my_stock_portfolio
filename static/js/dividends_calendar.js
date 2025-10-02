(() => {
  const $  = (s, r=document)=>r.querySelector(s);
  const $$ = (s, r=document)=>Array.from(r.querySelectorAll(s));
  const API = "/dividends/calendar.json";

  const yen = n => {
    try {
      return Math.round(parseFloat(n || 0))
        .toLocaleString("ja-JP", { style: "currency", currency: "JPY", maximumFractionDigits: 0 });
    } catch (_){ return "¥0"; }
  };

  const firstDow = (y,m)=> new Date(y, m-1, 1).getDay();
  const lastDay  = (y,m)=> new Date(y, m, 0).getDate();

  function paintGrid(payload){
    const cal = $("#cal"); if(!cal) return;
    const y = payload.year, m = payload.month;
    const fd = firstDow(y,m), ld = lastDay(y,m);

    // 初期化（42セル固定）
    $$(".cell", cal).forEach(c=>{
      c.classList.remove("has-items");
      c.innerHTML = '<div class="d"></div>';
      c.dataset.day = "";
      c.onclick = null;
    });

    // 日付を入れる
    for(let d=1; d<=ld; d++){
      const idx = fd + (d-1);
      const cell = cal.querySelector(`.cell[data-idx="${idx}"]`);
      if(!cell) continue;
      cell.dataset.day = String(d);
      cell.querySelector(".d").textContent = d;
    }

    // 件数ドット
    (payload.days || []).forEach(day=>{
      if(!day || !day.d) return;
      const idx  = fd + (day.d - 1);
      const cell = cal.querySelector(`.cell[data-idx="${idx}"]`);
      if(!cell) return;

      const count = (day.items || []).length;
      if(count > 0){
        const dot = document.createElement("div");
        dot.className = "dot";
        dot.textContent = count;    // ← 件数のみ
        cell.appendChild(dot);
        cell.classList.add("has-items");
        cell.onclick = ()=>openSheet(y, m, day);
      }
    });

    cal.dataset.year  = y;
    cal.dataset.month = m;
  }

  // ====== Bottom Sheet ======
  function openSheet(y,m,day){
    $("#sheetTitle").textContent = `${y}年${m}月${day.d}日`;
    $("#sheetSum").textContent   = `合計：${yen(day.total || 0)}`;

    const list = $("#sheetList");
    list.innerHTML = "";
    (day.items || []).forEach(it=>{
      const row = document.createElement("div");
      row.className = "row";
      row.innerHTML = `
        <div class="name">${escapeHtml(it.name || it.ticker || "—")}</div>
        <div class="amt">${yen(it.net || 0)}</div>`;
      list.appendChild(row);
    });

    $("#backdrop").classList.add("is-open");
    $("#sheet").classList.add("is-open");
  }
  function closeSheet(){
    $("#sheet").classList.remove("is-open");
    $("#backdrop").classList.remove("is-open");
  }
  $("#sheetClose")?.addEventListener("click", closeSheet);
  $("#backdrop")?.addEventListener("click", closeSheet);
  window.addEventListener("keydown", (e)=>{ if(e.key === "Escape") closeSheet(); });

  const escapeHtml = s => (s || "").replace(/[&<>"']/g, m=>(
    {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[m]
  ));

  // ====== フィルタ変更で再取得 ======
  function qNow(){
    return {
      year:    $("#fYear")?.value   || new Date().getFullYear(),
      month:   $("#fMonth")?.value  || (new Date().getMonth() + 1),
      broker:  $("#fBroker")?.value || "",
      account: $("#fAccount")?.value|| "",
    };
  }
  function fetchAndRender(q){
    const p = new URLSearchParams(q);
    fetch(`${API}?${p.toString()}`, { credentials: "same-origin" })
      .then(r => r.json())
      .then(json => paintGrid(json))
      .catch(()=>{/* no-op */});
  }
  ["change","input"].forEach(ev=>{
    $("#calFilter")?.addEventListener(ev, ()=>{
      closeSheet();
      fetchAndRender(qNow());
    });
  });

  // 初期描画（サーバ埋め込み or API）
  const init = window.__DIVCAL_INIT__;
  if(init && init.year && init.month) paintGrid(init);
  else fetchAndRender(qNow());
})();