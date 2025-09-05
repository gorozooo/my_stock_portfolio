document.addEventListener("DOMContentLoaded", function() {
  const yearFilter  = document.getElementById("yearFilter");
  const monthFilter = document.getElementById("monthFilter");
  const table       = document.getElementById("realizedTable");
  const tbody       = table.querySelector("tbody");
  const rows        = [...tbody.querySelectorAll("tr")].filter(r => !r.classList.contains('group-row'));

  const emptyState  = document.getElementById("emptyState");

  // サマリー要素
  const sumCount  = document.getElementById("sumCount");
  const sumProfit = document.getElementById("sumProfit");
  const avgProfit = document.getElementById("avgProfit");

  /* ========== フィルタ ========== */
  function filterTable() {
    const year  = yearFilter.value;
    const month = monthFilter.value;

    rows.forEach(row => {
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
    const anyVisible = rows.some(r => r.style.display !== "none");
    emptyState.style.display = anyVisible ? "none" : "";
  }

  function numeric(valueText) {
    // "+50,000", "-5%", " 12 " -> 数値化
    const t = (valueText || "").toString().replace(/[^\-0-9.]/g, "");
    const v = parseFloat(t);
    return isNaN(v) ? 0 : v;
  }

  function updateSummary() {
    const vis = rows.filter(r => r.style.display !== "none");
    const profits = vis.map(r => {
      const cell = r.children[4]; // 5列目=損益額
      return numeric(cell?.textContent);
    });
    const sum = profits.reduce((a,b)=>a+b,0);
    const avg = profits.length ? sum / profits.length : 0;

    sumCount.textContent  = vis.length.toString();
    sumProfit.textContent = Math.round(sum).toLocaleString();
    avgProfit.textContent = Math.round(avg).toLocaleString();
  }

  yearFilter.addEventListener("change", filterTable);
  monthFilter.addEventListener("change", filterTable);

  /* ========== クイックフィルタ ========== */
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
      const m = now.getMonth()+1; // 1-12
      const mm = pad2(m);
      const key = b.dataset.range;

      if (key === "this-month") setYearMonth(String(y), mm);
      else if (key === "last-month") {
        const d = new Date(y, m-2, 1); // 先月
        setYearMonth(String(d.getFullYear()), pad2(d.getMonth()+1));
      } else if (key === "this-year") setYearMonth(String(y), "");
      else if (key === "all") setYearMonth("", "");
    });
  });

  /* ========== ソート（ヘッダークリック） ========== */
  table.querySelectorAll("thead th").forEach((th, idx)=>{
    th.addEventListener("click", ()=>{
      const asc = th.dataset.asc !== "true"; // トグル
      th.dataset.asc = asc;

      const dataRows = rows.filter(r => r.style.display !== "none"); // 表示中をソート
      const isDateCol = idx === 0;

      dataRows.sort((a,b)=>{
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

      // 並べ直し（非表示行は末尾に維持）
      const hiddenRows = rows.filter(r => r.style.display === "none");
      [...dataRows, ...hiddenRows].forEach(r => tbody.appendChild(r));
    });
  });

  /* ========== 行タップでモーダル ========== */
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

  function openModalForRow(row, e){
    e?.preventDefault?.();
    modalName.textContent     = row.dataset.name     || "";
    modalPrice.textContent    = row.dataset.price    || "";
    modalSector.textContent   = row.dataset.sector   || "";
    modalPurchase.textContent = row.dataset.purchase || "";
    modalQuantity.textContent = row.dataset.quantity || "";
    modalProfit.textContent   = row.dataset.profit   || "";
    modalRate.textContent     = row.dataset.rate     || "";

    modal.classList.add("show");
    modal.style.display = "flex";
    body.style.overflow = "hidden"; // 背景スクロール停止
  }

  rows.forEach(row => {
    const handler = (e)=> openModalForRow(row, e);
    row.addEventListener("click", handler);
    row.addEventListener("touchstart", handler, {passive:true});
  });

  function closeModal(){
    modal.classList.remove("show");
    body.style.overflow = ""; // 背景スクロール復活
    setTimeout(()=>{ modal.style.display = "none"; }, 300);
  }

  closeBtn.addEventListener("click", closeModal);
  window.addEventListener("click", (e)=>{ if (e.target === modal) closeModal(); });

  /* ========== スワイプで閉じる（下方向） ========== */
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
      if (dy > 80) { // しきい値
        closeModal();
        setTimeout(()=>{ panel.style.transform = ""; }, 320);
      } else {
        panel.style.transform = ""; // 戻す
      }
    });
  })();

  /* 初期描画 */
  filterTable();
});