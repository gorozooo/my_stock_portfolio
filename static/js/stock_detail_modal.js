/* =========================================================
   詳細モーダル（概要 + 価格 + 指標）
   - ページ拡大/縮小はモーダル表示中のみ“完全禁止”
   - チャート内はピンチズーム / 1本指ドラッグで上下左右パン
   - ローソク + MA20/50 + ATR(14)±1.5 + フラクタル + スイング高安＆レンジ帯
   - ★Closeのみ返る場合でも描画できるようにOHLCへ正規化（フォールバック）
   ========================================================= */
(function () {
  const mountId = "detail-modal-mount";

  /* ---------- utils ---------- */
  const toNum = (v, d = 0) => {
    if (v === null || v === undefined) return d;
    const n = Number(String(v).replace(/[^\d.-]/g, ""));
    return Number.isFinite(n) ? n : d;
  };
  const yen = (n) => "¥" + Math.round(toNum(n)).toLocaleString();
  const num = (n) => toNum(n).toLocaleString();

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

  /* ---------- ページズーム完全禁止（モーダル中のみ） ---------- */
  let originalViewport = null;
  let zoomGuardsOn = false;
  let onGestureStart, onGestureChange, onGestureEnd, onTouchMoveZoom, onDblClick;

  function ensureViewportMeta() {
    let meta = document.querySelector('meta[name="viewport"]');
    if (!meta) {
      meta = document.createElement('meta');
      meta.name = 'viewport';
      meta.content = 'width=device-width, initial-scale=1';
      document.head.appendChild(meta);
    }
    return meta;
  }

  function togglePageZoom(disable) {
    const meta = ensureViewportMeta();
    if (disable) {
      if (originalViewport === null) originalViewport = meta.getAttribute("content") || "";
      meta.setAttribute("content", "width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no");
      installZoomGuards();
    } else {
      removeZoomGuards();
      if (originalViewport !== null) {
        meta.setAttribute("content", originalViewport);
        originalViewport = null;
      }
    }
  }

  function installZoomGuards() {
    if (zoomGuardsOn) return;
    zoomGuardsOn = true;

    onGestureStart = (e) => { e.preventDefault(); };
    onGestureChange = (e) => { e.preventDefault(); };
    onGestureEnd   = (e) => { e.preventDefault(); };
    onTouchMoveZoom = (e) => {
      if (typeof e.scale === "number" && e.scale !== 1) e.preventDefault();
    };
    onDblClick = (e) => { e.preventDefault(); };

    document.addEventListener("gesturestart",  onGestureStart, {passive:false});
    document.addEventListener("gesturechange", onGestureChange, {passive:false});
    document.addEventListener("gestureend",    onGestureEnd,   {passive:false});
    document.addEventListener("touchmove",     onTouchMoveZoom, {passive:false});
    document.addEventListener("dblclick",      onDblClick, {passive:false});
  }

  function removeZoomGuards() {
    if (!zoomGuardsOn) return;
    zoomGuardsOn = false;

    document.removeEventListener("gesturestart",  onGestureStart, {passive:false});
    document.removeEventListener("gesturechange", onGestureChange, {passive:false});
    document.removeEventListener("gestureend",    onGestureEnd,   {passive:false});
    document.removeEventListener("touchmove",     onTouchMoveZoom, {passive:false});
    document.removeEventListener("dblclick",      onDblClick, {passive:false});
  }

  function closeDetail() {
    togglePageZoom(false);
    const m = document.getElementById(mountId);
    if (m) m.innerHTML = "";
    document.removeEventListener("keydown", escCloseOnce);
    document.body.classList.add("hide-legacy-modals");
  }

  // カード現在株価
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

  /* ===================== 価格タブ：データ取得 ===================== */
  async function fetchPrice(stockId, period) {
    const url = new URL(`/stocks/${stockId}/price.json`, window.location.origin);
    url.searchParams.set("period", period);
    const res = await fetch(url.toString(), { credentials: "same-origin" });
    if (!res.ok) throw new Error("価格データの取得に失敗しました");
    return await res.json();
  }

  /* ===================== テクニカル（凄腕仕様） ===================== */
  function calcATR14(series) {
    const TR = [];
    for (let i = 0; i < series.length; i++) {
      const h = toNum(series[i].h), l = toNum(series[i].l);
      const pc = i > 0 ? toNum(series[i-1].c) : h;
      TR.push(Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc)));
    }
    const n = Math.min(14, TR.length);
    const atr = n ? TR.slice(-n).reduce((a,b)=>a+b,0)/n : 0;
    return atr;
  }
  function calcSMA(series, len, key="c") {
    const out = new Array(series.length).fill(null);
    let sum = 0, q = [];
    for (let i = 0; i < series.length; i++) {
      const v = toNum(series[i][key]);
      q.push(v); sum += v;
      if (q.length > len) sum -= q.shift();
      if (q.length === len) out[i] = sum / len;
    }
    return out;
  }
  function findFractals(series) {
    const up = [], dn = [];
    for (let i = 2; i < series.length - 2; i++) {
      const h = toNum(series[i].h), l = toNum(series[i].l);
      const condUp = h > toNum(series[i-1].h) && h > toNum(series[i-2].h)
                  && h > toNum(series[i+1].h) && h > toNum(series[i+2].h);
      const condDn = l < toNum(series[i-1].l) && l < toNum(series[i-2].l)
                  && l < toNum(series[i+1].l) && l < toNum(series[i+2].l);
      if (condUp) up.push({ i, price: h });
      if (condDn) dn.push({ i, price: l });
    }
    return { up, dn };
  }
  function findRecentSwings(series, maxCount = 2) {
    const L = series.length; if (L < 7) return { highs: [], lows: [] };
    const highs = [], lows = [], k = 3;
    const atr = calcATR14(series);
    const threshold = atr * 0.5;

    for (let i = k; i < L - k; i++) {
      const h = toNum(series[i].h), l = toNum(series[i].l);
      let isHigh = true, isLow = true;
      for (let j = 1; j <= k; j++) {
        if (!(h > toNum(series[i-j].h) && h > toNum(series[i+j].h))) isHigh = false;
        if (!(l < toNum(series[i-j].l) && l < toNum(series[i+j].l))) isLow = false;
        if (!isHigh && !isLow) break;
      }
      if (isHigh) {
        const neighbor = Math.max(toNum(series[i-1].h), toNum(series[i+1].h));
        if (h - neighbor >= threshold) highs.push({ index: i, price: h });
      }
      if (isLow) {
        const neighbor = Math.min(toNum(series[i-1].l), toNum(series[i+1].l));
        if (neighbor - l >= threshold) lows.push({ index: i, price: l });
      }
    }
    return { highs: highs.slice(-maxCount), lows: lows.slice(-maxCount) };
  }

  /* ===================== ズーム・パン状態（チャート毎） ===================== */
  function makeViewState(seriesLen) {
    return {
      xScale: 1,          // 1 = 全幅（>1 でズームイン）
      yScale: 1,          // 1 = 自動スケール（>1 で縦ズーム）
      xOffset: 0,         // 可視範囲の左端（データindex基準）
      yOffset: 0,         // 価格の平行移動（値）
      minXScale: 1,
      maxXScale: 15,
      minYScale: 1,
      maxYScale: 6,
      seriesLen
    };
  }
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }

  /* ===================== チャート描画（ズーム対応 + OHLCフォールバック） ===================== */
  function renderProChart(modal, data) {
    // 数値表示
    const lastEl = modal.querySelector("#price-last");
    const chgEl  = modal.querySelector("#price-chg");
    const h52El  = modal.querySelector("#price-52h");
    const l52El  = modal.querySelector("#price-52l");
    const haEl   = modal.querySelector("#price-allh");
    const laEl   = modal.querySelector("#price-alll");
    if (lastEl) lastEl.textContent = data.last_close ? yen(data.last_close) : "—";
    if (chgEl && data.prev_close) {
      const chg = Math.round(data.change || 0).toLocaleString();
      const pct = Number(data.change_pct || 0).toFixed(2);
      chgEl.textContent = `${(data.change >= 0 ? "+" : "")}${chg} / ${pct}%`;
    } else if (chgEl) chgEl.textContent = "—";
    if (h52El) h52El.textContent = data.high_52w ? yen(data.high_52w) : "—";
    if (l52El) l52El.textContent = data.low_52w  ? yen(data.low_52w)  : "—";
    if (haEl)  haEl.textContent  = data.high_all ? yen(data.high_all) : "—";
    if (laEl)  laEl.textContent  = data.low_all  ? yen(data.low_all)  : "—";

    const cvs = modal.querySelector("#price-canvas");
    if (!cvs) return;
    const ctx = cvs.getContext("2d");

    // キャンバス内ジェスチャを全許可
    cvs.style.touchAction = "none";
    if (!cvs.style.width)  cvs.style.width  = "100%";
    if (!cvs.style.height) cvs.style.height = "180px";

    // DPR
    const Wcss = cvs.clientWidth  || cvs.width  || 600;
    const Hcss = cvs.clientHeight || cvs.height || 180;
    const dpr = window.devicePixelRatio || 1;
    cvs.width  = Math.floor(Wcss * dpr);
    cvs.height = Math.floor(Hcss * dpr);
    ctx.setTransform(1,0,0,1,0,0);
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, Wcss, Hcss);

    // ★OHLCフォールバック（Closeのみ→OHLC化）
    const raw = Array.isArray(data.series) ? data.series : [];
    if (raw.length < 2) return;
    const hasOHLC = raw[0] && "o" in raw[0] && "h" in raw[0] && "l" in raw[0] && "c" in raw[0];
    const series = hasOHLC
      ? raw
      : raw.map(p => { const c = toNum(p.c); return { t: p.t, o: c, h: c, l: c, c: c }; });

    // ビュー状態
    modal._view = modal._view || makeViewState(series.length);
    const view = modal._view;
    if (view.seriesLen !== series.length) Object.assign(view, makeViewState(series.length));

    const padX = 38, padY = 18;
    const innerW = Wcss - padX * 2;
    const innerH = Hcss - padY * 2;

    // 可視X（左端 index と可視幅）
    const span = series.length / view.xScale;
    view.xOffset = clamp(view.xOffset, 0, Math.max(0, series.length - span));
    const xStart = view.xOffset;
    const xEnd   = xStart + span;
    const iStart = Math.floor(xStart);
    const iEnd   = Math.min(series.length - 1, Math.ceil(xEnd));

    // 可視Y（可視区間の高安）
    let minY = +Infinity, maxY = -Infinity;
    for (let i = iStart; i <= iEnd; i++) {
      const h = toNum(series[i].h), l = toNum(series[i].l);
      if (h > maxY) maxY = h;
      if (l < minY) minY = l;
    }
    const padRate = 0.06;
    const baseRange = (maxY - minY) || 1;
    minY -= baseRange * padRate;
    maxY += baseRange * padRate;

    // 縦ズーム＆オフセット
    if (view.yScale > 1) {
      const mid = (minY + maxY) / 2 + view.yOffset;
      const half = (maxY - minY) / 2 / view.yScale;
      minY = mid - half; maxY = mid + half;
    } else if (view.yOffset !== 0) {
      minY += view.yOffset; maxY += view.yOffset;
    }

    modal._lastYRange = (maxY - minY) || 1;

    const xAt = (idx) => {
      const frac = (idx - xStart) / Math.max(1e-9, (xEnd - xStart));
      return padX + frac * innerW;
    };
    const yAt = (v) => padY + innerH * (1 - (v - minY) / Math.max(1e-9, (maxY - minY)));

    // 背景グリッド
    ctx.save();
    ctx.strokeStyle = "rgba(255,255,255,0.06)";
    ctx.lineWidth = 1;
    const marks = 4;
    for (let m = 0; m <= marks; m++) {
      const y = padY + innerH * (m/marks);
      ctx.beginPath(); ctx.moveTo(padX, y); ctx.lineTo(Wcss - padX, y); ctx.stroke();
    }
    ctx.restore();

    // ===== テク計算 =====
    const atr = calcATR14(series);
    const sma20 = calcSMA(series, 20, "c");
    const sma50 = calcSMA(series, 50, "c");
    const fract = findFractals(series);
    const swings = findRecentSwings(series, 2);

    // ===== ローソク =====
    const step = innerW / Math.max(1, (xEnd - xStart));
    const bodyW = Math.max(3, step * 0.65);
    for (let i = iStart; i <= iEnd; i++) {
      const p = series[i];
      const o = toNum(p.o), h = toNum(p.h), l = toNum(p.l), c = toNum(p.c);
      const cx = xAt(i);
      const yO = yAt(o), yH = yAt(h), yL = yAt(l), yC = yAt(c);
      const isBull = c >= o;
      const col = isBull ? "rgba(244,67,54,1)" : "rgba(76,175,80,1)";
      const colW = isBull ? "rgba(244,67,54,0.9)" : "rgba(76,175,80,0.9)";
      ctx.strokeStyle = colW; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(cx, yH); ctx.lineTo(cx, yL); ctx.stroke();
      ctx.fillStyle = col;
      const yTop = Math.min(yO, yC), yBot = Math.max(yO, yC);
      ctx.fillRect(cx - bodyW/2, yTop, bodyW, Math.max(1, yBot - yTop));
    }

    // ===== MA20/50 =====
    function drawLineFromArray(arr, color, width=1.5) {
      ctx.save();
      ctx.strokeStyle = color; ctx.lineWidth = width;
      ctx.beginPath();
      let started = false;
      for (let i = iStart; i <= iEnd; i++) {
        const v = arr[i]; if (v == null) continue;
        const x = xAt(i), y = yAt(v);
        if (!started) { ctx.moveTo(x, y); started = true; }
        else ctx.lineTo(x, y);
      }
      ctx.stroke();
      ctx.restore();
    }
    drawLineFromArray(sma20, "rgba(0,200,255,0.9)", 1.5);
    drawLineFromArray(sma50, "rgba(255,215,0,0.9)", 1.5);

    // ===== ATRバンド（終値±1.5*ATR） =====
    const mul = 1.5;
    ctx.save();
    ctx.strokeStyle = "rgba(255,255,255,0.25)";
    ctx.setLineDash([4,3]);
    ctx.beginPath();
    let st=false;
    for (let i = iStart; i <= iEnd; i++) {
      const c = toNum(series[i].c);
      const y = yAt(c + atr*mul);
      const x = xAt(i);
      if (!st){ ctx.moveTo(x,y); st=true; } else ctx.lineTo(x,y);
    }
    ctx.stroke();
    ctx.beginPath();
    st=false;
    for (let i = iStart; i <= iEnd; i++) {
      const c = toNum(series[i].c);
      const y = yAt(c - atr*mul);
      const x = xAt(i);
      if (!st){ ctx.moveTo(x,y); st=true; } else ctx.lineTo(x,y);
    }
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.restore();

    // ===== フラクタル（▲/▼） =====
    ctx.save();
    ctx.font = "11px system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    fract.up.forEach(f => {
      if (f.i < iStart || f.i > iEnd) return;
      const x = xAt(f.i), y = yAt(f.price) - 10;
      ctx.fillStyle = "rgba(255,215,0,0.95)";
      ctx.fillText("▲", x, y);
    });
    fract.dn.forEach(f => {
      if (f.i < iStart || f.i > iEnd) return;
      const x = xAt(f.i), y = yAt(f.price) + 10;
      ctx.fillStyle = "rgba(0,200,255,0.95)";
      ctx.fillText("▼", x, y);
    });
    ctx.restore();

    // ===== スイング高安（水平線+レンジ帯） =====
    (function drawSwings(){
      const highs = swings.highs, lows = swings.lows;
      const drawLine = (price, color, strong=false, label) => {
        const y = yAt(price);
        ctx.save();
        ctx.setLineDash(strong ? [6,3] : [5,5]);
        ctx.strokeStyle = color; ctx.lineWidth = strong ? 1.6 : 1.2;
        ctx.beginPath(); ctx.moveTo(padX, y); ctx.lineTo(Wcss - padX, y); ctx.stroke();
        ctx.setLineDash([]);
        const text = `${label} ${Math.round(price).toLocaleString()}`;
        const pad = 4, th = 14;
        ctx.font = "10px system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif";
        const tw = ctx.measureText(text).width;
        const bx = Wcss - padX - tw - pad*2;
        const by = Math.max(padY, Math.min(Hcss - padY - th, y - th/2));
        ctx.fillStyle = "rgba(18,18,24,0.9)";
        ctx.strokeStyle = color; ctx.lineWidth = 1;
        if (ctx.roundRect){ ctx.beginPath(); ctx.roundRect(bx,by,tw+pad*2,th,4); ctx.fill(); ctx.stroke(); }
        else { ctx.fillRect(bx,by,tw+pad*2,th); ctx.strokeRect(bx,by,tw+pad*2,th); }
        ctx.fillStyle = color; ctx.textAlign="left"; ctx.textBaseline="middle";
        ctx.fillText(text, bx+pad, by+th/2);
        ctx.restore();
      };
      const hi1 = highs[highs.length-1], hi2 = highs[highs.length-2];
      const lo1 = lows[lows.length-1],  lo2 = lows[lows.length-2];
      if (hi1) drawLine(hi1.price, "rgba(255,215,0,0.95)", true, "スイング高値#1");
      if (hi2) drawLine(hi2.price, "rgba(255,215,0,0.55)", false,"スイング高値#2");
      if (lo1) drawLine(lo1.price, "rgba(0,200,255,0.95)", true, "スイング安値#1");
      if (lo2) drawLine(lo2.price, "rgba(0,200,255,0.55)", false,"スイング安値#2");

      if (hi1 && lo1) {
        ctx.save();
        const yTop = yAt(Math.max(hi1.price, lo1.price));
        const yBot = yAt(Math.min(hi1.price, lo1.price));
        ctx.fillStyle = "rgba(0,200,255,0.08)";
        ctx.fillRect(padX, yTop, Wcss - padX*2, Math.max(1, yBot-yTop));
        ctx.restore();
      }
    })();

    // ===== 簡易Y軸ラベル =====
    (function drawAxes(){
      ctx.save();
      ctx.fillStyle = "rgba(255,255,255,0.7)";
      ctx.font = "10px system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif";
      ctx.textBaseline = "middle";
      const yTicks = 4;
      for (let m=0; m<=yTicks; m++){
        const v = minY + (maxY - minY)*(m/yTicks);
        const y = yAt(v);
        ctx.textAlign = "right";
        ctx.fillText(Math.round(v).toLocaleString(), Wcss - 2, y);
      }
      ctx.restore();
    })();

    // ===== ジェスチャー登録 =====
    bindGestures(modal, cvs, {padX, padY, innerW, innerH}, series);
  }

  /* ===================== ジェスチャー（スマホ優先） ===================== */
  function bindGestures(modal, canvas, geom, series) {
    if (canvas._bound) return;  // 二重登録防止
    canvas._bound = true;

    const view = modal._view;

    // 1本指ドラッグ = パン
    let dragging = false, lastX = 0, lastY = 0;
    canvas.addEventListener("touchstart", (e) => {
      if (e.touches.length === 1) {
        dragging = true;
        lastX = e.touches[0].clientX;
        lastY = e.touches[0].clientY;
      }
    }, {passive:false});

    canvas.addEventListener("touchmove", (e) => {
      if (dragging && e.touches.length === 1) {
        e.preventDefault(); // ← ページスクロール抑止
        const dx = e.touches[0].clientX - lastX;
        const dy = e.touches[0].clientY - lastY;
        lastX = e.touches[0].clientX;
        lastY = e.touches[0].clientY;

        const span = series.length / view.xScale;
        const idxPerPx = span / geom.innerW;
        view.xOffset = clamp(view.xOffset - dx * idxPerPx, 0, Math.max(0, series.length - span));

        const priceRange = (modal._lastYRange || 1);
        const pricePerPx = priceRange / geom.innerH;
        view.yOffset += dy * pricePerPx;

        if (modal._priceLastData) renderProChart(modal, modal._priceLastData);
      }
    }, {passive:false});

    canvas.addEventListener("touchend", () => { dragging = false; }, {passive:false});

    // 2本指ピンチ = ズーム
    let pinchStartDist = 0, pinchStartXScale = 1, pinchStartYScale = 1;
    canvas.addEventListener("touchstart", (e) => {
      if (e.touches.length === 2) {
        const [a,b] = e.touches;
        pinchStartDist = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
        pinchStartXScale = view.xScale;
        pinchStartYScale = view.yScale;
      }
    }, {passive:false});

    canvas.addEventListener("touchmove", (e) => {
      if (e.touches.length === 2 && pinchStartDist > 0) {
        e.preventDefault(); // ← ページ側ピンチズームを封じる
        const [a,b] = e.touches;
        const dist = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
        const ratio = dist / (pinchStartDist || 1);

        view.xScale = clamp(pinchStartXScale * ratio, view.minXScale, view.maxXScale);
        view.yScale = clamp(pinchStartYScale * ratio, view.minYScale, view.maxYScale);

        const span = series.length / view.xScale;
        view.xOffset = clamp(view.xOffset, 0, Math.max(0, series.length - span));

        if (modal._priceLastData) renderProChart(modal, modal._priceLastData);
      }
    }, {passive:false});

    canvas.addEventListener("touchend", () => { pinchStartDist = 0; }, {passive:false});

    // ダブルタップ = リセット（ブラウザのダブルタップズームは抑止済み）
    let lastTap = 0;
    canvas.addEventListener("touchend", (e) => {
      const now = Date.now();
      if (now - lastTap < 250) {
        e.preventDefault();
        Object.assign(view, makeViewState(series.length));
        modal._lastYRange = null;
        if (modal._priceLastData) renderProChart(modal, modal._priceLastData);
      }
      lastTap = now;
    }, {passive:false});

    // マウス：ドラッグパン & ホイールズーム
    let mouseDrag=false, mx=0, my=0;
    canvas.addEventListener("mousedown", e=>{ mouseDrag=true; mx=e.clientX; my=e.clientY; });
    window.addEventListener("mouseup", ()=> mouseDrag=false);
    canvas.addEventListener("mousemove", e=>{
      if (!mouseDrag) return;
      const dx = e.clientX - mx, dy = e.clientY - my;
      mx = e.clientX; my = e.clientY;
      const span = series.length / view.xScale;
      const idxPerPx = span / geom.innerW;
      view.xOffset = clamp(view.xOffset - dx * idxPerPx, 0, Math.max(0, series.length - span));
      const priceRange = (modal._lastYRange || 1);
      const pricePerPx = priceRange / geom.innerH;
      view.yOffset += dy * pricePerPx;
      if (modal._priceLastData) renderProChart(modal, modal._priceLastData);
    });
    canvas.addEventListener("wheel", e=>{
      e.preventDefault();
      const factor = Math.exp(-e.deltaY * 0.0015);
      view.xScale = clamp(view.xScale * factor, view.minXScale, view.maxXScale);
      view.yScale = clamp(view.yScale * factor, view.minYScale, view.maxYScale);
      const span = series.length / view.xScale;
      view.xOffset = clamp(view.xOffset, 0, Math.max(0, series.length - span));
      if (modal._priceLastData) renderProChart(modal, modal._priceLastData);
    }, {passive:false});
  }

  /* ===================== 価格データロード（期間キャッシュ） ===================== */
  async function loadPriceTab(modal, stockId, period="1M") {
    modal._priceCache = modal._priceCache || {};
    if (!modal._priceCache[period]) modal._priceCache[period] = fetchPrice(stockId, period);
    const data = await modal._priceCache[period];
    modal._priceLastData = data;

    // 初期Y幅（パン量算出に使用）
    if (Array.isArray(data.series) && data.series.length >= 2) {
      const s = data.series;
      let minY = +Infinity, maxY = -Infinity;
      for (const p of s) { const h=toNum(p.h ?? p.c), l=toNum(p.l ?? p.c); if(h>maxY)maxY=h; if(l<minY)minY=l; }
      modal._lastYRange = (maxY - minY) || 1;
    }
    renderProChart(modal, data);
  }

  /* ===================== 指標タブ ===================== */
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
    } else setText("#fd-mcap", "");
    if (d.dividend_yield_pct != null) setText("#fd-div", Number(d.dividend_yield_pct).toFixed(2) + "%");
    else setText("#fd-div", "");
    if (d.dividend_per_share != null) setText("#fd-dps", yen(d.dividend_per_share));
    else setText("#fd-dps", "");
    setText("#fd-updated", d.updated_at ? d.updated_at.replace("T"," ").slice(0,19) : "");

    modal.dataset.fundLoaded = "1";
  }

  /* ===================== モーダル起動 ===================== */
  async function openDetail(stockId, cardEl) {
    if (!stockId) return;
    const mount = ensureMount();

    removeLegacyModals();
    document.body.classList.add("hide-legacy-modals");

    // ページ拡大/縮小を完全禁止（モーダル中のみ）
    togglePageZoom(true);

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
              const period = activeChip?.dataset?.range || "1M";
              await loadPriceTab(modal, stockId, period);
            } else if (name === "fundamental") {
              await loadFundamentalTab(modal, stockId, cardCp);
            }
          } catch (e) { console.error(e); }
        });
      });

      // 期間チップ
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
          // 期間変更時はビューをリセット（見失い防止）
          modal._view = null;
          await loadPriceTab(modal, stockId, period);
        });
      }

      // ★価格タブを初回も確実にロード（レイアウト安定後に）
      const pricePanel = modal.querySelector('.detail-panel[data-panel="price"]');
      if (pricePanel) {
        const activeChip = modal.querySelector(".price-range-chips .chip.is-active");
        const period = activeChip?.dataset?.range || "1M";
        setTimeout(() => loadPriceTab(modal, stockId, period), 0);
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

      // 概要：確定値
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

  /* ===================== 起動 ===================== */
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