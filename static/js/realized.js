document.addEventListener("DOMContentLoaded", function() {
  const yearFilter  = document.getElementById("yearFilter");
  const monthFilter = document.getElementById("monthFilter");
  const table       = document.getElementById("realizedTable");
  const tbody       = table.querySelector("tbody");
  const allRows     = [...tbody.querySelectorAll("tr")];
  const dataRows    = allRows.filter(r => !r.classList.contains('group-row'));

  const emptyState  = document.getElementById("emptyState");

  // サマリー要素
  const sumCount  = document.getElementById("sumCount");
  const sumProfit = document.getElementById("sumProfit");
  const avgProfit = document.getElementById("avgProfit");
  const winRateEl = document.getElementById("winRate");

  /* ========= 数値ユーティリティ ========= */
  function numeric(valueText) {
    const t = (valueText || "").toString().replace(/[^\-0-9.]/g, "");
    const v = parseFloat(t);
    return isNaN(v) ? 0 : v;
  }

  /* ========= フィルタ ========= */
  function filterTable() {
    const year  = yearFilter.value;
    const month = monthFilter.value;

    dataRows.forEach(row => {
      const date = row.dataset.date || "";
      const [yy, mm] = date.split("-");
      let show = true;
      if (year  && yy !== year)  show = false;
      if (month && mm !== month) show = false;
      row.style.display = show ? "" : "none";
    });

    toggleEmpty();
    updateSummary();
  }

  function toggleEmpty() {
    const anyVisible = dataRows.some(r => r.style.display !== "none");
    emptyState.style.display = anyVisible ? "none" : "";
  }

  function updateSummary() {
    const vis = dataRows.filter(r => r.style.display !== "none");
    const profits = vis.map(r => numeric(r.children[4]?.textContent)); // 損益額
    const wins = vis.filter(r => numeric(r.children[4]?.textContent) > 0).length;

    const sum = profits.reduce((a,b)=>a+b,0);
    const avg = profits.length ? sum / profits.length : 0;
    const winRate = vis.length ? Math.round((wins / vis.length) * 100) : 0;

    sumCount.textContent  = String(vis.length);
    sumProfit.textContent = Math.round(sum).toLocaleString();
    avgProfit.textContent = Math.round(avg).toLocaleString();
    winRateEl.textContent = `${winRate}%`;
  }

  yearFilter.addEventListener("change", filterTable);
  monthFilter.addEventListener("change", filterTable);

  /* ========= クイックフィルタ ========= */
  function pad2(n){ return n < 10 ? "0"+n : ""+n; }
  function setYearMonth(y, m) {
    yearFilter.value  = y || "";
    monthFilter.value = m || "";
    filterTable();
  }
  document.querySelectorAll(".quick-chips button").forEach(b=>{
    b.addEventListener("click", ()=>{
      const now = new Date();
      const y = now.getFullYear();
      const m = now.getMonth()+1;
      const mm = pad2(m);
      const key = b.dataset.range;

      if (key === "this-month") setYearMonth(String(y), mm);
      else if (key === "last-month") {
        const d = new Date(y, m-2, 1);
        setYearMonth(String(d.getFullYear()), pad2(d.getMonth()+1));
      } else if (key === "this-year") setYearMonth(String(y), "");
      else if (key === "all") setYearMonth("", "");
    });
  });

  /* ========= ソート（ヘッダークリック） ========= */
  table.querySelectorAll("thead th").forEach((th, idx)=>{
    th.addEventListener("click", ()=>{
      const asc = th.dataset.asc !== "true";
      th.dataset.asc = asc;

      const visibleRows = dataRows.filter(r => r.style.display !== "none");
      const isDateCol = idx === 0;

      visibleRows.sort((a,b)=>{
        let va, vb;
        if (isDateCol) {
          va = new Date(a.children[idx].textContent.trim());
          vb = new Date(b.children[idx].textContent.trim());
        } else {
          va = numeric(a.children[idx].textContent);
          vb = numeric(b.children[idx].textContent);
        }
        return asc ? (va > vb ? 1 : -1) : (va < vb ? 1 : -1);
      });

      // 再配置：まず可視行を順に、次に不可視行
      const hiddenRows = dataRows.filter(r => r.style.display === "none");
      [...visibleRows, ...hiddenRows].forEach(r => tbody.appendChild(r));
    });
  });

  /* ========= 行タップでモーダル（誤タップ防止ロジック） ========= */
  const modal      = document.getElementById("stockModal");
  const panel      = modal.querySelector(".modal-content");
  const closeBtn   = modal.querySelector(".close");
  const body       = document.body;

  const modalName     = document.getElementById("modalName");
  const modalPrice    = document.getElementById("modalPrice");
  const modalSector   = document.getElementById("modalSector");
  const modalPurchase = document.getElementById("modalPurchase");
  const modalQuantity = document.getElementById("modalQuantity");
  const modalProfit   = document.getElementById("modalProfit");
  const modalRate     = document.getElementById("modalRate");

  function openModalForRow(row){
    modalName.textContent     = row.dataset.name     || "";
    modalPrice.textContent    = row.dataset.price    || "";
    modalSector.textContent   = row.dataset.sector   || "";
    modalPurchase.textContent = row.dataset.purchase || "";
    modalQuantity.textContent = row.dataset.quantity || "";
    modalProfit.textContent   = row.dataset.profit   || "";
    modalRate.textContent     = row.dataset.rate     || "";

    modal.classList.add("show");
    modal.style.display = "flex";
    body.style.overflow = "hidden";
  }

  // スクロールとタップの判定
  const TAP_MAX_MOVE = 10;   // px（これ以上動いたらスクロール扱い）
  const TAP_MAX_TIME = 500;  // ms（長押しは無視：必要なら調整）
  dataRows.forEach(row => {
    let startX=0, startY=0, startT=0, moved=false;

    row.addEventListener("touchstart", (e)=>{
      const t = e.touches[0];
      startX = t.clientX; startY = t.clientY; startT = Date.now(); moved = false;
    }, {passive:true});

    row.addEventListener("touchmove", (e)=>{
      const t = e.touches[0];
      const dx = Math.abs(t.clientX - startX);
      const dy = Math.abs(t.clientY - startY);
      if (dx > TAP_MAX_MOVE || dy > TAP_MAX_MOVE) moved = true; // スクロール中
    }, {passive:true});

    row.addEventListener("touchend", (e)=>{
      const dt = Date.now() - startT;
      if (!moved && dt <= TAP_MAX_TIME && row.style.display !== "none") {
        e.preventDefault();
        openModalForRow(row);
      }
    });

    // マウス（PC）クリックはそのまま
    row.addEventListener("click", (e)=>{
      if (row.style.display !== "none") openModalForRow(row);
    });
  });

  function closeModal(){
    modal.classList.remove("show");
    body.style.overflow = "";
    setTimeout(()=>{ modal.style.display = "none"; }, 300);
  }

  closeBtn.addEventListener("click", closeModal);
  window.addEventListener("click", (e)=>{ if (e.target === modal) closeModal(); });

  // 下スワイプで閉じる
  (function enableSwipeToClose(){
    let startY=0, dy=0;
    panel.addEventListener("touchstart", e=>{
      startY = e.touches[0].clientY; dy = 0;
    }, {passive:true});
    panel.addEventListener("touchmove", e=>{
      dy = e.touches[0].clientY - startY;
      if (dy > 0) panel.style.transform = `translateY(${dy}px)`;
    }, {passive:true});
    panel.addEventListener("touchend", ()=>{
      if (dy > 80) {
        closeModal();
        setTimeout(()=>{ panel.style.transform = ""; }, 320);
      } else {
        panel.style.transform = "";
      }
    });
  })();

  /* 初期描画 */
  filterTable();
});