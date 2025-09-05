document.addEventListener("DOMContentLoaded", function() {
  // 年月フィルタ
  const yearFilter  = document.getElementById("yearFilter");
  const monthFilter = document.getElementById("monthFilter");

  // テーブルと行
  const table       = document.getElementById("realizedTable");
  const tbody       = table.querySelector("tbody");
  const allRows     = [...tbody.querySelectorAll("tr")];
  const dataRows    = allRows.filter(r => !r.classList.contains('group-row'));

  // スクロール領域
  const tableWrapper = document.getElementById("tableWrapper");
  const topFixed     = document.querySelector(".top-fixed");

  // 空状態
  const emptyState  = document.getElementById("emptyState");

  // KPI
  const sumCount  = document.getElementById("sumCount");
  const winRateEl = document.getElementById("winRate");
  const sumProfit = document.getElementById("sumProfit");
  const avgProfit = document.getElementById("avgProfit");

  /* ====== ページ全体はスクロールさせず、表ラッパーだけ縦スクロール ====== */
  // 念のため、body/docのスクロールを抑える（このページの体験を安定化）
  const prevHtmlOverflow = document.documentElement.style.overflow;
  const prevBodyOverflow = document.body.style.overflow;
  document.documentElement.style.overflow = "hidden";
  document.body.style.overflow = "hidden";

  function measureBottomTabHeight(){
    const el = document.querySelector(".bottom-tab, #bottom-tab");
    return el ? el.offsetHeight : 0;
  }
  function setScrollableHeight(){
    const vh     = window.innerHeight;
    const topH   = topFixed ? topFixed.offsetHeight : 0;
    const bottom = measureBottomTabHeight();
    const padding = 10; // 余白
    const maxH  = Math.max(140, vh - topH - bottom - padding);
    tableWrapper.style.maxHeight = maxH + "px";
    tableWrapper.style.overflow = "auto"; // 縦横スクロール可
  }
  setScrollableHeight();
  window.addEventListener("resize", setScrollableHeight);
  window.addEventListener("orientationchange", setScrollableHeight);

  /* ====== 数値ユーティリティ ====== */
  function numeric(text){
    const t = (text || "").toString().replace(/[^\-0-9.]/g, "");
    const v = parseFloat(t);
    return isNaN(v) ? 0 : v;
  }
  function pad2(n){ return n < 10 ? "0"+n : ""+n; }

  /* ====== フィルタ ====== */
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

  function toggleEmpty(){
    const any = dataRows.some(r => r.style.display !== "none");
    emptyState.style.display = any ? "none" : "";
  }

  function updateSummary(){
    const vis = dataRows.filter(r => r.style.display !== "none");
    const profits = vis.map(r => numeric(r.children[4]?.textContent));
    const wins = vis.filter(r => numeric(r.children[4]?.textContent) > 0).length;

    const sum = profits.reduce((a,b)=>a+b,0);
    const avg = profits.length ? sum / profits.length : 0;
    const winRate = vis.length ? Math.round((wins/vis.length)*100) : 0;

    sumCount.textContent  = String(vis.length);
    winRateEl.textContent = `${winRate}%`;
    sumProfit.textContent = Math.round(sum).toLocaleString();
    avgProfit.textContent = Math.round(avg).toLocaleString();
  }

  yearFilter.addEventListener("change", filterTable);
  monthFilter.addEventListener("change", filterTable);

  /* ====== クイックフィルタ ====== */
  document.querySelectorAll(".quick-chips button").forEach(b=>{
    b.addEventListener("click", ()=>{
      const now = new Date();
      const y = now.getFullYear();
      const m = now.getMonth()+1;
      const mm = pad2(m);
      const key = b.dataset.range;

      if (key === "this-month"){
        yearFilter.value = String(y);
        monthFilter.value = mm;
      } else if (key === "last-month"){
        const d = new Date(y, m-2, 1);
        yearFilter.value = String(d.getFullYear());
        monthFilter.value = pad2(d.getMonth()+1);
      } else if (key === "this-year"){
        yearFilter.value = String(y);
        monthFilter.value = "";
      } else {
        yearFilter.value = "";
        monthFilter.value = "";
      }
      filterTable();
    });
  });

  /* ====== ソート（ヘッダークリック） ====== */
  table.querySelectorAll("thead th").forEach((th, idx)=>{
    th.addEventListener("click", ()=>{
      const asc = th.dataset.asc !== "true";
      th.dataset.asc = asc;

      const visible = dataRows.filter(r => r.style.display !== "none");
      const isDate = idx === 0;

      visible.sort((a,b)=>{
        let va, vb;
        if (isDate){
          va = new Date(a.children[idx].textContent.trim());
          vb = new Date(b.children[idx].textContent.trim());
        }else{
          va = numeric(a.children[idx].textContent);
          vb = numeric(b.children[idx].textContent);
        }
        return asc ? (va>vb?1:-1) : (va<vb?1:-1);
      });

      const hidden = dataRows.filter(r => r.style.display === "none");
      [...visible, ...hidden].forEach(r => tbody.appendChild(r));
    });
  });

  /* ====== モーダル（中央表示・誤タップ防止） ====== */
  const modal    = document.getElementById("stockModal");
  const panel    = modal.querySelector(".modal-content");
  const closeBtn = modal.querySelector(".close");

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
    // 背景（ページ）スクロールはもともと抑制済み。表スクロールもモーダル上では起きない。
  }

  const TAP_MAX_MOVE = 10;   // px
  const TAP_MAX_TIME = 500;  // ms
  dataRows.forEach(row=>{
    let sx=0, sy=0, st=0, moved=false;

    row.addEventListener("touchstart", e=>{
      const t = e.touches[0];
      sx = t.clientX; sy = t.clientY; st = Date.now(); moved=false;
    }, {passive:true});

    row.addEventListener("touchmove", e=>{
      const t = e.touches[0];
      const dx = Math.abs(t.clientX - sx);
      const dy = Math.abs(t.clientY - sy);
      if (dx > TAP_MAX_MOVE || dy > TAP_MAX_MOVE) moved = true;
    }, {passive:true});

    row.addEventListener("touchend", e=>{
      const dt = Date.now() - st;
      if (!moved && dt <= TAP_MAX_TIME && row.style.display !== "none"){
        e.preventDefault();
        openModalForRow(row);
      }
    });

    row.addEventListener("click", ()=>{
      if (row.style.display !== "none") openModalForRow(row);
    });
  });

  function closeModal(){
    modal.classList.remove("show");
    setTimeout(()=>{ modal.style.display = "none"; }, 180);
  }
  closeBtn.addEventListener("click", closeModal);
  window.addEventListener("click", (e)=>{ if (e.target === modal) closeModal(); });

  /* ====== 初期描画 ====== */
  filterTable();
  // レイアウト安定後に高さ再計算
  setTimeout(setScrollableHeight, 120);

  /* ====== ページ離脱時：スクロール制御を元に戻す（他ページ影響防止） ====== */
  window.addEventListener("beforeunload", ()=>{
    document.documentElement.style.overflow = prevHtmlOverflow;
    document.body.style.overflow = prevBodyOverflow;
  });
});