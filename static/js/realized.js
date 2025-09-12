document.addEventListener("DOMContentLoaded", () => {
  /* ===== 要素 ===== */
  const yearFilter   = document.getElementById("yearFilter");
  const monthFilter  = document.getElementById("monthFilter");
  const table        = document.getElementById("realizedTable");
  const tbody        = table.querySelector("tbody");
  const emptyState   = document.getElementById("emptyState");
  const chips        = [...document.querySelectorAll(".quick-chips .chip")];
  const segBtns      = [...document.querySelectorAll(".seg-btn")];
  const tableWrapper = document.getElementById("tableWrapper");
  const fab          = document.getElementById("scrollTopFab");
  const kpiToggle    = document.getElementById("kpiToggle");
  const topFixed     = document.querySelector(".top-fixed");
  const searchInput  = document.getElementById("searchInput");
  const clearSearch  = document.getElementById("clearSearch");
  const themeToggle  = document.getElementById("themeToggle");
  const densityToggle= document.getElementById("densityToggle");

  /* ===== 高さ実測（iPhone対策） ===== */
  function calcHeights(){
    const top = document.querySelector(".top-fixed");
    const topH = top ? (top.getBoundingClientRect().height + 8) : 0;
    const bottom = document.querySelector(".bottom-tab, .bottom_navbar, #bottomTab, [data-bottom-tab]");
    const bottomH = bottom ? (bottom.getBoundingClientRect().height) : 0;
    document.documentElement.style.setProperty("--top-h", `${topH}px`);
    document.documentElement.style.setProperty("--bottom-h", `${bottomH}px`);
  }
  calcHeights();
  window.addEventListener("resize", calcHeights);
  window.addEventListener("orientationchange", () => setTimeout(calcHeights, 60));

  /* ===== テーブルユーティリティ ===== */
  const numeric = (t)=> {
    const v = parseFloat(String(t||"").replace(/[^\-0-9.]/g,""));
    return isNaN(v) ? 0 : v;
  };
  const fmt = (n)=> Math.round(n).toLocaleString();

  function getAllDataRows(){
    return [...tbody.querySelectorAll("tr")].filter(r => !r.classList.contains("group-row"));
  }

  /* ===== KPI計算 + 勝率リング ===== */
  const sumCount        = document.getElementById("sumCount");
  const winRateEl       = document.getElementById("winRate");
  const netProfitEl     = document.getElementById("netProfit");
  const totalProfitEl   = document.getElementById("totalProfit");
  const totalLossEl     = document.getElementById("totalLoss");
  const avgNetEl        = document.getElementById("avgNet");
  const avgProfitOnlyEl = document.getElementById("avgProfitOnly");
  const avgLossOnlyEl   = document.getElementById("avgLossOnly");
  const winArc          = document.getElementById("winArc");

  function updateSummary(){
    const visible = getAllDataRows().filter(r => r.style.display !== "none");
    const vals = visible.map(r => numeric((r.children[6] && r.children[6].innerText) || "0")); // 7列目=損益額
    const pos = vals.filter(v => v > 0), neg = vals.filter(v => v < 0);

    const count = visible.length;
    const wins = pos.length;
    const net = vals.reduce((a,b)=>a+b,0);
    const posSum = pos.reduce((a,b)=>a+b,0);
    const negSum = neg.reduce((a,b)=>a+b,0);
    const avgNet = count ? net / count : 0;
    const avgPos = pos.length ? posSum / pos.length : 0;
    const avgNeg = neg.length ? negSum / neg.length : 0;

    if (sumCount)  sumCount.textContent  = String(count);
    const winRate = count ? Math.round((wins/count)*100) : 0;
    if (winRateEl) winRateEl.textContent = `${winRate}%`;

    if (netProfitEl){
      netProfitEl.textContent = fmt(net);
      netProfitEl.classList.toggle('profit', net > 0);
      netProfitEl.classList.toggle('loss', net < 0);
    }
    if (totalProfitEl) totalProfitEl.textContent = fmt(posSum);
    if (totalLossEl)   totalLossEl.textContent   = fmt(negSum);
    if (avgNetEl){
      avgNetEl.textContent = fmt(avgNet);
      avgNetEl.classList.toggle('profit', avgNet > 0);
      avgNetEl.classList.toggle('loss', avgNet < 0);
    }
    if (avgProfitOnlyEl) avgProfitOnlyEl.textContent = fmt(avgPos);
    if (avgLossOnlyEl)   avgLossOnlyEl.textContent   = fmt(avgNeg);

    // 勝率リング
    if (winArc){
      const pct = Math.max(0, Math.min(100, winRate));
      winArc.setAttribute("stroke-dasharray", `${pct},100`);
    }
  }

  /* ===== フィルタ（年月 + 検索 + クイック） ===== */
  function filterTable(){
    const year  = yearFilter ? yearFilter.value.trim() : "";
    const month = monthFilter ? monthFilter.value.trim() : "";
    const q = (searchInput?.value || "").trim().toLowerCase();

    getAllDataRows().forEach(row => {
      const date = row.dataset.date || "";
      const [yy, mm] = date.split("-");
      const hay = (row.dataset.name + " " + row.dataset.code + " " + row.dataset.broker + " " + row.dataset.account + " " + row.dataset.type).toLowerCase();
      let show = true;
      if (year  && yy !== year)  show = false;
      if (month && mm !== month) show = false;
      if (q && !hay.includes(q)) show = false;
      row.style.display = show ? "" : "none";
    });

    emptyState.style.display = getAllDataRows().some(r => r.style.display !== "none") ? "none" : "";
    updateSummary();
    updateBars();
  }

  if (yearFilter) yearFilter.addEventListener("change", ()=>{ chips.forEach(c=>c.classList.remove('active')); filterTable(); });
  if (monthFilter) monthFilter.addEventListener("change", ()=>{ chips.forEach(c=>c.classList.remove('active')); filterTable(); });

  chips.forEach(b=>{
    b.addEventListener("click", ()=>{
      const now = new Date();
      const y = now.getFullYear();
      const m = now.getMonth()+1;
      const pad2 = n => n<10 ? "0"+n : ""+n;
      const mm = pad2(m);

      if (b.dataset.range === "this-month"){
        if (yearFilter) yearFilter.value = String(y);
        if (monthFilter) monthFilter.value = mm;
      }else if (b.dataset.range === "last-month"){
        const d = new Date(y, m-2, 1);
        if (yearFilter) yearFilter.value = String(d.getFullYear());
        if (monthFilter) monthFilter.value = pad2(d.getMonth()+1);
      }else if (b.dataset.range === "this-year"){
        if (yearFilter) yearFilter.value = String(y);
        if (monthFilter) monthFilter.value = "";
      }else{
        if (yearFilter) yearFilter.value = "";
        if (monthFilter) monthFilter.value = "";
      }
      chips.forEach(c=>c.classList.remove('active'));
      b.classList.add('active');
      filterTable();
    });
  });

  // 検索
  searchInput?.addEventListener("input", filterTable);
  clearSearch?.addEventListener("click", ()=>{
    if (!searchInput) return;
    searchInput.value = "";
    filterTable();
  });

  /* ===== ヘッダーソート ===== */
  table.querySelectorAll("thead th").forEach((th, idx)=>{
    th.addEventListener("click", ()=>{
      const asc = th.dataset.asc !== "true";
      th.dataset.asc = asc;
      const visible = getAllDataRows().filter(r => r.style.display !== "none");
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
      const hidden = getAllDataRows().filter(r => r.style.display === "none");
      [...visible, ...hidden].forEach(r => tbody.appendChild(r));
    });
  });

  /* ===== 表示トグル（損益額⇄率） ===== */
  function applyToggle(mode){
    const profitCol = 6, rateCol = 7; // 0始まり
    table.querySelectorAll("thead th")[profitCol].classList.toggle("col-hide", mode === "rate");
    table.querySelectorAll("thead th")[rateCol].classList.toggle("col-hide", mode === "amount");
    [...table.querySelectorAll(`tbody td:nth-child(${profitCol+1})`)].forEach(td=>td.classList.toggle("col-hide", mode === "rate"));
    [...table.querySelectorAll(`tbody td:nth-child(${rateCol+1})`)].forEach(td=>td.classList.toggle("col-hide", mode === "amount"));
  }
  segBtns.forEach(b=>{
    b.addEventListener("click", ()=>{
      segBtns.forEach(x=>x.classList.remove("active"));
      b.classList.add("active");
      applyToggle(b.dataset.show);
    });
  });
  applyToggle("amount");

  /* ===== 損益バー ===== */
  function updateBars(){
    const visible = getAllDataRows().filter(r => r.style.display !== "none");
    const pnVals = visible.map(r => {
      const bar = r.querySelector(".profit-cell .bar");
      if (!bar) return 0;
      return Math.abs(parseFloat(bar.dataset.pn || "0"));
    });
    const max = Math.max(5000, ...pnVals);
    visible.forEach(r=>{
      const bar = r.querySelector(".profit-cell .bar");
      if (!bar) return;
      const val = Math.abs(parseFloat(bar.dataset.pn || "0"));
      const w = Math.min(100, Math.round((val / max) * 100));
      if (!bar.firstElementChild){
        const fill = document.createElement("span");
        fill.style.position = "absolute";
        fill.style.left = "0"; fill.style.top = "0"; fill.style.bottom = "0";
        bar.appendChild(fill);
      }
      const fill = bar.firstElementChild;
      fill.style.width = Math.max(8, Math.round(64 * w / 100)) + "px";
      fill.style.borderRadius = "999px";
      fill.style.background = bar.closest(".loss")
        ? "linear-gradient(90deg, rgba(255,80,100,.95), rgba(255,120,120,.85))"
        : "linear-gradient(90deg, rgba(0,220,130,.95), rgba(0,255,210,.85))";
      bar.style.boxShadow = "inset 0 0 0 1px rgba(255,255,255,.20)";
      bar.style.overflow = "hidden";
      bar.style.borderRadius = "999px";
      bar.style.width = "64px";
    });
  }

  /* ===== モーダル ===== */
  const modal    = document.getElementById("stockModal");
  const closeBtn = modal ? modal.querySelector(".close") : null;
  const modalTitle      = document.getElementById("modalTitle");
  const modalPurchase   = document.getElementById("modalPurchase");
  const modalQuantity   = document.getElementById("modalQuantity");
  const modalBroker     = document.getElementById("modalBroker");
  const modalAccount    = document.getElementById("modalAccount");
  const modalSell       = document.getElementById("modalSell");
  const modalProfit     = document.getElementById("modalProfit");
  const modalFee        = document.getElementById("modalFee");
  const modalSellAmount = document.getElementById("modalSellAmount");
  const modalBuyAmount  = document.getElementById("modalBuyAmount");

  const num = (t)=> {
    if (t == null) return 0;
    const s = String(t).replace(/[^\-0-9.]/g,'');
    const v = parseFloat(s);
    return isNaN(v) ? 0 : v;
  };
  const yen = (n)=> Math.round(n).toLocaleString('ja-JP');

  function openModalForRow(row){
    if (!modal) return;
    const name  = row.dataset.name || "";
    const code  = row.dataset.code || "";
    const title = code ? `${name}（${code}）` : name;

    const q     = num(row.dataset.quantity);
    const buy   = num(row.dataset.purchase);
    const sell  = num(row.dataset.sell);
    const fee   = num(row.dataset.fee);
    const prof  = num(row.dataset.profit);

    const buyAmt  = q ? buy * q : 0;
    const sellAmt = q ? sell * q : 0;

    if (modalTitle)     modalTitle.textContent     = title;
    if (modalPurchase)  modalPurchase.textContent  = buy ? yen(buy) : '-';
    if (modalQuantity)  modalQuantity.textContent  = q   ? yen(q)   : '-';
    if (modalBroker)    modalBroker.textContent    = row.dataset.broker  || '';
    if (modalAccount)   modalAccount.textContent   = row.dataset.account || '';
    if (modalSell)      modalSell.textContent      = sell ? yen(sell) : '-';
    if (modalProfit){
      modalProfit.textContent = prof ? (prof>0? '+'+yen(prof) : yen(prof)) : '0';
      modalProfit.classList.remove('profit','loss');
      if (prof>0) modalProfit.classList.add('profit');
      if (prof<0) modalProfit.classList.add('loss');
    }
    if (modalFee)        modalFee.textContent        = fee ? yen(fee) : '0';
    if (modalSellAmount) modalSellAmount.textContent = sellAmt ? yen(sellAmt) : '-';
    if (modalBuyAmount)  modalBuyAmount.textContent  = buyAmt  ? yen(buyAmt)  : '-';

    modal.classList.add("show");
  }
  function closeModal(){ if (!modal) return; modal.classList.remove("show"); }
  if (closeBtn) closeBtn.addEventListener("click", closeModal);
  window.addEventListener("click", (e)=>{ if (modal && e.target === modal) closeModal(); });

  function attachRowHandlers(){
    const TAP_MAX_MOVE = 10, TAP_MAX_TIME = 500;
    getAllDataRows().forEach(row=>{
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
      }, {passive:false});
      row.addEventListener("click", ()=>{ if (row.style.display!=="none") openModalForRow(row); });
    });
  }

  /* ===== FAB（先頭へ） ===== */
  function onScroll(){
    const show = tableWrapper.scrollTop > 200;
    fab.classList.toggle("show", show);
  }
  tableWrapper.addEventListener("scroll", onScroll, {passive:true});
  fab.addEventListener("click", ()=> tableWrapper.scrollTo({top:0, behavior:"smooth"}));

  /* ===== KPI 折りたたみ / 自動スリム化 ===== */
  let userPinned = localStorage.getItem("realized.kpiPinned") === "1";
  const PREF_KEY = "realized.kpiCollapsed";

  function setCollapsed(collapsed){
    if (!topFixed) return;
    topFixed.classList.toggle("collapsed", collapsed);
    if (kpiToggle){
      kpiToggle.setAttribute("aria-expanded", (!collapsed).toString());
      kpiToggle.textContent = collapsed ? "KPIを表示" : "KPIを隠す";
    }
    calcHeights();
  }

  const saved = localStorage.getItem(PREF_KEY);
  if (saved === null){
    setCollapsed(true);
    localStorage.setItem(PREF_KEY, "1");
  }else{
    setCollapsed(saved === "1");
  }

  if (kpiToggle){
    kpiToggle.addEventListener("click", ()=>{
      const next = !topFixed.classList.contains("collapsed");
      setCollapsed(next);
      localStorage.setItem(PREF_KEY, next ? "1" : "0");
      userPinned = (next === false);
      localStorage.setItem("realized.kpiPinned", userPinned ? "1" : "0");
    });
  }

  function autoSlimByScroll(){
    if (!topFixed) return;
    if (userPinned) return;
    const y = tableWrapper.scrollTop;
    const collapsed = y > 24;
    topFixed.classList.toggle("slim", y > 4);
    if (collapsed !== topFixed.classList.contains("collapsed")){
      setCollapsed(collapsed);
      localStorage.setItem(PREF_KEY, collapsed ? "1" : "0");
    }
  }
  tableWrapper.addEventListener("scroll", autoSlimByScroll, {passive:true});

  /* ===== テーマ・密度切替 ===== */
  themeToggle?.addEventListener("click", ()=>{
    const root = document.querySelector(".realized-page");
    root.classList.toggle("theme-dark");
    root.classList.toggle("theme-light");
  });
  densityToggle?.addEventListener("click", ()=>{
    // 行高の密度切替
    const rows = table.querySelectorAll("tbody tr");
    rows.forEach(r => r.style.height = (r.style.height === "44px" ? "50px" : "44px"));
  });

  /* ===== 初期描画 ===== */
  attachRowHandlers();
  filterTable();      // KPI & 可視化
  updateBars();
  calcHeights();
});