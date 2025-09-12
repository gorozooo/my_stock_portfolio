document.addEventListener("DOMContentLoaded", () => {
  /* ===== Utility ===== */
  const $  = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));
  const numeric = (t)=> {
    const v = parseFloat(String(t||"").replace(/[^\-0-9.]/g,""));
    return isNaN(v) ? 0 : v;
  };
  const fmt = (n)=> Math.round(n).toLocaleString();
  const pad2 = (n)=> n<10 ? "0"+n : ""+n;

  /* ===== Grabs ===== */
  const yearFilter   = $("#yearFilter");
  const monthFilter  = $("#monthFilter");
  const table        = $("#realizedTable");
  const tbody        = table?.querySelector("tbody");
  const emptyState   = $("#emptyState");
  const chips        = $$(".quick-chips .chip");
  const kpiToggle    = $("#kpiToggle");
  const controlsBox  = $(".rp-controls");
  const searchInput  = $("#searchInput");
  const clearSearch  = $("#clearSearch");
  const fab          = $("#scrollTopFab");
  const themeToggle  = $("#themeToggle");
  const densityToggle= $("#densityToggle");

  const dataRows = ()=> [...tbody.querySelectorAll("tr")].filter(r => !r.classList.contains("group-row"));

  /* ===== View Height Re-calc (ボトムタブ対応) ===== */
  const topbar = $(".rp-topbar");
  const tabs   = $(".rp-tabs");
  function findBottomTab() {
    return document.querySelector(".bottom-tab")
        || document.querySelector(".bottom_navbar")
        || document.getElementById("bottomTab")
        || document.querySelector("[data-bottom-tab]");
  }
  function vh(){ return Math.max(window.innerHeight, document.documentElement.clientHeight); }
  function recalcViewHeights(){
    const bottomEl = findBottomTab();
    const bottomH = bottomEl ? bottomEl.getBoundingClientRect().height : 0;
    document.documentElement.style.setProperty("--bottom-h", `${bottomH}px`);

    const topH    = (topbar?.getBoundingClientRect().height || 0);
    const ctrlH   = (controlsBox?.getBoundingClientRect().height || 0);
    const tabsH   = (tabs?.getBoundingClientRect().height || 0);
    const padding = 12;

    const rest = Math.max(120, vh() - (topH + ctrlH + tabsH + bottomH + padding));
    document.documentElement.style.setProperty("--view-h", `${rest}px`);
    $$(".view.active .scroll-area").forEach(el => { el.style.height = `${rest}px`; });
  }
  let bottomElInit = findBottomTab();
  if (bottomElInit) {
    const bottomObserver = new MutationObserver(recalcViewHeights);
    bottomObserver.observe(bottomElInit, {attributes:true, childList:true, subtree:true});
  }
  window.addEventListener("load", recalcViewHeights);
  window.addEventListener("resize", recalcViewHeights);
  window.addEventListener("orientationchange", () => setTimeout(recalcViewHeights, 60));

  /* ===== KPI ===== */
  const sumCount        = $("#sumCount");
  const winRateEl       = $("#winRate");
  const netProfitEl     = $("#netProfit");
  const totalProfitEl   = $("#totalProfit");
  const totalLossEl     = $("#totalLoss");
  const avgNetEl        = $("#avgNet");
  const avgProfitOnlyEl = $("#avgProfitOnly");
  const avgLossOnlyEl   = $("#avgLossOnly");
  const winArc          = $("#winArc");

  function updateSummary(){
    const visible = dataRows().filter(r => r.style.display !== "none");
    // 金額は .pnl-cell .num から読む（normalize後はネット値）
    const profitCells = visible.map(r => (r.children[6] && r.children[6].querySelector('.num')?.innerText) || "0");
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

    // リング：12時起点、アニメ＆色相
    if (winArc){
      const pct = Math.max(0, Math.min(100, winRate));
      winArc.setAttribute("stroke-dasharray", `${pct} 100`);
      winArc.setAttribute("stroke-dashoffset", "0");
      winArc.style.transition = 'stroke-dasharray .35s ease, stroke .35s ease';
      const hue = Math.round((pct/100)*120); // 0=赤, 120=緑
      winArc.style.stroke = `hsl(${hue} 70% 55%)`;
    }
  }

  /* ===== 正規化：手数料込みのネット損益に統一 ===== */
  function normalizePnLRows() {
    const rows = dataRows();
    rows.forEach(row => {
      const qtyStr = row.dataset.quantity ?? "";
      const isDividend = (qtyStr === "" || qtyStr === null);

      // 表のDOM
      const pnCell = row.querySelector(".pnl-cell");
      const pnNum  = pnCell?.querySelector(".num");
      const barEl  = pnCell?.querySelector(".bar");

      if (!pnCell || !pnNum || !barEl) return;

      if (isDividend) {
        // 配当はサーバ値をそのまま（data-profitが正）
        const serverPn = row.dataset.profit || "0";
        pnNum.textContent = serverPn;
        pnCell.classList.toggle("profit", numeric(serverPn) >= 0);
        pnCell.classList.toggle("loss",   numeric(serverPn) < 0);
        barEl.dataset.pn = String(numeric(serverPn));
        return;
      }

      // 売買ネット損益 = (売却単価*株数 - 取得単価*株数) - 手数料
      const qty  = parseFloat((row.dataset.quantity || "0").replace(/[^\-0-9.]/g,"")) || 0;
      const buy  = parseFloat((row.dataset.purchase || "0").replace(/[^\-0-9.]/g,"")) || 0;
      const sell = parseFloat((row.dataset.sell || "0").replace(/[^\-0-9.]/g,"")) || 0;
      const fee  = parseFloat((row.dataset.fee || "0").replace(/[^\-0-9.]/g,""));
      const buyAmt  = qty ? buy * qty : 0;
      const sellAmt = qty ? sell * qty : 0;
      const gross   = sellAmt - buyAmt;
      const feeAdj  = isNaN(fee) ? 0 : (fee >= 0 ? -fee : fee); // コストは差し引く
      const net     = gross + feeAdj;

      // 金額テキスト
      const s = Math.round(net).toLocaleString();
      pnNum.textContent = net > 0 ? `+${s}` : s;

      // 色
      pnCell.classList.remove("profit","loss");
      pnCell.classList.add(net < 0 ? "loss" : "profit");

      // データ属性もネット値に更新（KPI/モーダルで利用）
      row.dataset.profit = net > 0 ? `+${Math.round(net).toLocaleString()}` : `${Math.round(net).toLocaleString()}`;
      barEl.dataset.pn = String(Math.round(net));

      // 合計金額（モーダル用）
      row.dataset._sell_amount_total = Math.round(sellAmt).toString();
      row.dataset._buy_amount_total  = Math.round(buyAmt).toString();
    });
  }

  /* ===== 表フィルタ ===== */
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
    buildTiles();
    buildInsights();
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
        }else if (idx === 6){
          // 損益列は金額でソート（normalize済みの .num）
          const na = numeric(a.children[6].querySelector('.num')?.innerText || "0");
          const nb = numeric(b.children[6].querySelector('.num')?.innerText || "0");
          va = na; vb = nb;
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

  /* ===== P/L Bars ===== */
  function updateBars(){
    const visible = dataRows().filter(r => r.style.display !== "none");
    const pnVals = visible.map(r => {
      const bar = r.querySelector(".pnl-cell .bar");
      if (!bar) return 0;
      return Math.abs(parseFloat(bar.dataset.pn || "0"));
    });
    const max = Math.max(5000, ...pnVals);
    visible.forEach(r=>{
      const bar = r.querySelector(".pnl-cell .bar");
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
      fill.style.transition = 'width .25s ease';
      fill.style.width = Math.max(8, Math.round(64 * w / 100)) + "px";
      fill.style.borderRadius = "999px";
      const numText = r.querySelector('.pnl-cell .num')?.innerText || "0";
      const isLoss = numeric(numText) < 0;
      fill.style.background = isLoss
        ? "linear-gradient(90deg, rgba(255,80,100,.95), rgba(255,120,120,.85))"
        : "linear-gradient(90deg, rgba(0,220,130,.95), rgba(0,255,210,.85))";
    });
  }

  /* ===== Modal ===== */
  const modal    = $("#stockModal");
  const closeBtn = modal ? modal.querySelector(".close") : null;
  const modalTitle      = $("#modalTitle");
  const modalPurchase   = $("#modalPurchase");
  const modalQuantity   = $("#modalQuantity");
  const modalBroker     = $("#modalBroker");
  const modalAccount    = $("#modalAccount");
  const modalSell       = $("#modalSell");
  const modalProfit     = $("#modalProfit");
  const modalFee        = $("#modalFee");
  const modalSellAmount = $("#modalSellAmount");
  const modalBuyAmount  = $("#modalBuyAmount");

  const num = (t)=> { const s = String(t??"").replace(/[^\-0-9.]/g,''); const v = parseFloat(s); return isNaN(v) ? 0 : v; }
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
    // 正規化後の data-profit（ネット）を信頼
    const prof  = num(row.dataset.profit);
    // 合計額は正規化で保存したものを優先（なければ計算）
    const buyAmt  = row.dataset._buy_amount_total  ? num(row.dataset._buy_amount_total)  : (q ? buy * q : 0);
    const sellAmt = row.dataset._sell_amount_total ? num(row.dataset._sell_amount_total) : (q ? sell * q : 0);

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

  /* ===== Tiles View ===== */
  const tilesGrid = $("#tilesGrid");
  function buildTiles(){
    if (!tilesGrid) return;
    tilesGrid.innerHTML = "";
    const visible = dataRows().filter(r => r.style.display !== "none");
    visible.slice(0, 200).forEach(row=>{
      const name=row.dataset.name||"", code=row.dataset.code||"", broker=row.dataset.broker||"", type=row.dataset.type||"";
      const qty=row.dataset.quantity||"-";
      const profit=row.querySelector('.pnl-cell .num')?.innerText || "0";
      const rate=row.dataset.rate || "0%";
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
  const monthlyChart = $("#monthlyChart");
  const topGainersEl = $("#topGainers");
  const topLosersEl  = $("#topLosers");
  const byBrokerEl   = $("#byBroker");

  function buildInsights(){
    const visible = dataRows().filter(r => r.style.display !== "none");
    const mapMonth = new Map();
    const mapName  = new Map();
    const mapBroker= new Map();

    visible.forEach(r=>{
      const ym = (r.dataset.date||"").slice(0,7);
      const pn = numeric(r.querySelector('.pnl-cell .num')?.innerText || "0");
      const name = r.dataset.name||"";
      const broker = r.dataset.broker||"";
      mapMonth.set(ym, (mapMonth.get(ym)||0)+pn);
      mapName.set(name,  (mapName.get(name)||0)+pn);
      mapBroker.set(broker,(mapBroker.get(broker)||0)+pn);
    });

    // Chart（純Canvas）
    if (monthlyChart){
      const ctx = monthlyChart.getContext("2d");
      ctx.clearRect(0,0,monthlyChart.width, monthlyChart.height);
      const W = monthlyChart.width, H = monthlyChart.height, P=30;
      const months = [...mapMonth.keys()].sort();
      const values = months.map(m=>mapMonth.get(m));
      const maxAbs = Math.max(1000, ...values.map(v=>Math.abs(v)));

      ctx.strokeStyle = "rgba(255,255,255,.25)";
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(P, H-P); ctx.lineTo(W-P, H-P); ctx.moveTo(P, P); ctx.lineTo(P, H-P); ctx.stroke();
      const zeroY = H/2;
      ctx.strokeStyle = "rgba(255,255,255,.18)";
      ctx.beginPath(); ctx.moveTo(P, zeroY); ctx.lineTo(W-P, zeroY); ctx.stroke();

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
        ctx.fillStyle = "rgba(255,255,255,.75)";
        ctx.font = "12px system-ui";
        ctx.textAlign = "center";
        ctx.fillText(m, x+barW/2, H-10);
      });
    }

    // Top gainers / losers
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

    // By broker
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

  /* ===== Tabs & FAB ===== */
  const tabBtns = $$(".rp-tabs .tab");
  const views = {
    ledger: $("#view-ledger"),
    tiles: $("#view-tiles"),
    insights: $("#view-insights"),
  };
  function currentScrollArea(){ return document.querySelector(".view.active .scroll-area"); }
  function onScroll(){
    const area = currentScrollArea();
    if (!area) return;
    const show = area.scrollTop > 200;
    fab.classList.toggle("show", show);
  }
  function bindScrollArea(){
    const area = currentScrollArea();
    if (!area) return;
    area.removeEventListener("scroll", onScroll);
    area.addEventListener("scroll", onScroll, {passive:true});
  }
  bindScrollArea();
  fab.addEventListener("click", ()=>{
    const area = currentScrollArea();
    area?.scrollTo({top:0, behavior:"smooth"});
  });
  tabBtns.forEach(btn=>{
    btn.addEventListener("click", ()=>{
      tabBtns.forEach(b=>b.classList.remove("active"));
      btn.classList.add("active");
      const v = btn.dataset.view;
      Object.keys(views).forEach(k=>views[k].classList.toggle("active", k===v));
      setTimeout(() => { recalcViewHeights(); bindScrollArea(); }, 0);
      if (v==="tiles")   buildTiles();
      if (v==="insights") buildInsights();
    });
  });

  /* ===== KPI 開閉（初期は閉） ===== */
  if (kpiToggle && controlsBox){
    const PREF_COLLAPSE = "rp.kpiCollapsed";
    function setCollapsed(collapsed){
      controlsBox.classList.toggle("collapsed", collapsed);
      kpiToggle.setAttribute("aria-expanded", (!collapsed).toString());
      kpiToggle.querySelector(".kpi-caret").textContent = collapsed ? "▼" : "▲";
      setTimeout(recalcViewHeights, 0);
    }
    const saved = localStorage.getItem(PREF_COLLAPSE);
    if (saved === null){ setCollapsed(true); localStorage.setItem(PREF_COLLAPSE, "1"); }
    else{ setCollapsed(saved === "1"); }
    kpiToggle.addEventListener("click", ()=>{
      const next = !controlsBox.classList.contains("collapsed");
      setCollapsed(next);
      localStorage.setItem(PREF_COLLAPSE, next ? "1" : "0");
    });
  }

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

  /* ===== Init ===== */
  normalizePnLRows();   // ← 手数料ネット化で統一（額・バー・KPI・モーダルに反映）
  attachRowHandlers();
  filterTable();        // ← 検索/フィルタ後にKPI/バー/タイル/インサイト更新
  updateBars();
  setTimeout(() => { recalcViewHeights(); bindScrollArea(); }, 0);
});