document.addEventListener("DOMContentLoaded", () => {
  /* ===== Utility ===== */
  const $  = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));
  const toNum = (t)=> {
    if (t === null || t === undefined) return 0;
    const s = String(t).replace(/[^\-0-9.]/g,"");
    if (s === "" || s === "-" || s === ".") return 0;
    const v = parseFloat(s);
    return isNaN(v) ? 0 : v;
  };
  const fmt = (n)=> Math.round(n).toLocaleString();
  const pad2 = (n)=> n<10 ? "0"+n : ""+n;

  /* ===== Elements ===== */
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

  /* ===== View Height / Bottom Tab ===== */
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
    const vals = visible.map(r => toNum(r.querySelector('.pnl-cell .num')?.innerText || "0"));
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
    totalLossEl.textContent   = fmt(negSum);
    avgNetEl.textContent = fmt(avgNet);
    avgNetEl.classList.toggle('profit', avgNet > 0);
    avgNetEl.classList.toggle('loss', avgNet < 0);
    avgProfitOnlyEl.textContent = fmt(avgPos);
    avgLossOnlyEl.textContent   = fmt(avgNeg);

    if (winArc){
      const pct = Math.max(0, Math.min(100, winRate));
      winArc.setAttribute("stroke-dasharray", `${pct} 100`);
      winArc.setAttribute("stroke-dashoffset", "0");
      const hue = Math.round((pct/100)*120);
      winArc.style.stroke = `hsl(${hue} 70% 55%)`;
    }
  }

  /* ===== 手数料込み“ネット損益”に正規化 ===== */
  function ensureNumSpan(row){
    // num span が無ければ生成（必ず金額が出るように）
    const pnCell = row.querySelector(".pnl-cell");
    if (!pnCell) return null;
    let numSpan = pnCell.querySelector(".num");
    const amt = pnCell.querySelector(".amount");
    const bar = pnCell.querySelector(".bar");
    if (!numSpan && amt){
      numSpan = document.createElement("span");
      numSpan.className = "num";
      amt.insertBefore(numSpan, bar || amt.firstChild);
    }
    return numSpan;
  }

  function normalizePnLRows() {
    dataRows().forEach(row => {
      const pnCell = row.querySelector(".pnl-cell");
      const barEl  = pnCell?.querySelector(".bar");
      const numSpan= ensureNumSpan(row); // ★ ここで必ず .num を確保
      if (!pnCell || !barEl || !numSpan) return;

      const qtyStr = row.dataset.quantity ?? "";
      const isDividend = (qtyStr === "" || qtyStr === null);

      if (isDividend) {
        // 配当：サーバ値をそのまま表示
        const serverPn = toNum(row.dataset.profit || "0");
        numSpan.textContent = serverPn > 0 ? `+${fmt(serverPn)}` : fmt(serverPn);
        pnCell.classList.toggle("profit", serverPn >= 0);
        pnCell.classList.toggle("loss",   serverPn < 0);
        barEl.dataset.pn = String(serverPn);
        // 合計金額は不要
        return;
      }

      // 単価×株数 → 売却額/取得額
      const qty  = toNum(row.dataset.quantity);
      const buy  = toNum(row.dataset.purchase);
      const sell = toNum(row.dataset.sell);
      // 手数料：符号に依存せず必ず“コスト控除”扱い（絶対値で引く）
      const feeRaw = toNum(row.dataset.fee);
      const feeAbs = Math.abs(feeRaw);

      const buyAmt  = qty ? buy * qty : 0;
      const sellAmt = qty ? sell * qty : 0;
      const net     = (sellAmt - buyAmt) - feeAbs; // ★ 絶対値で差し引く

      // 金額（必ず出す）
      numSpan.textContent = net > 0 ? `+${fmt(net)}` : fmt(net);

      // 色
      pnCell.classList.remove("profit","loss");
      pnCell.classList.add(net < 0 ? "loss" : "profit");

      // データ属性更新（KPI/タイル/インサイトでも利用）
      row.dataset.profit = net > 0 ? `+${fmt(net)}` : `${fmt(net)}`;
      barEl.dataset.pn   = String(net);

      // 合計金額（モーダル用）
      row.dataset._sell_amount_total = String(Math.round(sellAmt));
      row.dataset._buy_amount_total  = String(Math.round(buyAmt));
      row.dataset._fee_abs           = String(Math.round(feeAbs)); // 表示用の絶対値も保持
    });
  }

  /* ===== フィルタ ===== */
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
          va = toNum(a.children[6].querySelector('.num')?.innerText || "0");
          vb = toNum(b.children[6].querySelector('.num')?.innerText || "0");
        }else{
          const na = toNum(a.children[idx].textContent);
          const nb = toNum(b.children[idx].textContent);
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
    const pnVals = visible.map(r => Math.abs(toNum(r.querySelector(".pnl-cell .bar")?.dataset.pn || "0")));
    const max = Math.max(5000, ...pnVals);
    visible.forEach(r=>{
      const bar = r.querySelector(".pnl-cell .bar");
      if (!bar) return;
      const val = Math.abs(toNum(bar.dataset.pn || "0"));
      const pct = Math.min(100, Math.round((val / max) * 100));
      let fill = bar.firstElementChild;
      if (!fill){
        fill = document.createElement("span");
        fill.style.position = "absolute";
        fill.style.left = "0"; fill.style.top = "0"; fill.style.bottom = "0";
        bar.appendChild(fill);
      }
      fill.style.transition = 'width .25s ease';
      fill.style.width = Math.max(8, Math.round(72 * pct / 100)) + "px";
      fill.style.borderRadius = "999px";
      const numText = r.querySelector('.pnl-cell .num')?.innerText || "0";
      const isLoss = toNum(numText) < 0;
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

  const yen = (n)=> Math.round(n).toLocaleString('ja-JP');

  function openModalForRow(row){
    if (!modal) return;
    const name  = row.dataset.name || "";
    const code  = row.dataset.code || "";
    const title = code ? `${name}（${code}）` : name;

    const q     = toNum(row.dataset.quantity);
    const buy   = toNum(row.dataset.purchase);
    const sell  = toNum(row.dataset.sell);
    const feeAbs= toNum(row.dataset._fee_abs || row.dataset.fee); // 絶対値優先
    const prof  = toNum(row.dataset.profit); // 正規化済みネット

    const buyAmt  = row.dataset._buy_amount_total  ? toNum(row.dataset._buy_amount_total)  : (q ? buy * q : 0);
    const sellAmt = row.dataset._sell_amount_total ? toNum(row.dataset._sell_amount_total) : (q ? sell * q : 0);

    modalTitle.textContent     = title;
    modalPurchase.textContent  = buy ? yen(buy) : '-';
    modalQuantity.textContent  = q   ? yen(q)   : '-';
    modalBroker.textContent    = row.dataset.broker  || '';
    modalAccount.textContent   = row.dataset.account || '';
    modalSell.textContent      = sell ? yen(sell) : '-';

    modalProfit.textContent    = prof ? (prof>0? '+'+yen(prof) : yen(prof)) : '0';
    modalProfit.classList.remove('profit','loss');
    modalProfit.classList.add(prof<0 ? 'loss' : 'profit');

    // 手数料は常に正の金額表示（コスト）にする
    modalFee.textContent        = yen(Math.abs(feeAbs));
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

  /* ===== Tiles ===== */
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
          <div class="${toNum(profit)>=0?'profit':'loss'}"><span class="badge">損益</span> ${profit}</div>
          <div class="${rate.trim().startsWith('-')?'loss':'profit'}"><span class="badge">率</span> ${rate}</div>
          <div><span class="badge">証券</span> ${broker}</div>
          <div><span class="badge">日付</span> ${row.dataset.date}</div>
        </div>`;
      tile.addEventListener("click", ()=> openModalForRow(row));
      tilesGrid.appendChild(tile);
    });
  }

  /* ===== Insights ===== */
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
      const pn = toNum(r.querySelector('.pnl-cell .num')?.innerText || "0");
      const name = r.dataset.name||"";
      const broker = r.dataset.broker||"";
      mapMonth.set(ym, (mapMonth.get(ym)||0)+pn);
      mapName.set(name,  (mapName.get(name)||0)+pn);
      mapBroker.set(broker,(mapBroker.get(broker)||0)+pn);
    });

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
  // 1) まず .num を強制的に用意してから
  dataRows().forEach(ensureNumSpan);
  // 2) ネット損益に正規化（手数料は絶対値で差し引く）
  normalizePnLRows();
  // 3) 各種描画
  attachRowHandlers();
  filterTable();
  updateBars();
  setTimeout(() => { recalcViewHeights(); bindScrollArea(); }, 0);
});