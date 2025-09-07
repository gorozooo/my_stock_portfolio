/* 詳細モーダル（概要 + 価格 + 指標）
   - 概要: /overview.json（from_card_current付き）で確定値
   - 価格: /price.json?period= を取得 → Canvas 描画（ローソク足 + スイング高安2本 + レンジ帯）
           ＋ 凄腕トレーダー仕様（期間別フラクタル/ATRしきい値）
           ＋ マウス/タッチの拡大縮小・ドラッグでパン
   - 指標: /fundamental.json を lazy-load（配当利回り・DPS含む）
*/
(function () {
  const mountId = "detail-modal-mount";

  // ========= 共通ユーティリティ =========
  const toNum = (v, d = 0) => {
    if (v === null || v === undefined) return d;
    const n = Number(String(v).replace(/[^\d.-]/g, ""));
    return Number.isFinite(n) ? n : d;
  };
  const yen = (n) => "¥" + Math.round(toNum(n)).toLocaleString();
  const num = (n) => toNum(n).toLocaleString();

  // ========= 概要 =========
  function calcOverview({ shares, unit_price, current_price, total_cost, position }) {
    const s = Math.max(0, toNum(shares));
    const up = Math.max(0, toNum(unit_price));
    const cp = Math.max(0, toNum(current_price));
    const tc = Math.max(0, toNum(total_cost) || s * up);
    const mv = cp * s;
    const pl = position === "売り" ? (up - cp) * s : mv - tc;
    return { market_value: mv, profit_loss: pl, total_cost: tc };
  }

  function optimisticOverviewHTML(d) {
    const { market_value, profit_loss } = calcOverview(d);
    const plClass = profit_loss >= 0 ? "pos" : "neg";
    return `
      <div class="overview-grid">
        <div class="ov-item"><div class="ov-k">証券会社</div><div class="ov-v">${d.broker || "—"}</div></div>
        <div class="ov-item"><div class="ov-k">口座区分</div><div class="ov-v">${d.account_type || "—"}</div></div>
        <div class="ov-item"><div class="ov-k">保有株数</div><div class="ov-v">${num(d.shares)} 株</div></div>
        <div class="ov-item"><div class="ov-k">ポジション</div><div class="ov-v">${d.position || "—"}</div></div>
        <div class="ov-item"><div class="ov-k">取得単価</div><div class="ov-v">${yen(d.unit_price)}</div></div>
        <div class="ov-item"><div class="ov-k">現在株価</div><div class="ov-v">${yen(d.current_price)}</div></div>
        <div class="ov-item"><div class="ov-k">取得額</div><div class="ov-v">${yen(d.total_cost)}</div></div>
        <div class="ov-item"><div class="ov-k">評価額</div><div class="ov-v">${yen(market_value)}</div></div>
        <div class="ov-item"><div class="ov-k">評価損益</div><div class="ov-v ${plClass}">${yen(profit_loss)}</div></div>
        <div class="ov-item"><div class="ov-k">購入日</div><div class="ov-v">${d.purchase_date || "—"}</div></div>
        <div class="ov-item" style="grid-column: 1 / -1;">
          <div class="ov-k">メモ</div>
          <div class="ov-v" style="white-space:pre-wrap;">${(d.note || "").trim() || "—"}</div>
        </div>
      </div>
    `;
  }

  function ensureMount() {
    let m = document.getElementById(mountId);
    if (!m) {
      m = document.createElement("div");
      m.id = mountId;
      document.body.appendChild(m);
    }
    return m;
  }

  function removeLegacyModals() {
    ["stock-modal", "edit-modal", "sell-modal"].forEach((id) => {
      const el = document.getElementById(id);
      if (el && el.parentNode) el.parentNode.removeChild(el);
    });
  }

  function escCloseOnce(e) { if (e.key === "Escape") closeDetail(); }
  function closeDetail() {
    const m = document.getElementById(mountId);
    if (m) m.innerHTML = "";
    document.removeEventListener("keydown", escCloseOnce);
    document.body.classList.add("hide-legacy-modals");
  }

  // ========= カードの現在株価取得 =========
  function getCardCurrentPrice(card) {
    let cp = toNum(card?.dataset?.current_price, 0);
    if (cp > 0) return cp;
    try {
      const rows = card.querySelectorAll(".stock-row");
      for (const r of rows) {
        const label = r.querySelector("span:first-child")?.textContent?.trim();
        if (label && label.indexOf("現在株価") !== -1) {
          const v = r.querySelector("span:last-child")?.textContent || "";
          const n = toNum(v, 0);
          if (n > 0) return n;
        }
      }
    } catch (_) {}
    return 0;
  }

  // ========= 凄腕トレーダー仕様：期間別パラメータ =========
  function getSwingParams(period){
    if ((period||"").toUpperCase()==="1M") return { k: 2, atrMul: 0.5 };
    if ((period||"").toUpperCase()==="3M") return { k: 3, atrMul: 0.65 };
    return { k: 4, atrMul: 0.85 }; // 1Y
  }

  function averageTrueRange(highs, lows, closes, period = 14){
    const trs = [];
    for (let i=1;i<closes.length;i++){
      const hl = highs[i] - lows[i];
      const hc = Math.abs(highs[i] - closes[i-1]);
      const lc = Math.abs(lows[i]  - closes[i-1]);
      trs.push(Math.max(hl,hc,lc));
    }
    if (!trs.length) return 0;
    const n = Math.min(period, trs.length);
    return trs.slice(-n).reduce((a,b)=>a+b,0)/n;
  }

  // 実用スイング抽出（フラクタル＋ATRしきい値）
  function findRecentSwings(series, period) {
    const L = series.length;
    if (L < 7) return { highs: [], lows: [] };
    const highs  = series.map(p => toNum(p.h));
    const lows   = series.map(p => toNum(p.l));
    const closes = series.map(p => toNum(p.c));

    const { k, atrMul } = getSwingParams(period);
    const atr = averageTrueRange(highs, lows, closes, 14);
    const threshold = atr * atrMul;

    const swingsHigh = [];
    const swingsLow  = [];
    for (let i=k;i<L-k;i++){
      const h = highs[i], l=lows[i];
      let isHigh=true, isLow=true;
      for (let j=1;j<=k;j++){
        if (!(h>=highs[i-j] && h>=highs[i+j])) isHigh=false;
        if (!(l<=lows[i-j]  && l<=lows[i+j])) isLow=false;
        if(!isHigh && !isLow) break;
      }
      if (isHigh){
        if (!swingsHigh.length || Math.abs(h - swingsHigh[swingsHigh.length-1].value) > threshold){
          swingsHigh.push({ index: i, value: h });
        }
      }
      if (isLow){
        if (!swingsLow.length || Math.abs(l - swingsLow[swingsLow.length-1].value) > threshold){
          swingsLow.push({ index: i, value: l });
        }
      }
    }
    return { highs: swingsHigh.slice(-2), lows: swingsLow.slice(-2) };
  }

  // ========= 価格取得 =========
  async function fetchPrice(stockId, period) {
    const url = new URL(`/stocks/${stockId}/price.json`, window.location.origin);
    url.searchParams.set("period", period);
    const res = await fetch(url.toString(), { credentials: "same-origin" });
    if (!res.ok) throw new Error("価格データの取得に失敗しました");
    return await res.json();
  }

  // ========= 軸ラベル =========
  function drawAxes(ctx, W, H, padX, padY, minY, maxY, leftDate, midDate, rightDate){
    ctx.save();
    ctx.fillStyle = "rgba(255,255,255,0.7)";
    ctx.font = "10px system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif";
    // X
    ctx.textAlign = "center"; ctx.textBaseline = "top";
    if (leftDate)  ctx.fillText(leftDate,  padX, H - padY + 4);
    if (midDate)   ctx.fillText(midDate,   W/2,  H - padY + 4);
    if (rightDate) ctx.fillText(rightDate, W-padX, H - padY + 4);

    // Y: min / mid / max
    ctx.textAlign = "right"; ctx.textBaseline = "middle";
    const fmt = (v) => Math.round(v).toLocaleString();
    const innerH = H - padY*2;
    const yAt = (v) => padY + innerH*(1-(v-minY)/Math.max(1e-9,(maxY-minY)));
    [minY,(minY+maxY)/2,maxY].forEach(v=>{
      const y=yAt(v);
      ctx.fillText(fmt(v), W-2, y);
      ctx.strokeStyle="rgba(255,255,255,0.08)";
      ctx.beginPath(); ctx.moveTo(padX,y); ctx.lineTo(W-padX,y); ctx.stroke();
    });
    ctx.restore();
  }

  // ========= チャート状態（パン/ズーム） =========
  function initChartState(modal, series, period){
    const L = series.length;
    const defaults = { "1M": 30, "3M": 60, "1Y": 120 };
    const target = defaults[(period||"").toUpperCase()] || Math.min(120, L);
    const start = Math.max(0, L - target);
    const end   = L - 1;

    modal._chartState = {
      period,
      series,
      start,
      end,
      dragging: false,
      dragStartX: 0,
      dragStartRange: {start, end}
    };
  }
  function clamp(v,min,max){ return Math.max(min, Math.min(max, v)); }
  function windowSize(st){ return st.end - st.start + 1; }

  // ========= スイング線 + レンジ帯 =========
  function drawSwingHL(ctx, W, H, padX, padY, minY, maxY, visSeries, period){
    if (!visSeries || visSeries.length<5) return;

    const swings = findRecentSwings(visSeries, period);
    const innerH = H - padY*2;
    const yAt = (v) => padY + innerH*(1-(v-minY)/Math.max(1e-9,(maxY-minY)));

    const latestHigh = swings.highs[swings.highs.length-1];
    const latestLow  = swings.lows[swings.lows.length-1];
    if (latestHigh && latestLow){
      const yTop = yAt(Math.max(latestHigh.value, latestLow.value));
      const yBot = yAt(Math.min(latestHigh.value, latestLow.value));
      ctx.save();
      ctx.fillStyle = "rgba(0,200,255,0.08)";
      ctx.fillRect(padX, yTop, W-padX*2, Math.max(1,yBot-yTop));
      ctx.restore();
    }

    const drawLine = (price, color, label, strong=false)=>{
      const y = yAt(price);
      ctx.save();
      ctx.setLineDash(strong?[6,3]:[5,5]);
      ctx.strokeStyle=color; ctx.lineWidth=strong?1.6:1.2;
      ctx.beginPath(); ctx.moveTo(padX,y); ctx.lineTo(W-padX,y); ctx.stroke();
      ctx.setLineDash([]);

      const text = `${label} ${Math.round(price).toLocaleString()}`;
      const pad = 4, th=14;
      ctx.font="10px system-ui,-apple-system,'Segoe UI',Roboto,sans-serif";
      const tw = ctx.measureText(text).width;
      const bx = W - padX - tw - pad*2;
      const by = clamp(y - th/2, padY, H - padY - th);
      ctx.fillStyle="rgba(18,18,24,0.9)"; ctx.strokeStyle=color; ctx.lineWidth=1;
      if (ctx.roundRect){ ctx.beginPath(); ctx.roundRect(bx,by,tw+pad*2,th,4); ctx.fill(); ctx.stroke(); }
      else { ctx.fillRect(bx,by,tw+pad*2,th); ctx.strokeRect(bx,by,tw+pad*2,th); }
      ctx.fillStyle=color; ctx.textAlign="left"; ctx.textBaseline="middle";
      ctx.fillText(text, bx+pad, by+th/2);
      ctx.restore();
    };

    if (swings.highs.length>=1) drawLine(swings.highs[swings.highs.length-1].value, "rgba(255,215,0,0.95)", "スイング高値#1", true);
    if (swings.highs.length>=2) drawLine(swings.highs[swings.highs.length-2].value, "rgba(255,215,0,0.60)", "スイング高値#2", false);
    if (swings.lows.length>=1)  drawLine(swings.lows[swings.lows.length-1].value,  "rgba(0,200,255,0.95)", "スイング安値#1", true);
    if (swings.lows.length>=2)  drawLine(swings.lows[swings.lows.length-2].value,  "rgba(0,200,255,0.55)", "スイング安値#2", false);
  }

  // ========= 価格レンダリング（ローソク＋パン/ズーム） =========
  function renderPrice(modal, d, period) {
    // 上部数値
    const lastEl = modal.querySelector("#price-last");
    const chgEl  = modal.querySelector("#price-chg");
    const h52El  = modal.querySelector("#price-52h");
    const l52El  = modal.querySelector("#price-52l");
    const haEl   = modal.querySelector("#price-allh");
    const laEl   = modal.querySelector("#price-alll");

    if (lastEl) lastEl.textContent = d.last_close ? yen(d.last_close) : "—";
    if (chgEl && d.prev_close) {
      const chg = Math.round(d.change || 0).toLocaleString();
      const pct = Number(d.change_pct || 0).toFixed(2);
      chgEl.textContent = `${(d.change >= 0 ? "+" : "")}${chg} / ${pct}%`;
    } else if (chgEl) {
      chgEl.textContent = "—";
    }
    if (h52El) h52El.textContent = d.high_52w ? yen(d.high_52w) : "—";
    if (l52El) l52El.textContent = d.low_52w  ? yen(d.low_52w)  : "—";
    if (haEl)  haEl.textContent  = d.high_all ? yen(d.high_all) : "—";
    if (laEl)  laEl.textContent  = d.low_all  ? yen(d.low_all)  : "—";

    // Canvas
    const cvs = modal.querySelector("#price-canvas");
    if (!cvs) return;
    const series = Array.isArray(d.series) ? d.series : [];
    if (series.length < 2) return;

    // チャート状態（初期化 or 更新）
    if (!modal._chartState || modal._chartState.series !== series) {
      initChartState(modal, series, period);
    } else {
      modal._chartState.period = period; // 期間更新
    }
    const st = modal._chartState;

    // DPR セット
    const ctx = cvs.getContext("2d");
    const Wcss = cvs.clientWidth, Hcss = cvs.clientHeight;
    const dpr = window.devicePixelRatio || 1;
    cvs.width = Math.floor(Wcss*dpr); cvs.height = Math.floor(Hcss*dpr);
    ctx.setTransform(1,0,0,1,0,0);
    ctx.scale(dpr,dpr);
    ctx.clearRect(0,0,Wcss,Hcss);

    // 可視範囲
    const start = clamp(Math.floor(st.start), 0, series.length-2);
    const end   = clamp(Math.floor(st.end),   start+1, series.length-1);
    const vis   = series.slice(start, end+1);
    const hasOHLC = ["o","h","l","c"].every(k => vis[0] && (k in vis[0]));

    // スケール
    const padX = 32, padY = 18;
    const innerW = Wcss - padX*2;
    const innerH = Hcss - padY*2;

    const valsForScale = hasOHLC
      ? vis.flatMap(p => [toNum(p.h), toNum(p.l)])
      : vis.map(p => toNum(p.c));
    const minY = Math.min(...valsForScale);
    const maxY = Math.max(...valsForScale);

    const xStep = innerW / Math.max(1, vis.length - 1);
    const xAt = (i) => padX + xStep * i;
    const yAt = (v) => padY + innerH * (1 - (v - minY) / Math.max(1e-9, (maxY - minY)));

    // ローソク／終値ライン
    if (hasOHLC) {
      const bodyW = Math.max(3, xStep * 0.6);
      vis.forEach((p, i) => {
        const o = toNum(p.o), h = toNum(p.h), l = toNum(p.l), c = toNum(p.c);
        const cx = xAt(i);
        const yO = yAt(o), yH = yAt(h), yL = yAt(l), yC = yAt(c);
        const yTop = Math.min(yO, yC);
        const yBot = Math.max(yO, yC);
        const isBull = c >= o;
        const col = isBull ? "rgba(244,67,54,1)" : "rgba(76,175,80,1)";
        const colWick = isBull ? "rgba(244,67,54,0.9)" : "rgba(76,175,80,0.9)";
        ctx.strokeStyle = colWick; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(cx, yH); ctx.lineTo(cx, yL); ctx.stroke();
        ctx.fillStyle = col;
        ctx.fillRect(cx - bodyW / 2, yTop, bodyW, Math.max(1, yBot - yTop));
      });
    } else {
      const ys = vis.map(p => toNum(p.c));
      // エリア
      const grad = ctx.createLinearGradient(0, padY, 0, padY + innerH);
      grad.addColorStop(0, "rgba(0,200,255,0.35)");
      grad.addColorStop(1, "rgba(0,200,255,0.00)");
      ctx.beginPath();
      ctx.moveTo(xAt(0), yAt(ys[0]));
      ys.forEach((v, i) => ctx.lineTo(xAt(i), yAt(v)));
      ctx.lineTo(xAt(ys.length - 1), padY + innerH);
      ctx.lineTo(xAt(0), padY + innerH);
      ctx.closePath();
      ctx.fillStyle = grad; ctx.fill();
      // ライン
      ctx.beginPath();
      ctx.moveTo(xAt(0), yAt(ys[0]));
      ys.forEach((v, i) => ctx.lineTo(xAt(i), yAt(v)));
      ctx.strokeStyle = "rgba(0,200,255,0.85)";
      ctx.lineWidth = 2; ctx.stroke();
    }

    // 軸
    const leftDate  = vis[0]?.t || "";
    const midDate   = vis[Math.floor(vis.length/2)]?.t || "";
    const rightDate = vis[vis.length-1]?.t || "";
    drawAxes(ctx, Wcss, Hcss, padX, padY, minY, maxY, leftDate, midDate, rightDate);

    // スイング線 + レンジ帯
    drawSwingHL(ctx, Wcss, Hcss, padX, padY, minY, maxY, vis, period);

    // ====== ここから 拡大・縮小（ホイール/ピンチ）＆ ドラッグでパン ======
    if (!modal._chartBound) {
      const onWheel = (ev) => {
        ev.preventDefault();
        if (!modal._chartState) return;
        const rect = cvs.getBoundingClientRect();
        const x = ev.clientX - rect.left;
        // カーソル下のインデックス（可視範囲内）
        const idxLocal = clamp(Math.round((x - padX) / Math.max(1e-6, xStep)), 0, vis.length-1);
        const idxGlobal = start + idxLocal;

        // ズーム量（deltaYで倍率）
        const zoomIn = ev.deltaY < 0 ? 1 : -1;
        const factor = zoomIn > 0 ? 0.85 : 1.15;

        const curSize = windowSize(st);
        const newSize = clamp(Math.round(curSize * factor), 20, series.length); // 最小20本
        const leftRatio = (idxGlobal - st.start) / curSize;
        let newStart = Math.round(idxGlobal - newSize * leftRatio);
        let newEnd   = newStart + newSize - 1;
        if (newStart < 0) { newStart = 0; newEnd = newSize - 1; }
        if (newEnd > series.length-1) { newEnd = series.length-1; newStart = newEnd - newSize + 1; }

        st.start = newStart; st.end = newEnd;
        renderPrice(modal, d, period);
      };

      const onDown = (ev) => {
        ev.preventDefault();
        st.dragging = true;
        st.dragStartX = (ev.touches ? ev.touches[0].clientX : ev.clientX);
        st.dragStartRange = { start: st.start, end: st.end };
      };
      const onMove = (ev) => {
        if (!st.dragging) return;
        const curX = (ev.touches ? ev.touches[0].clientX : ev.clientX);
        const rect = cvs.getBoundingClientRect();
        const px  = (curX - st.dragStartX);
        const barsPerPx = windowSize(st) / Math.max(1, rect.width - padX*2);
        const shift = Math.round(px * barsPerPx); // 右へドラッグ→正

        let newStart = st.dragStartRange.start - shift;
        let newEnd   = st.dragStartRange.end   - shift;
        const size   = newEnd - newStart + 1;
        if (newStart < 0) { newStart = 0; newEnd = size - 1; }
        if (newEnd > series.length-1) { newEnd = series.length-1; newStart = newEnd - size + 1; }

        st.start = newStart; st.end = newEnd;
        renderPrice(modal, d, period);
      };
      const onUp = () => { st.dragging = false; };

      cvs.addEventListener("wheel", onWheel, { passive:false });
      cvs.addEventListener("mousedown", onDown);
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
      // タッチ
      cvs.addEventListener("touchstart", onDown, { passive:false });
      cvs.addEventListener("touchmove",  onMove, { passive:false });
      cvs.addEventListener("touchend",   onUp,   { passive:false });

      modal._chartBound = true;
    }
  }

  // ========= 価格タブロード（キャッシュ + 期間） =========
  async function loadPriceTab(modal, stockId, period = "1M") {
    modal._priceCache = modal._priceCache || {};
    if (!modal._priceCache[period]) {
      modal._priceCache[period] = fetchPrice(stockId, period);
    }
    const data = await modal._priceCache[period];
    renderPrice(modal, data, period);
  }

  // ========= 指標タブ =========
  async function loadFundamentalTab(modal, stockId, cardCp) {
    if (modal.dataset.fundLoaded === "1") return;

    const url = new URL(`/stocks/${stockId}/fundamental.json`, window.location.origin);
    if (cardCp && cardCp > 0) url.searchParams.set("from_card_current", String(cardCp));
    const res = await fetch(url.toString(), { credentials: "same-origin" });
    if (!res.ok) throw new Error("指標データの取得に失敗しました");
    const d = await res.json();

    const setText = (sel, valStr) => {
      const el = modal.querySelector(sel);
      if (!el) return;
      el.textContent = (valStr === null || valStr === undefined || valStr === "") ? "—" : String(valStr);
    };

    setText("#fd-per",  d.per  != null ? Number(d.per).toFixed(2)  : "");
    setText("#fd-pbr",  d.pbr  != null ? Number(d.pbr).toFixed(2)  : "");
    setText("#fd-eps",  d.eps  != null ? yen(d.eps)                : "");
    if (d.market_cap != null) {
      const mc = Number(d.market_cap);
      let disp = "—";
      if (mc >= 1e12) disp = (mc / 1e12).toFixed(2) + " 兆円";
      else if (mc >= 1e8) disp = (mc / 1e8).toFixed(2) + " 億円";
      else disp = yen(mc);
      setText("#fd-mcap", disp);
    } else {
      setText("#fd-mcap", "");
    }
    if (d.dividend_yield_pct != null) {
      const pct = Number(d.dividend_yield_pct);
      setText("#fd-div", pct.toFixed(2) + "%");
    } else {
      setText("#fd-div", "");
    }
    if (d.dividend_per_share != null) {
      setText("#fd-dps", yen(d.dividend_per_share));
    } else {
      setText("#fd-dps", "");
    }
    setText("#fd-updated", d.updated_at ? d.updated_at.replace("T", " ").slice(0, 19) : "");

    modal.dataset.fundLoaded = "1";
  }

  // ========= モーダル起動 =========
  async function openDetail(stockId, cardEl) {
    if (!stockId) return;
    const mount = ensureMount();

    removeLegacyModals();
    document.body.classList.add("hide-legacy-modals");

    const cardCp = getCardCurrentPrice(cardEl);
    const cardUp = toNum(cardEl?.dataset?.unit_price, 0);
    const cardShares = toNum(cardEl?.dataset?.shares, 0);
    const cardPosition = (cardEl?.dataset?.position || "買い");
    const optimisticCp = cardCp > 0 ? cardCp : cardUp;

    try {
      const htmlRes = await fetch(`/stocks/${stockId}/detail_fragment/`, { credentials: "same-origin" });
      if (!htmlRes.ok) throw new Error("モーダルの読み込みに失敗しました");
      const html = await htmlRes.text();

      mount.innerHTML = "";
      mount.innerHTML = html;

      const modal = mount.querySelector("#detail-modal");
      if (!modal) throw new Error("モーダルが生成できませんでした");

      // 閉じる
      modal.querySelectorAll("[data-dm-close]").forEach((el) => el.addEventListener("click", closeDetail));
      document.addEventListener("keydown", escCloseOnce);

      // タブ切替（価格/指標は lazy load）
      modal.querySelectorAll(".detail-tab").forEach((btn) => {
        btn.addEventListener("click", async () => {
          if (btn.disabled) return;
          const name = btn.getAttribute("data-tab");
          modal.querySelectorAll(".detail-tab").forEach((b) => b.classList.toggle("is-active", b === btn));
          modal.querySelectorAll(".detail-panel").forEach((p) =>
            p.classList.toggle("is-active", p.getAttribute("data-panel") === name)
          );

          try {
            if (name === "price") {
              const activeChip = modal.querySelector(".price-range-chips .chip.is-active");
              const period = (activeChip?.dataset?.range || "1M").toUpperCase();
              await loadPriceTab(modal, stockId, period);
            } else if (name === "fundamental") {
              await loadFundamentalTab(modal, stockId, cardCp);
            }
          } catch (e) {
            console.error(e);
          }
        });
      });

      // 期間チップ（1M/3M/1Y）
      const chipsWrap = modal.querySelector(".price-range-chips");
      if (chipsWrap) {
        chipsWrap.addEventListener("click", async (e) => {
          const btn = e.target.closest(".chip");
          if (!btn) return;
          const period = (btn.dataset.range || "1M").toUpperCase();
          chipsWrap.querySelectorAll(".chip").forEach(c => {
            c.classList.toggle("is-active", c === btn);
            c.setAttribute("aria-selected", c === btn ? "true" : "false");
          });
          try { await loadPriceTab(modal, stockId, period); } catch(err){ console.error(err); }
        });
      }

      // 概要：即時プレビュー
      const ovWrap = modal.querySelector('[data-panel="overview"]');
      if (ovWrap) {
        const optimistic = {
          broker: cardEl?.dataset?.broker || "",
          account_type: cardEl?.dataset?.account || "",
          position: cardPosition,
          shares: cardShares,
          unit_price: cardUp,
          current_price: optimisticCp,
          total_cost: cardShares * cardUp,
          purchase_date: "",
          note: ""
        };
        ovWrap.innerHTML = optimisticOverviewHTML(optimistic);
      }

      // 概要：確定値（カードの現在株価も渡す）
      const url = new URL(`/stocks/${stockId}/overview.json`, window.location.origin);
      if (cardCp > 0) url.searchParams.set("from_card_current", String(cardCp));
      const res = await fetch(url.toString(), { credentials: "same-origin" });
      if (!res.ok) throw new Error("概要データの取得に失敗しました");
      const d = await res.json();

      const fixed = { ...d };
      if (toNum(fixed.current_price, 0) <= 0 && cardCp > 0) fixed.current_price = cardCp;
      if (toNum(fixed.total_cost, 0) <= 0) fixed.total_cost = toNum(fixed.shares, 0) * toNum(fixed.unit_price, 0);
      if (ovWrap) ovWrap.innerHTML = optimisticOverviewHTML(fixed);
    } catch (err) {
      console.error(err);
      alert("詳細の読み込みでエラーが発生しました。時間をおいて再度お試しください。");
      closeDetail();
    }
  }

  // ========= 起動 =========
  document.addEventListener("DOMContentLoaded", () => {
    removeLegacyModals();
    document.body.classList.add("hide-legacy-modals");

    document.body.addEventListener("click", (e) => {
      const card = e.target.closest(".stock-card");
      if (!card) return;
      if (e.target.closest("a")) return;
      if (card.classList.contains("swiped")) return;

      const id = card.dataset.id;
      if (!id || id === "0") return;
      openDetail(id, card);
    });

    document.body.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" && e.key !== " ") return;
      const card = e.target.closest?.(".stock-card");
      if (!card) return;
      const id = card.dataset.id;
      if (!id || id === "0") return;
      e.preventDefault();
      openDetail(id, card);
    });
  });
})();