document.addEventListener("DOMContentLoaded", () => {
  // 要素
  const yearFilter  = document.getElementById("yearFilter");
  const monthFilter = document.getElementById("monthFilter");
  const table       = document.getElementById("realizedTable");
  const tbody       = table.querySelector("tbody");
  const allRows     = [...tbody.querySelectorAll("tr")];
  const dataRows    = allRows.filter(r => !r.classList.contains('group-row'));
  const emptyState  = document.getElementById("emptyState");
  const chips       = [...document.querySelectorAll(".quick-chips .chip")];

  // KPI
  const sumCount        = document.getElementById("sumCount");
  const winRateEl       = document.getElementById("winRate");
  const netProfitEl     = document.getElementById("netProfit");
  const totalProfitEl   = document.getElementById("totalProfit");
  const totalLossEl     = document.getElementById("totalLoss");
  const avgNetEl        = document.getElementById("avgNet");
  const avgProfitOnlyEl = document.getElementById("avgProfitOnly");
  const avgLossOnlyEl   = document.getElementById("avgLossOnly");

  /* ===== 数値ユーティリティ ===== */
  const numeric = (t)=> {
    const v = parseFloat(String(t||"").replace(/[^\-0-9.]/g,""));
    return isNaN(v) ? 0 : v;
  };
  const pad2 = (n)=> n<10 ? "0"+n : ""+n;
  const fmt  = (n)=> Math.round(n).toLocaleString();

  /* ===== KPI更新 ===== */
  function updateSummary(){
    const vis = dataRows.filter(r => r.style.display !== "none");
    const vals = vis.map(r => numeric(r.children[4]?.textContent));
    const pos  = vals.filter(v => v > 0);
    const neg  = vals.filter(v => v < 0);

    const count = vis.length;
    const wins  = pos.length;
    const net   = vals.reduce((a,b)=>a+b,0);
    const posSum= pos.reduce((a,b)=>a+b,0);
    const negSum= neg.reduce((a,b)=>a+b,0);
    const avgNet= count ? net / count : 0;
    const avgPos= pos.length ? posSum / pos.length : 0;
    const avgNeg= neg.length ? negSum / neg.length : 0;

    sumCount.textContent  = String(count);
    winRateEl.textContent = count ? `${Math.round((wins/count)*100)}%` : "0%";

    netProfitEl.textContent = fmt(net);
    netProfitEl.classList.toggle('profit', net > 0);
    netProfitEl.classList.toggle('loss', net < 0);

    totalProfitEl.textContent = fmt(posSum);
    totalLossEl.textContent   = fmt(negSum);

    avgNetEl.textContent        = fmt(avgNet);
    avgNetEl.classList.toggle('profit', avgNet > 0);
    avgNetEl.classList.toggle('loss', avgNet < 0);
    avgProfitOnlyEl.textContent = fmt(avgPos);
    avgLossOnlyEl.textContent   = fmt(avgNeg);
  }

  /* ===== 表フィルタ ===== */
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

    emptyState.style.display = dataRows.some(r => r.style.display !== "none") ? "none" : "";
    updateSummary();
  }

  yearFilter.addEventListener("change", ()=>{
    chips.forEach(c=>c.classList.remove('active'));
    filterTable();
  });
  monthFilter.addEventListener("change", ()=>{
    chips.forEach(c=>c.classList.remove('active'));
    filterTable();
  });

  /* ===== クイックフィルタ（アクティブ表示） ===== */
  chips.forEach(b=>{
    b.addEventListener("click", ()=>{
      const now = new Date();
      const y = now.getFullYear();
      const m = now.getMonth()+1;
      const mm = pad2(m);
      const key = b.dataset.range;

      if (key === "this-month"){
        yearFilter.value = String(y); monthFilter.value = mm;
      } else if (key === "last-month"){
        const d = new Date(y, m-2, 1);
        yearFilter.value = String(d.getFullYear());
        monthFilter.value = pad2(d.getMonth()+1);
      } else if (key === "this-year"){
        yearFilter.value = String(y); monthFilter.value = "";
      } else {
        yearFilter.value = ""; monthFilter.value = "";
      }
      chips.forEach(c=>c.classList.remove('active'));
      b.classList.add('active');
      filterTable();
    });
  });

  /* ===== ソート（ヘッダークリック） ===== */
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
          const na = numeric(a.children[idx].textContent);
          const nb = numeric(b.children[idx].textContent);
          va = isNaN(na) ? a.children[idx].textContent : na;
          vb = isNaN(nb) ? b.children[idx].textContent : nb;
        }
        return asc ? (va>vb?1:-1) : (va<vb?1:-1);
      });

      const hidden = dataRows.filter(r => r.style.display === "none");
      [...visible, ...hidden].forEach(r => tbody.appendChild(r));
    });
  });

  /* ===== モーダル ===== */
  const modal    = document.getElementById("stockModal");
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
  }

  const TAP_MAX_MOVE = 10, TAP_MAX_TIME = 500;
  dataRows.forEach(row=>{
    let sx=0, sy=0, st=0, moved=false;
    row.addEventListener("touchstart", e=>{
      const t = e.touches[0]; sx=t.clientX; sy=t.clientY; st=Date.now(); moved=false;
    }, {passive:true});
    row.addEventListener("touchmove", e=>{
      const t = e.touches[0];
      if (Math.abs(t.clientX-sx)>TAP_MAX_MOVE || Math.abs(t.clientY-sy)>TAP_MAX_MOVE) moved=true;
    }, {passive:true});
    row.addEventListener("touchend", e=>{
      const dt = Date.now()-st;
      if (!moved && dt<=TAP_MAX_TIME && row.style.display!=="none"){ e.preventDefault(); openModalForRow(row); }
    });
    row.addEventListener("click", ()=>{ if (row.style.display!=="none") openModalForRow(row); });
  });

  function closeModal(){ modal.classList.remove("show"); }
  closeBtn.addEventListener("click", closeModal);
  window.addEventListener("click", (e)=>{ if (e.target === modal) closeModal(); });

  /* 初期描画 */
  filterTable();
});