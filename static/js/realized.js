document.addEventListener("DOMContentLoaded", () => {
  /* ===== Grab elements ===== */
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
  const controlsBox  = document.querySelector(".rp-controls");
  const searchInput  = document.getElementById("searchInput");
  const clearSearch  = document.getElementById("clearSearch");
  const themeToggle  = document.getElementById("themeToggle");
  const densityToggle= document.getElementById("densityToggle");

  /* ===== Utils ===== */
  const numeric = (t)=> {
    const v = parseFloat(String(t||"").replace(/[^\-0-9.]/g,""));
    return isNaN(v) ? 0 : v;
  };
  const fmt = (n)=> Math.round(n).toLocaleString();
  const pad2 = n => n<10 ? "0"+n : ""+n;

  const dataRows = ()=> [...tbody.querySelectorAll("tr")].filter(r => !r.classList.contains("group-row"));

  function calcHeights(){
    const top = document.querySelector(".rp-controls");
    const topH = top ? (top.getBoundingClientRect().height + 8) : 0;
    const bottom = document.querySelector(".bottom-tab, .bottom_navbar, #bottomTab, [data-bottom-tab]");
    const bottomH = bottom ? (bottom.getBoundingClientRect().height) : 0;
    document.documentElement.style.setProperty("--top-h", `${topH}px`);
    document.documentElement.style.setProperty("--bottom-h", `${bottomH}px`);
  }
  calcHeights();
  window.addEventListener("resize", calcHeights);
  window.addEventListener("orientationchange", () => setTimeout(calcHeights, 60));

  /* ===== KPI ===== */
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
    const visible = dataRows().filter(r => r.style.display !== "none");
    const profitCells = visible.map(r => (r.children[6] && r.children[6].innerText) || "0");
    const vals = profitCells.map(numeric);
    const pos = vals.filter(v => v > 0), neg = vals.filter(v => v < 0);

    const count = visible.length;
    const wins = pos.length;
    const net = vals.reduce((a,b)=>a+b,0);
    const posSum = pos.reduce((a,b)=>a+b,0);
    const negSum = neg.reduce((a,b)=>a+b,0);
    const avgNet = count ? net / count : 0;
    const avgPos = pos.length ? posSum / pos.length : 0;
    const avgNeg = neg.length ? negSum / neg.length : 0;

    sumCount.textContent = String(count);
    const winRate = count ? Math.round((wins/count)*100) : 0;
    winRateEl.textContent = `${winRate}%`;
    netProfitEl.textContent = fmt(net);
    netProfitEl.classList.toggle('profit', net > 0);
    netProfitEl.classList.toggle('loss', net < 0);
    totalProfitEl.textContent = fmt(posSum);
    totalLossEl.textContent = fmt(negSum);
    avgNetEl.textContent = fmt(avgNet);
    avgNetEl.classList.toggle('profit', avgNet > 0);
    avgNetEl.classList.toggle('loss', avgNet < 0);
    avgProfitOnlyEl.textContent = fmt(avgPos);
    avgLossOnlyEl.textContent   = fmt(avgNeg);

    if (winArc){
      const pct = Math.max(0, Math.min(100, winRate));
      winArc.setAttribute("stroke-dasharray", `${pct},100`);
    }
  }

  /* ===== Filters (year/month/search/chips) ===== */
  function filterTable(){
    const year  = yearFilter?.value.trim() ?? "";
    const month = monthFilter?.value.trim() ?? "";
    const q = (searchInput?.value || "").trim().toLowerCase();

    dataRows().forEach(row => {
      const date = row.dataset.date || "";
      const [yy, mm] = date.split("-");
      const hay = (row.dataset.name + " " + row.dataset.code + " " + row.dataset.broker + " " + row.dataset.account + " " + row.dataset.type).toLowerCase();
      let show = true;
      if (year  && yy !== year)  show = false;
      if (month && mm !== month) show = false;
      if (q && !hay.includes(q)) show = false;
      row.style.display = show ? "" : "none";
    });

    emptyState.style.display = dataRows().some(r => r.style.display !== "none") ? "none" : "";
    updateSummary();
    updateBars();
    buildTiles();   // 反映
    buildInsights();// 反映
  }

  yearFilter?.addEventListener("change", ()=>{ chips.forEach(c=>c.classList.remove('active')); filterTable(); });
  monthFilter?.addEventListener("change", ()=>{ chips.forEach(c=>c.classList.remove('active')); filterTable(); });

  chips.forEach(b=>{
    b.addEventListener("click", ()=>{
      const now = new Date();
      const y = now.getFullYear();
      const m = now.getMonth()+1;
      const mm = pad2(m);

      if (b.dataset.range === "this-month"){
        yearFilter.value = String(y); monthFilter.value = mm;
      }else if (b.dataset.range === "last-month"){
        const d = new Date(y, m-2, 1);
        yearFilter.value = String(d.getFullYear());
        monthFilter.value = pad2(d.getMonth()+1);
      }else if (b.dataset.range === "this-year"){
        yearFilter.value = String(y); monthFilter.value = "";
      }else{
        yearFilter.value = ""; monthFilter.value = "";
      }
      chips.forEach(c=>c.classList.remove('active'));
      b.classList.add('active');
      filterTable();
    });
  });

  searchInput?.addEventListener("input", filterTable);
  clearSearch?.addEventListener("click", ()=>{ searchInput.value=""; filterTable(); });

  /* ===== Sort ===== */
  table.querySelectorAll("thead th").forEach((th, idx)=>{
    th.addEventListener("click", ()=>{
      const asc = th.dataset.asc !== "true";
      th.dataset.asc = asc;
      const visible = dataRows().filter(r => r.style.display !== "none");
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
      const hidden = dataRows().filter(r => r.style.display === "none");
      [...visible, ...hidden].forEach(r => tbody.appendChild(r));
      updateBars();
      buildTiles();
      buildInsights();
    });
  });

  /* ===== Toggle amount/rate columns ===== */
  function applyToggle(mode){
    const profitCol = 6, rateCol = 7;
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

  /* ===== Bars ===== */
  function updateBars(){
    const visible = dataRows().filter(r => r.style.display !== "none");
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
    });
  }

  /* ===== Modal ===== */
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

    modalTitle.textContent     = title;
    modalPurchase.textContent  = buy ? yen(buy) : '-';
    modalQuantity.textContent  = q   ? yen(q)   : '-';
    modalBroker.textContent    = row.dataset.broker  || '';
    modalAccount.textContent   = row.dataset.account || '';
    modalSell.textContent      = sell ? yen(sell) : '-';
    modalProfit.textContent    = prof ? (prof>0? '+'+yen(prof) : yen(prof)) : '0';
    modalProfit.classList.remove('profit','loss');
    if (prof>0) modalProfit.classList.add('profit');
    if (prof<0) modalProfit.classList.add('loss');
    modalFee.textContent        = fee ? yen(fee) : '0';
    modalSellAmount.textContent = sellAmt ? yen(sellAmt) : '-';
    modalBuyAmount.textContent  = buyAmt  ? yen(buyAmt)  : '-';

    modal.classList.add("show");
  }
  function closeModal(){ modal?.classList.remove("show"); }
  closeBtn?.addEventListener("click", closeModal);
  window.addEventListener("click", (e)=>{ if (modal && e.target === modal) closeModal(); });

  function attachRowHandlers(){
    const TAP_MAX_MOVE = 10, TAP_MAX_TIME = 500;
    dataRows().forEach(row=>{
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

  /* ===== FAB scroll top ===== */
  function onScroll(){
    const show = tableWrapper.scrollTop > 200;
    fab.classList.toggle("show", show);
  }
  tableWrapper.addEventListener("scroll", onScroll, {passive:true});
  fab.addEventListener("click", ()=> tableWrapper.scrollTo({top:0, behavior:"smooth"}));

  /* ===== KPI collapse & auto slim ===== */
  const PREF_COLLAPSE = "rp.kpiCollapsed";
  function setCollapsed(collapsed){
    controlsBox.classList.toggle("collapsed", collapsed);
    kpiToggle.setAttribute("aria-expanded", (!collapsed).toString());
    kpiToggle.textContent = collapsed ? "KPI" : "KPI▲";
    calcHeights();
  }
  const saved = localStorage.getItem(PREF_COLLAPSE);
  if (saved === null){ setCollapsed(true); localStorage.setItem(PREF_COLLAPSE, "1"); }
  else{ setCollapsed(saved === "1"); }
  kpiToggle.addEventListener("click", ()=>{
    const next = !controlsBox.classList.contains("collapsed");
    setCollapsed(next);
    localStorage.setItem(PREF_COLLAPSE, next ? "1" : "0");
  });

  /* ===== Theme & density ===== */
  themeToggle?.addEventListener("click", ()=>{
    const root = document.querySelector(".rp-page");
    root.classList.toggle("theme-dark");
    root.classList.toggle("theme-light");
  });
  densityToggle?.addEventListener("click", ()=>{
    const rows = table.querySelectorAll("tbody tr");
    rows.forEach(r => r.style.height = (r.style.height === "44px" ? "50px" : "44px"));
  });

  /* ===== Tiles View ===== */
  const tilesGrid = document.getElementById("tilesGrid");
  function buildTiles(){
    if (!tilesGrid) return;
    tilesGrid.innerHTML = "";
    const visible = dataRows().filter(r => r.style.display !== "none");
    visible.slice(0, 200).forEach(row=>{
      const name=row.dataset.name||"", code=row.dataset.code||"", broker=row.dataset.broker||"", type=row.dataset.type||"";
      const qty=row.dataset.quantity||"-";
      const profit=row.children[6]?.innerText || "0";
      const rate=row.children[7]?.innerText || "0%";
      const tile = document.createElement("div");
      tile.className="tile";
      tile.innerHTML=`
        <div class="t-head">
          <div class="t-name" title="${name}">${name}</div>
          <div class="t-code">${code}</div>
        </div>
        <div class="t-body">
          <div><span class="badge">区分</span> ${type}</div>
          <div><span class="badge">株数</span> ${qty}</div>
          <div class="${numeric(profit)>=0?'profit':'loss'}"><span class="badge">損益</span> ${profit}</div>
          <div class="${rate.trim().startsWith('-')?'loss':'profit'}"><span class="badge">率</span> ${rate}</div>
          <div><span class="badge">証券</span> ${broker}</div>
          <div><span class="badge">日付</span> ${row.dataset.date}</div>
        </div>`;
      tile.addEventListener("click", ()=> openModalForRow(row));
      tilesGrid.appendChild(tile);
    });
  }

  /* ===== Insights View ===== */
  const monthlyChart = document.getElementById("monthlyChart");
  const topGainersEl = document.getElementById("topGainers");
  const topLosersEl  = document.getElementById("topLosers");
  const byBrokerEl   = document.getElementById("byBroker");

  function buildInsights(){
    // Monthly aggregation (YYYY-MM)
    const visible = dataRows().filter(r => r.style.display !== "none");
    const mapMonth = new Map();
    const mapName  = new Map();
    const mapBroker= new Map();

    visible.forEach(r=>{
      const ym = (r.dataset.date||"").slice(0,7);
      const pn = numeric(r.children[6]?.innerText || "0");
      const name = r.dataset.name||"";
      const broker = r.dataset.broker||"";
      mapMonth.set(ym, (mapMonth.get(ym)||0)+pn);
      mapName.set(name,  (mapName.get(name)||0)+pn);
      mapBroker.set(broker,(mapBroker.get(broker)||0)+pn);
    });

    // Chart
    if (monthlyChart){
      const ctx = monthlyChart.getContext("2d");
      ctx.clearRect(0,0,monthlyChart.width, monthlyChart.height);
      const W = monthlyChart.width, H = monthlyChart.height, P=30;
      const months = [...mapMonth.keys()].sort();
      const values = months.map(m=>mapMonth.get(m));
      const maxAbs = Math.max(1000, ...values.map(v=>Math.abs(v)));
      // axes
      ctx.strokeStyle = "rgba(255,255,255,.25)";
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(P, H-P); ctx.lineTo(W-P, H-P); ctx.moveTo(P, P); ctx.lineTo(P, H-P); ctx.stroke();
      // zero line
      const zeroY = H/2;
      ctx.strokeStyle = "rgba(255,255,255,.18)"; ctx.beginPath(); ctx.moveTo(P, zeroY); ctx.lineTo(W-P, zeroY); ctx.stroke();
      // bars
      const n = Math.max(1, months.length);
      const gap = 6;
      const barW = Math.max(8, ((W-P*2) - gap*(n-1)) / n);
      months.forEach((m, i)=>{
        const v = mapMonth.get(m);
        const x = P + i*(barW+gap);
        const scale = (H/2 - P)/maxAbs;
        const h = Math.round(Math.abs(v) * scale);
        const y = v>=0 ? (zeroY - h) : zeroY;
        ctx.fillStyle = v>=0 ? "rgba(0,220,130,.85)" : "rgba(255,80,100,.85)";
        ctx.fillRect(x, y, barW, h);
        // label
        ctx.fillStyle = "rgba(255,255,255,.75)";
        ctx.font = "12px system-ui";
        ctx.textAlign = "center";
        ctx.fillText(m, x+barW/2, H-10);
      });
    }

    // Top gainers/losers
    const arr = [...mapName.entries()];
    arr.sort((a,b)=>b[1]-a[1]);
    const topG = arr.slice(0,5);
    const topL = arr.slice(-5).reverse();

    if (topGainersEl){
      topGainersEl.innerHTML = "";
      topG.forEach(([name, v])=>{
        const li = document.createElement("li");
        li.innerHTML = `<strong class="profit">+${fmt(v)}</strong> — ${name}`;
        topGainersEl.appendChild(li);
      });
    }
    if (topLosersEl){
      topLosersEl.innerHTML = "";
      topL.forEach(([name, v])=>{
        const li = document.createElement("li");
        li.innerHTML = `<strong class="loss">${fmt(v)}</strong> — ${name}`;
        topLosersEl.appendChild(li);
      });
    }

    // by broker
    if (byBrokerEl){
      byBrokerEl.innerHTML = "";
      [...mapBroker.entries()].sort((a,b)=>Math.abs(b[1])-Math.abs(a[1])).forEach(([bk, v])=>{
        const li = document.createElement("li");
        li.className="pill";
        li.innerHTML = `${bk || '—'}：<span class="${v>=0?'profit':'loss'}">${v>=0?'+':''}${fmt(v)}</span>`;
        byBrokerEl.appendChild(li);
      });
    }
  }

  /* ===== Tabs ===== */
  const tabBtns = [...document.querySelectorAll(".rp-tabs .tab")];
  const views = {
    ledger: document.getElementById("view-ledger"),
    tiles: document.getElementById("view-tiles"),
    insights: document.getElementById("view-insights"),
  };
  tabBtns.forEach(btn=>{
    btn.addEventListener("click", ()=>{
      tabBtns.forEach(b=>b.classList.remove("active"));
      btn.classList.add("active");
      const v = btn.dataset.view;
      Object.keys(views).forEach(k=>views[k].classList.toggle("active", k===v));
      calcHeights();
      if (v==="tiles")  buildTiles();
      if (v==="insights") buildInsights();
    });
  });

  /* ===== Init ===== */
  attachRowHandlers();
  filterTable();      // KPI, bars, tiles, insights
  updateBars();
  calcHeights();
});