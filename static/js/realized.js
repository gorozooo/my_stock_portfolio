document.addEventListener("DOMContentLoaded", () => {
  // 要素
  const yearFilter  = document.getElementById("yearFilter");
  const monthFilter = document.getElementById("monthFilter");
  const table       = document.getElementById("realizedTable");
  const tbody       = table ? table.querySelector("tbody") : null;
  const emptyState  = document.getElementById("emptyState");
  const chips       = [...document.querySelectorAll(".quick-chips .chip")];

  // KPI（存在チェックを入れて安全に扱う）
  const sumCountEl        = document.getElementById("sumCount");
  const winRateEl         = document.getElementById("winRate");
  const netProfitEl       = document.getElementById("netProfit");
  const totalProfitEl     = document.getElementById("totalProfit");
  const totalLossEl       = document.getElementById("totalLoss");
  const avgNetEl          = document.getElementById("avgNet");
  const avgProfitOnlyEl   = document.getElementById("avgProfitOnly");
  const avgLossOnlyEl     = document.getElementById("avgLossOnly");

  // ---- 早期リターン（致命的に必要な要素が無い場合）----
  if (!tbody || !sumCountEl || !winRateEl || !netProfitEl) {
    console.warn("[realized] 必須要素が見つかりませんでした。");
    return;
  }

  // ===== デモデータ注入（テスト用行を増やす） =====
  const SEED_DEMO_ROWS = true;         // 実運用は false へ
  const MIN_ROWS_FOR_TEST = 60;

  function ymd(date){
    const y = date.getFullYear();
    const m = String(date.getMonth()+1).padStart(2,'0');
    const d = String(date.getDate()).padStart(2,'0');
    return `${y}-${m}-${d}`;
  }
  function monthLabel(date){
    return `${date.getFullYear()}年 ${date.getMonth()+1}月`;
  }
  function addGroupRow(label){
    const tr = document.createElement('tr');
    tr.className = 'group-row';
    const td = document.createElement('td');
    td.colSpan = 6;
    td.textContent = label;
    tr.appendChild(td);
    tbody.appendChild(tr);
  }
  function addDataRow({date, name, tradeType, qty, profit, rate, price, sector, purchase}){
    const tr = document.createElement('tr');
    tr.dataset.date     = date;
    tr.dataset.name     = name;
    tr.dataset.price    = price;
    tr.dataset.sector   = sector;
    tr.dataset.purchase = purchase;
    tr.dataset.quantity = String(qty);
    tr.dataset.profit   = profit > 0 ? `+${profit.toLocaleString()}` : `${profit.toLocaleString()}`;
    tr.dataset.rate     = (rate > 0 ? `+${rate}` : `${rate}`) + '%';

    const cells = [];
    // 日付
    const td0 = document.createElement('td'); td0.textContent = date; cells.push(td0);
    // 銘柄
    const td1 = document.createElement('td'); td1.className = 'stock-name-cell';
    const span1 = document.createElement('span'); span1.textContent = name; td1.appendChild(span1); cells.push(td1);
    // 区分
    const td2 = document.createElement('td'); td2.className = 'trade-type-cell';
    const span2 = document.createElement('span'); span2.textContent = tradeType; td2.appendChild(span2); cells.push(td2);
    // 数量
    const td3 = document.createElement('td'); td3.textContent = String(qty); cells.push(td3);
    // 損益額
    const td4 = document.createElement('td');
    td4.textContent = (profit > 0 ? `+${profit.toLocaleString()}` : profit.toLocaleString());
    td4.className = profit > 0 ? 'profit' : (profit < 0 ? 'loss' : '');
    cells.push(td4);
    // 損益率
    const td5 = document.createElement('td');
    td5.textContent = (rate > 0 ? `+${rate}` : `${rate}`) + '%';
    td5.className = rate > 0 ? 'profit' : (rate < 0 ? 'loss' : '');
    cells.push(td5);

    cells.forEach(td => tr.appendChild(td));
    tbody.appendChild(tr);
  }
  function getDataRows(){
    return [...tbody.querySelectorAll('tr')].filter(r => !r.classList.contains('group-row'));
  }
  function seedDemoRowsIfNeeded(){
    const current = getDataRows();
    if (!SEED_DEMO_ROWS || current.length >= MIN_ROWS_FOR_TEST) return;

    const names   = ['トヨタ','任天堂','ソニー','キーエンス','武田薬品','三菱UFJ','KDDI','リクルート','オリックス','ZHD'];
    const sectors = ['自動車','ゲーム','電機','精密','医薬','銀行','通信','人材','金融','IT'];
    const trades  = ['売却','売却','売却','配当'];
    const today = new Date();
    let lastLabel = null;
    let created = 0;

    for (let i=0; i<120 && created < (MIN_ROWS_FOR_TEST - current.length); i+= (Math.random()<0.4?2:1) ){
      const d = new Date(today);
      d.setDate(today.getDate() - i);
      const ymdStr = ymd(d);

      const label = monthLabel(d);
      if (lastLabel !== label){
        addGroupRow(label);
        lastLabel = label;
      }

      const idx = Math.floor(Math.random()*names.length);
      const name = names[idx];
      const sector = sectors[idx];
      const qty = [10,20,30,50,100][Math.floor(Math.random()*5)];
      const base = Math.floor( (Math.random()*2-1) * 80000 ); // -80,000〜+80,000
      const profit = base === 0 ? 5000 : base;
      const rate   = Math.max(-20, Math.min(20, Math.round((profit/ (qty*1000))*100)));
      const price  = (Math.floor(Math.random()*1000)+5000).toLocaleString();
      const purchase = (Math.floor(Math.random()*1000)+4500).toLocaleString();
      const tradeType = trades[Math.floor(Math.random()*trades.length)];

      addDataRow({date: ymdStr, name, tradeType, qty, profit, rate, price, sector, purchase});
      created++;
    }
  }

  // ===== 数値ユーティリティ =====
  const numeric = (t)=> {
    const v = parseFloat(String(t||"").replace(/[^\-0-9.]/g,""));
    return isNaN(v) ? 0 : v;
  };
  const pad2 = (n)=> n<10 ? "0"+n : ""+n;
  const fmt  = (n)=> Math.round(n).toLocaleString();

  // ===== KPI更新（安全ガード付き）
  function updateSummary(){
    const rows = getDataRows().filter(r => r.style.display !== "none");
    const vals = rows.map(r => numeric(r.children[4]?.textContent));
    const pos  = vals.filter(v => v > 0);
    const neg  = vals.filter(v => v < 0);

    const count = rows.length;
    const wins  = pos.length;
    const net   = vals.reduce((a,b)=>a+b,0);
    const posSum= pos.reduce((a,b)=>a+b,0);
    const negSum= neg.reduce((a,b)=>a+b,0);
    const avgNet= count ? net / count : 0;
    const avgPos= pos.length ? posSum / pos.length : 0;
    const avgNeg= neg.length ? negSum / neg.length : 0;

    // 件数・勝率・実現損益（必ず文字を入れる）
    sumCountEl.textContent  = String(count);
    winRateEl.textContent   = count ? `${Math.round((wins/count)*100)}%` : "0%";
    netProfitEl.textContent = fmt(net);
    netProfitEl.classList.toggle('profit', net > 0);
    netProfitEl.classList.toggle('loss', net < 0);

    // 2行目
    if (totalProfitEl) totalProfitEl.textContent = fmt(posSum);
    if (totalLossEl)   totalLossEl.textContent   = fmt(negSum);

    // 3行目
    if (avgNetEl){
      avgNetEl.textContent = fmt(avgNet);
      avgNetEl.classList.toggle('profit', avgNet > 0);
      avgNetEl.classList.toggle('loss', avgNet < 0);
    }
    if (avgProfitOnlyEl) avgProfitOnlyEl.textContent = fmt(avgPos);
    if (avgLossOnlyEl)   avgLossOnlyEl.textContent   = fmt(avgNeg);
  }

  // ===== 表フィルタ
  function filterTable() {
    const year  = yearFilter ? yearFilter.value : "";
    const month = monthFilter ? monthFilter.value : "";

    getDataRows().forEach(row => {
      const date = row.dataset.date || "";
      const [yy, mm] = date.split("-");
      let show = true;
      if (year  && yy !== year)  show = false;
      if (month && mm !== month) show = false;
      row.style.display = show ? "" : "none";
    });

    if (emptyState){
      emptyState.style.display = getDataRows().some(r => r.style.display !== "none") ? "none" : "";
    }
    updateSummary(); // ← フィルタ適用のたびにKPI上書き
  }

  // ===== イベント
  if (yearFilter) yearFilter.addEventListener("change", ()=>{
    chips.forEach(c=>c.classList.remove('active'));
    filterTable();
  });
  if (monthFilter) monthFilter.addEventListener("change", ()=>{
    chips.forEach(c=>c.classList.remove('active'));
    filterTable();
  });

  chips.forEach(b=>{
    b.addEventListener("click", ()=>{
      const now = new Date();
      const y = now.getFullYear();
      const m = now.getMonth()+1;
      const mm = pad2(m);
      const key = b.dataset.range;

      if (key === "this-month"){
        if (yearFilter)  yearFilter.value = String(y);
        if (monthFilter) monthFilter.value = mm;
      } else if (key === "last-month"){
        const d = new Date(y, m-2, 1);
        if (yearFilter)  yearFilter.value = String(d.getFullYear());
        if (monthFilter) monthFilter.value = pad2(d.getMonth()+1);
      } else if (key === "this-year"){
        if (yearFilter)  yearFilter.value = String(y);
        if (monthFilter) monthFilter.value = "";
      } else {
        if (yearFilter)  yearFilter.value = "";
        if (monthFilter) monthFilter.value = "";
      }
      chips.forEach(c=>c.classList.remove('active'));
      b.classList.add('active');
      filterTable();
    });
  });

  // ===== ソート（ヘッダークリック）
  table.querySelectorAll("thead th").forEach((th, idx)=>{
    th.addEventListener("click", ()=>{
      const asc = th.dataset.asc !== "true";
      th.dataset.asc = asc;

      const visible = getDataRows().filter(r => r.style.display !== "none");
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

      const hidden = getDataRows().filter(r => r.style.display === "none");
      [...visible, ...hidden].forEach(r => tbody.appendChild(r));

      // 並べ替え後もKPIは据え置き（可視行の合計/平均なので変化しないが、念のため更新）
      updateSummary();
    });
  });

  // ===== モーダル
  const modal    = document.getElementById("stockModal");
  const closeBtn = modal ? modal.querySelector(".close") : null;
  const modalName     = document.getElementById("modalName");
  const modalPrice    = document.getElementById("modalPrice");
  const modalSector   = document.getElementById("modalSector");
  const modalPurchase = document.getElementById("modalPurchase");
  const modalQuantity = document.getElementById("modalQuantity");
  const modalProfit   = document.getElementById("modalProfit");
  const modalRate     = document.getElementById("modalRate");

  function openModalForRow(row){
    if (!modal) return;
    if (modalName)     modalName.textContent     = row.dataset.name     || "";
    if (modalPrice)    modalPrice.textContent    = row.dataset.price    || "";
    if (modalSector)   modalSector.textContent   = row.dataset.sector   || "";
    if (modalPurchase) modalPurchase.textContent = row.dataset.purchase || "";
    if (modalQuantity) modalQuantity.textContent = row.dataset.quantity || "";
    if (modalProfit)   modalProfit.textContent   = row.dataset.profit   || "";
    if (modalRate)     modalRate.textContent     = row.dataset.rate     || "";
    modal.classList.add("show");
  }

  const TAP_MAX_MOVE = 10, TAP_MAX_TIME = 500;
  function attachRowHandlers(){
    getDataRows().forEach(row=>{
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
  }

  // ===== 初期処理
  seedDemoRowsIfNeeded();   // 先に行を増やす
  attachRowHandlers();      // 追加後にハンドラを付ける
  filterTable();            // 可視行でKPIを即時更新
  // 念のため、描画が落ち着いたタイミングでもう一度KPIを上書き
  requestAnimationFrame(updateSummary);
});