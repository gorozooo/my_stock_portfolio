document.addEventListener("DOMContentLoaded", () => {
  // ===== 要素 =====
  const yearFilter  = document.getElementById("yearFilter");
  const monthFilter = document.getElementById("monthFilter");
  const table       = document.getElementById("realizedTable");
  const tbody       = table.querySelector("tbody");
  const emptyState  = document.getElementById("emptyState");
  const chips       = [...document.querySelectorAll(".quick-chips .chip")];

  // ===== デモデータ注入（必要時のみ） =====
  // 実運用: false にしてください
  const SEED_DEMO_ROWS   = false;
  const MIN_ROWS_FOR_TEST= 60;

  function ymd(date){
    const y = date.getFullYear();
    const m = String(date.getMonth()+1).padStart(2,'0');
    const d = String(date.getDate()).padStart(2,'0');
    return `${y}-${m}-${d}`;
  }
  function monthLabel(date){ return `${date.getFullYear()}年 ${date.getMonth()+1}月`; }
  function addGroupRow(label){
    const tr = document.createElement('tr');
    tr.className = 'group-row';
    const td = document.createElement('td');
    td.colSpan = 8; // 列数に合わせる
    td.textContent = label;
    tr.appendChild(td);
    tbody.appendChild(tr);
  }

  // デモ用のダミー候補
  const names   = ['トヨタ','任天堂','ソニー','キーエンス','武田薬品','三菱UFJ','KDDI','リクルート','オリックス','ZHD'];
  const codes   = ['7203','7974','6758','6861','4502','8306','9433','6098','8591','4689'];
  const sectors = ['自動車','ゲーム','電機','精密','医薬','銀行','通信','人材','金融','IT'];
  const brokers = ['SBI','楽天','松井','マネックス','野村'];
  const accounts= ['特定','一般','NISA'];
  const trades  = ['売却','配当']; // 表示は「区分」

  function addDataRow({
    date, name, code, broker, account, tradeType, qty,
    profit, rate, purchase, sell, fee, sector
  }){
    const tr = document.createElement('tr');

    // data-*（モーダル用）
    tr.dataset.date     = date;
    tr.dataset.name     = name;
    tr.dataset.code     = code || '';
    tr.dataset.broker   = broker || '';
    tr.dataset.account  = account || '';
    tr.dataset.type     = tradeType;
    tr.dataset.quantity = (qty ?? '') + '';
    tr.dataset.profit   = profit > 0 ? `+${profit.toLocaleString()}` : `${profit.toLocaleString()}`;
    tr.dataset.rate     = (rate > 0 ? `+${rate}` : `${rate}`) + '%';
    tr.dataset.purchase = purchase != null ? String(purchase) : '';
    tr.dataset.sell     = sell != null ? String(sell) : '';
    tr.dataset.fee      = fee != null ? String(fee) : '';
    tr.dataset.sector   = sector || '';

    // 表示セル（8列）
    const tds = [];

    const td0 = document.createElement('td'); td0.textContent = date; tds.push(td0);

    const td1 = document.createElement('td'); td1.className = 'stock-name-cell';
    const span1 = document.createElement('span'); span1.textContent = name; td1.appendChild(span1); tds.push(td1);

    const td2 = document.createElement('td'); td2.textContent = broker || ''; tds.push(td2);
    const td3 = document.createElement('td'); td3.textContent = account || ''; tds.push(td3);

    const td4 = document.createElement('td'); td4.className = 'trade-type-cell';
    const span2 = document.createElement('span'); span2.textContent = tradeType; td4.appendChild(span2); tds.push(td4);

    const td5 = document.createElement('td'); td5.textContent = qty != null ? String(qty) : '-'; tds.push(td5);

    const td6 = document.createElement('td');
    td6.textContent = (profit > 0 ? `+${profit.toLocaleString()}` : profit.toLocaleString());
    td6.className = profit > 0 ? 'profit' : (profit < 0 ? 'loss' : '');
    tds.push(td6);

    const td7 = document.createElement('td');
    td7.textContent = (rate > 0 ? `+${rate}` : `${rate}`) + '%';
    td7.className = rate > 0 ? 'profit' : (rate < 0 ? 'loss' : '');
    tds.push(td7);

    tds.forEach(td => tr.appendChild(td));
    tbody.appendChild(tr);
  }

  function seedDemoRowsIfNeeded(){
    const currentDataRows = [...tbody.querySelectorAll('tr')].filter(r => !r.classList.contains('group-row'));
    if (!SEED_DEMO_ROWS || currentDataRows.length >= MIN_ROWS_FOR_TEST) return;

    const today = new Date();
    let lastMonth = null;
    let made = 0;

    for (let i=0; i<160 && made < (MIN_ROWS_FOR_TEST - currentDataRows.length); i+= (Math.random()<0.4?2:1) ){
      const d = new Date(today);
      d.setDate(today.getDate() - i);
      const ymdStr = ymd(d);

      const mLabel = monthLabel(d);
      if (lastMonth !== mLabel){
        addGroupRow(mLabel);
        lastMonth = mLabel;
      }

      const idx = Math.floor(Math.random()*names.length);
      const name = names[idx];
      const code = codes[idx];
      const sector = sectors[idx];
      const broker = brokers[Math.floor(Math.random()*brokers.length)];
      const account= accounts[Math.floor(Math.random()*accounts.length)];
      const tradeType = trades[Math.floor(Math.random()*trades.length)];
      const qty = [10,20,30,50,100,null][Math.floor(Math.random()*6)]; // 配当用に null を混ぜる

      const base = Math.floor( (Math.random()*2-1) * 80000 ); // -80,000〜+80,000
      const profit = base === 0 ? 5000 : base;
      const rate   = (qty ? Math.max(-20, Math.min(20, Math.round((profit/ (qty*1000))*100))) : 0);
      const purchase = qty ? Math.floor(Math.random()*2000)+5000 : null;   // 例: 5000〜7000 / 配当はnull
      const sell     = qty ? purchase + Math.floor((Math.random()*2-1)*1500) : null;
      const fee      = - Math.floor(Math.random()*250); // 手数料は負数

      addDataRow({
        date: ymdStr, name, code, broker, account, tradeType, qty,
        profit, rate, purchase, sell, fee, sector
      });
      made++;
    }
  }
  seedDemoRowsIfNeeded();

  // ===== テーブル行取得（グループ行を除く） =====
  function getAllDataRows(){
    const rows = [...tbody.querySelectorAll('tr')];
    return rows.filter(r => !r.classList.contains('group-row'));
  }

  // ===== KPI =====
  const sumCount        = document.getElementById("sumCount");
  const winRateEl       = document.getElementById("winRate");
  const netProfitEl     = document.getElementById("netProfit");
  const totalProfitEl   = document.getElementById("totalProfit");
  const totalLossEl     = document.getElementById("totalLoss");
  const avgNetEl        = document.getElementById("avgNet");
  const avgProfitOnlyEl = document.getElementById("avgProfitOnly");
  const avgLossOnlyEl   = document.getElementById("avgLossOnly");

  // 数値ユーティリティ
  const numeric = (t)=> {
    const v = parseFloat(String(t||"").replace(/[^\-0-9.]/g,""));
    return isNaN(v) ? 0 : v;
  };
  const pad2 = (n)=> n<10 ? "0"+n : ""+n;
  const fmt  = (n)=> Math.round(n).toLocaleString();

  // KPI更新
  const COL_PROFIT = 6; // 8列構成で損益額は7列目(0始まりで6)
  function updateSummary(){
    const dataRows = getAllDataRows().filter(r => r.style.display !== "none");
    const vals = dataRows.map(r => numeric((r.children[COL_PROFIT] && r.children[COL_PROFIT].textContent) || '0'));
    const pos  = vals.filter(v => v > 0);
    const neg  = vals.filter(v => v < 0);

    const count = dataRows.length;
    const wins  = pos.length;
    const net   = vals.reduce((a,b)=>a+b,0);
    const posSum= pos.reduce((a,b)=>a+b,0);
    const negSum= neg.reduce((a,b)=>a+b,0);
    const avgNet= count ? net / count : 0;
    const avgPos= pos.length ? posSum / pos.length : 0;
    const avgNeg= neg.length ? negSum / neg.length : 0;

    if (sumCount)  sumCount.textContent  = String(count);
    if (winRateEl) winRateEl.textContent = count ? `${Math.round((wins/count)*100)}%` : "0%";

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
  }

  // ===== 表フィルタ =====
  function filterTable() {
    const year  = yearFilter ? yearFilter.value : "";
    const month = monthFilter ? monthFilter.value : "";

    getAllDataRows().forEach(row => {
      const date = row.dataset.date || "";
      const [yy, mm] = date.split("-");
      let show = true;
      if (year  && yy !== year)  show = false;
      if (month && mm !== month) show = false;
      row.style.display = show ? "" : "none";
    });

    if (emptyState){
      emptyState.style.display = getAllDataRows().some(r => r.style.display !== "none") ? "none" : "";
    }
    updateSummary();
  }

  if (yearFilter){
    yearFilter.addEventListener("change", ()=>{
      chips.forEach(c=>c.classList.remove('active'));
      filterTable();
    });
  }
  if (monthFilter){
    monthFilter.addEventListener("change", ()=>{
      chips.forEach(c=>c.classList.remove('active'));
      filterTable();
    });
  }

  // ===== クイックフィルタ（アクティブ表示） =====
  chips.forEach(b=>{
    b.addEventListener("click", ()=>{
      const now = new Date();
      const y = now.getFullYear();
      const m = now.getMonth()+1;
      const mm = pad2(m);
      const key = b.dataset.range;

      if (yearFilter && monthFilter){
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
      }
      chips.forEach(c=>c.classList.remove('active'));
      b.classList.add('active');
      filterTable();
    });
  });

  // ===== ソート（ヘッダークリック） =====
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

  // ===== モーダル =====
  const modal    = document.getElementById("stockModal");
  const closeBtn = modal ? modal.querySelector(".close") : null;

  // 新モーダルの要素
  const modalTitle      = document.getElementById("modalTitle");
  const modalPurchase   = document.getElementById("modalPurchase");
  const modalQuantity   = document.getElementById("modalQuantity");
  const modalBroker     = document.getElementById("modalBroker");
  const modalAccount    = document.getElementById("modalAccount");
  const modalSell       = document.getElementById("modalSell");
  const modalProfit     = document.getElementById("modalProfit");
  const modalFee        = document.getElementById("modalFee");
  const modalSellAmount = document.getElementById("modalSellAmount"); // 売却額
  const modalBuyAmount  = document.getElementById("modalBuyAmount");  // 取得額

  function num(text){
    if (text == null) return 0;
    const s = String(text).replace(/[^\-0-9.]/g,'');
    const v = parseFloat(s);
    return isNaN(v) ? 0 : v;
  }
  function yen(n){ return Math.round(n).toLocaleString('ja-JP'); }

  function openModalForRow(row){
    if (!modal) return;
    const name  = row.dataset.name || "";
    const code  = row.dataset.code || "";
    const title = code ? `${name}（${code}）` : name;

    const q     = num(row.dataset.quantity);
    const buy   = num(row.dataset.purchase); // 取得単価
    const sell  = num(row.dataset.sell);     // 売却単価
    const fee   = num(row.dataset.fee);      // 手数料（負数想定）
    const prof  = num(row.dataset.profit);   // 損益額（+/-あり）

    // 合計額（手数料は個別表示、合計には含めない設計）
    const buyAmt  = q ? buy * q : 0;   // 取得額
    const sellAmt = q ? sell * q : 0;  // 売却額

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

  function closeModal(){
    if (!modal) return;
    modal.classList.remove("show");
  }

  if (closeBtn) closeBtn.addEventListener("click", closeModal);
  window.addEventListener("click", (e)=>{ if (modal && e.target === modal) closeModal(); });

  // 行イベント（タップ・クリック）
  const TAP_MAX_MOVE = 10, TAP_MAX_TIME = 500;
  function attachRowHandlers(){
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
      });

      row.addEventListener("click", ()=>{ if (row.style.display!=="none") openModalForRow(row); });
    });
  }
  attachRowHandlers();

  // ===== 初期描画 =====
  filterTable();
});