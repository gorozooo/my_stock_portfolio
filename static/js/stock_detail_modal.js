/* 詳細モーダル（概要 + 価格 + 指標）
   - 価格タブ: /price.json?period= を取得 → ローソク足 + スイング高安
   - ピンチズーム & ドラッグパン（1M/3M/1Y すべてで有効、期間切替後も維持）
*/
(function () {
  const mountId = "detail-modal-mount";

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
  function closeDetail() {
    const m = document.getElementById(mountId);
    if (m) m.innerHTML = "";
    document.removeEventListener("keydown", escCloseOnce);
    document.body.classList.add("hide-legacy-modals");
    // ページズームを完全禁止（モーダル外でも）=> ユーザー要望に合わせて常時禁止に変更
    // ※もし「モーダル中のみ禁止」に戻したい場合は、viewport タグを元に戻す処理をここへ
  }

  // --- カードから“現在株価”を取得（data属性 → テキスト救済） ---
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

  // ====== 価格API ======
  async function fetchPrice(stockId, period) {
    const url = new URL(`/stocks/${stockId}/price.json`, window.location.origin);
    url.searchParams.set("period", period);
    const res = await fetch(url.toString(), { credentials: "same-origin" });
    if (!res.ok) throw new Error("価格データの取得に失敗しました");
    return await res.json();
  }

  // ========= スイング検出（実用寄り：各2本まで） =========
  function findRecentSwings(series, hasOHLC, maxCount = 2) {
    const L = series.length;
    if (L < 7) return { highs: [], lows: [] };

    const highsArr = hasOHLC ? series.map(p => toNum(p.h)) : series.map(p => toNum(p.c));
    const lowsArr  = hasOHLC ? series.map(p => toNum(p.l)) : series.map(p => toNum(p.c));

    // しきい値（簡易ATR/SDでノイズ除去）
    let threshold = 0;
    if (hasOHLC) {
      const tr = [];
      for (let i = 0; i < L; i++) {
        const h = toNum(series[i].h), l = toNum(series[i].l);
        const cPrev = i > 0 ? toNum(series[i-1].c) : h;
        tr.push(Math.max(h - l, Math.abs(h - cPrev), Math.abs(l - cPrev)));
      }
      const n = Math.min(14, tr.length);
      const atr = tr.slice(-n).reduce((a,b)=>a+b,0) / n;
      threshold = atr * 0.5;
    } else {
      const cls = series.map(p => toNum(p.c));
      const n = Math.min(20, cls.length);
      const tail = cls.slice(-n);
      const m = tail.reduce((a,b)=>a+b,0) / n;
      const sd = Math.sqrt(tail.reduce((a,b)=>a+(b-m)*(b-m),0)/n);
      threshold = sd * 0.8;
    }

    const k = 3;
    const highs = [];
    const lows = [];
    for (let i = k; i < L - k; i++) {
      const h = highsArr[i], l = lowsArr[i];
      let isHigh = true, isLow = true;
      for (let j = 1; j <= k; j++) {
        if (!(h > highsArr[i-j] && h > highsArr[i+j])) isHigh = false;
        if (!(l < lowsArr[i-j] && l < lowsArr[i+j])) isLow = false;
        if (!isHigh && !isLow) break;
      }
      if (isHigh) {
        const neighbor = Math.max(highsArr[i-1], highsArr[i+1]);
        if (h - neighbor >= threshold) highs.push({ index: i, price: h });
      }
      if (isLow) {
        const neighbor = Math.min(lowsArr[i-1], lowsArr[i+1]);
        if (neighbor - l >= threshold) lows.push({ index: i, price: l });
      }
    }
    return { highs: highs.slice(-maxCount), lows: lows.slice(-maxCount) };
  }

  function recentWindowHL(series, hasOHLC) {
    const tail = series.slice(-Math.min(20, series.length));
    const highs = hasOHLC ? tail.map(p => toNum(p.h)) : tail.map(p => toNum(p.c));
    const lows  = hasOHLC ? tail.map(p => toNum(p.l)) : tail.map(p => toNum(p.c));
    if (!highs.length || !lows.length) return { highs: [], lows: [] };
    const hiVal = Math.max(...highs);
    const loVal = Math.min(...lows);
    return {
      highs: [{ index: series.length - tail.length + highs.indexOf(hiVal), price: hiVal }],
      lows:  [{ index: series.length - tail.length + lows.indexOf(loVal),  price: loVal  }],
    };
  }

  // ===== 描画（ズーム・パン対応） =====
  function renderPrice(modal, d) {
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

    const cvs = modal.querySelector("#price-canvas");
    const ovr = modal.querySelector("#price-overlay");
    if (!cvs || !ovr) return;

    // DPI調整
    const ctx = cvs.getContext("2d");
    const octx= ovr.getContext("2d");
    const Wcss = cvs.clientWidth;
    const Hcss = cvs.clientHeight;
    const dpr = window.devicePixelRatio || 1;
    for (const c of [cvs, ovr]) {
      c.width = Math.floor(Wcss * dpr);
      c.height= Math.floor(Hcss * dpr);
    }
    ctx.setTransform(1,0,0,1,0,0); ctx.scale(dpr, dpr);
    octx.setTransform(1,0,0,1,0,0); octx.scale(dpr, dpr);
    ctx.clearRect(0,0,Wcss,Hcss);
    octx.clearRect(0,0,Wcss,Hcss);

    const series = Array.isArray(d.series) ? d.series : [];
    if (series.length < 2) return;
    const hasOHLC = ["o","h","l","c"].every(k => series[0] && (k in series[0]));

    // ====== 表示ウィンドウ（ズーム/パン状態） ======
    // modal._chartState は期間ごとに保持
    modal._chartState = modal._chartState || {};
    const key = d.period || "1M";
    const st = (modal._chartState[key] ||= {});
    const fullXmin = 0, fullXmax = series.length - 1;

    // 初期化（期間が変わった or 未設定）→フル表示
    if (st.xMin === undefined || st.xMax === undefined || st.seriesLen !== series.length) {
      st.xMin = fullXmin;
      st.xMax = fullXmax;
      st.seriesLen = series.length;
      st.yAuto = true; // yはデフォ自動（ズームで固定に切替）
    }

    // yスケール（自動 or 手動）
    const x0i = Math.max(0, Math.floor(st.xMin));
    const x1i = Math.min(series.length-1, Math.ceil(st.xMax));
    const valsForScale = hasOHLC
      ? series.slice(x0i, x1i+1).flatMap(p => [toNum(p.h), toNum(p.l)])
      : series.slice(x0i, x1i+1).map(p => toNum(p.c));
    const yFullMin = Math.min(...(hasOHLC ? series.flatMap(p => [toNum(p.l)]) : series.map(p => toNum(p.c))));
    const yFullMax = Math.max(...(hasOHLC ? series.flatMap(p => [toNum(p.h)]) : series.map(p => toNum(p.c))));
    if (st.yAuto || st.yMin === undefined || st.yMax === undefined) {
      st.yMin = Math.min(...valsForScale);
      st.yMax = Math.max(...valsForScale);
      if (st.yMin === st.yMax) { st.yMin -= 1; st.yMax += 1; }
    }

    const padX = 28, padY = 18;
    const innerW = Wcss - padX * 2;
    const innerH = Hcss - padY * 2;

    const xAtIdx = (i) => {
      const t = (i - st.xMin) / Math.max(1e-9, (st.xMax - st.xMin));
      return padX + innerW * t;
    };
    const yAtVal = (v) => {
      const t = (v - st.yMin) / Math.max(1e-9, (st.yMax - st.yMin));
      return padY + innerH * (1 - t);
    };

    // 軸
    drawAxes(ctx=Wctx(ctx, Wcss, Hcss), Wcss, Hcss, padX, padY, st.yMin, st.yMax, [
      series[0].t, series[Math.floor(series.length/2)].t, series[series.length-1].t
    ]);

    // ローソク or ライン
    if (hasOHLC) {
      const bodyW = Math.max(3, innerW / Math.max(1, (st.xMax - st.xMin + 1)) * 0.6);
      for (let i = x0i; i <= x1i; i++) {
        const p = series[i];
        const o = toNum(p.o), h = toNum(p.h), l = toNum(p.l), c = toNum(p.c);
        const cx = xAtIdx(i);
        const yO = yAtVal(o), yH = yAtVal(h), yL = yAtVal(l), yC = yAtVal(c);
        const yTop = Math.min(yO, yC);
        const yBot = Math.max(yO, yC);
        const isBull = c >= o;
        const col = isBull ? "rgba(244,67,54,1)" : "rgba(76,175,80,1)";
        const colWick = isBull ? "rgba(244,67,54,0.9)" : "rgba(76,175,80,0.9)";

        // ヒゲ
        ctx.strokeStyle = colWick;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(cx, yH); ctx.lineTo(cx, yL);
        ctx.stroke();

        // 実体
        const hBody = Math.max(1, yBot - yTop);
        ctx.fillStyle = col;
        ctx.fillRect(cx - bodyW / 2, yTop, bodyW, hBody);
      }
    } else {
      const ys = series.slice(x0i, x1i+1).map(p => toNum(p.c));
      ctx.beginPath();
      ctx.moveTo(xAtIdx(x0i), yAtVal(ys[0]));
      for (let i = x0i+1; i <= x1i; i++) {
        ctx.lineTo(xAtIdx(i), yAtVal(ys[i-x0i]));
      }
      ctx.strokeStyle = "rgba(0,200,255,0.85)";
      ctx.lineWidth = 2;
      ctx.stroke();
    }

    // スイング線
    const visible = series.slice(x0i, x1i+1);
    let { highs, lows } = findRecentSwings(visible, hasOHLC, 2);
    if (highs.length === 0 && lows.length === 0) {
      const backup = recentWindowHL(visible, hasOHLC);
      highs = backup.highs; lows = backup.lows;
    }
    const drawLine = (price, color, label, strong=false) => {
      const y = yAtVal(price);
      ctx.save();
      ctx.setLineDash(strong ? [6,3] : [5,5]);
      ctx.strokeStyle = color;
      ctx.lineWidth = strong ? 1.6 : 1.2;
      ctx.beginPath(); ctx.moveTo(padX, y); ctx.lineTo(Wcss - padX, y); ctx.stroke();
      ctx.setLineDash([]);
      // ラベル
      const text = `${label} ${Math.round(price).toLocaleString()}`;
      const pad = 4;
      ctx.font = "10px system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif";
      const tw = ctx.measureText(text).width;
      const th = 14;
      const bx = Wcss - padX - tw - pad * 2;
      const by = Math.max(padY, Math.min(Hcss - padY - th, y - th / 2));
      ctx.fillStyle = "rgba(18,18,24,0.9)";
      ctx.strokeStyle = color; ctx.lineWidth = 1;
      if (ctx.roundRect) { ctx.beginPath(); ctx.roundRect(bx, by, tw+pad*2, th, 4); ctx.fill(); ctx.stroke(); }
      else { ctx.fillRect(bx, by, tw+pad*2, th); ctx.strokeRect(bx, by, tw+pad*2, th); }
      ctx.fillStyle = color; ctx.textAlign="left"; ctx.textBaseline="middle";
      ctx.fillText(text, bx+pad, by+th/2);
      ctx.restore();
    };
    if (highs.length >= 1) drawLine(highs[highs.length-1].price, "rgba(255,215,0,0.95)", "スイング高値#1", true);
    if (highs.length >= 2) drawLine(highs[highs.length-2].price, "rgba(255,215,0,0.60)", "スイング高値#2", false);
    if (lows.length  >= 1) drawLine(lows[lows.length-1].price,  "rgba(0,200,255,0.95)", "スイング安値#1", true);
    if (lows.length  >= 2) drawLine(lows[lows.length-2].price,  "rgba(0,200,255,0.55)", "スイング安値#2", false);

    // ズーム・パン用の便利関数と状態を保存
    st.series = series; st.hasOHLC = hasOHLC; st.padX = padX; st.padY = padY;
    st.innerW = innerW; st.innerH = innerH; st.Wcss = Wcss; st.Hcss = Hcss;
    st.xAtIdx = xAtIdx; st.yAtVal = yAtVal; st.yFullMin = yFullMin; st.yFullMax = yFullMax;
    st.minBars = 8; // 横方向の最小表示本数
    st.minYRange = (yFullMax - yFullMin) * 0.05 || 1; // 最小縦レンジ

    modal._priceData = d; // 現在のデータを保持
  }

  // 軸描画のラッパ（引数使いやすく）
  function Wctx(ctx){ return ctx; }
  function drawAxes(ctx, W, H, padX, padY, minY, maxY, dates) {
    ctx.save();
    ctx.fillStyle = "rgba(255,255,255,0.7)";
    ctx.font = "10px system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    if (dates.length >= 1) {
      const first = dates[0], last = dates[dates.length - 1];
      const mid = dates[Math.floor(dates.length / 2)];
      ctx.fillText(first, padX + 4, H - padY + 4);
      ctx.fillText(mid, W / 2, H - padY + 4);
      ctx.fillText(last, W - padX - 12, H - padY + 4);
    }
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    const fmt = (v) => Math.round(v).toLocaleString();
    const innerH = H - padY * 2;
    const yAt = (v) => padY + innerH * (1 - (v - minY) / Math.max(1e-9, (maxY - minY)));
    const marks = [minY, (minY + maxY) / 2, maxY];
    marks.forEach((v) => {
      const y = yAt(v);
      ctx.fillText(fmt(v), W - 2, y);
      ctx.strokeStyle = "rgba(255,255,255,0.08)";
      ctx.beginPath(); ctx.moveTo(padX, y); ctx.lineTo(W - padX, y); ctx.stroke();
    });
    ctx.restore();
  }

  async function loadPriceTab(modal, stockId, period = "1M") {
    modal._priceCache = modal._priceCache || {};
    if (!modal._priceCache[period]) {
      modal._priceCache[period] = fetchPrice(stockId, period);
    }
    const data = await modal._priceCache[period];
    renderPrice(modal, data);
    ensureChartGestures(modal); // ← 期間切替後も必ずジェスチャを再アタッチ（1回限り）
  }

  // ===== 指標タブ =====
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

  // ======== ジェスチャ（ピンチズーム/ドラッグパン/ホイール） ========
  function ensureChartGestures(modal) {
    if (modal._gesturesInstalled) return;
    const cvs = modal.querySelector("#price-canvas");
    if (!cvs) return;

    const state = modal._chartState || {};
    const viewportEl = document.querySelector('meta[name="viewport"]');
    // ページ全体のズームを完全禁止（スマホでも） ※ユーザーの要望に合わせて常時禁止
    if (viewportEl) viewportEl.setAttribute("content", "width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no");

    let pointers = new Map(); // id -> {x,y}
    let lastCenter = null;
    let lastDist = null;
    let isPanning = false;
    let lastPan = null;

    function getRect() { return cvs.getBoundingClientRect(); }
    function normXY(evt) {
      const r = getRect();
      return { x: evt.clientX - r.left, y: evt.clientY - r.top };
    }
    function dist(a,b){ const dx=a.x-b.x, dy=a.y-b.y; return Math.hypot(dx,dy); }
    function centerOf(map){
      const arr=[...map.values()];
      if(arr.length===0) return null;
      const x=arr.reduce((s,p)=>s+p.x,0)/arr.length;
      const y=arr.reduce((s,p)=>s+p.y,0)/arr.length;
      return {x,y};
    }

    // x座標→バーindex
    function xToIndex(modal, x) {
      const st = modal._chartState[modal._priceData.period];
      const { padX, innerW, xMin, xMax } = st;
      const t = Math.max(0, Math.min(1, (x - padX) / innerW));
      return st.xMin + (st.xMax - st.xMin) * t;
    }
    function yToValue(modal, y) {
      const st = modal._chartState[modal._priceData.period];
      const { padY, innerH, yMin, yMax } = st;
      const t = Math.max(0, Math.min(1, (y - padY) / innerH));
      return yMax - (yMax - yMin) * t;
    }

    function clampView(st) {
      const len = st.seriesLen;
      const minSpan = st.minBars;
      const maxSpan = len;
      let span = st.xMax - st.xMin;
      if (span < minSpan) {
        const mid = (st.xMin + st.xMax)/2;
        st.xMin = mid - minSpan/2;
        st.xMax = mid + minSpan/2;
      }
      if (st.xMin < 0){ st.xMax -= st.xMin; st.xMin = 0; }
      if (st.xMax > len-1){ const over = st.xMax - (len-1); st.xMin -= over; st.xMax = len-1; }
      if (st.xMin < 0){ st.xMin = 0; }

      if (!st.yAuto) {
        if (st.yMax - st.yMin < st.minYRange) {
          const mid = (st.yMax + st.yMin)/2;
          st.yMin = mid - st.minYRange/2;
          st.yMax = mid + st.minYRange/2;
        }
        // y はフルスケールの外側にはみ出しすぎない程度に緩くクランプ
        const margin = (st.yFullMax - st.yFullMin) * 2; // 広めの自由度
        st.yMin = Math.max(st.yFullMin - margin, st.yMin);
        st.yMax = Math.min(st.yFullMax + margin, st.yMax);
      }
    }

    function rerender() {
      if (!modal._priceData) return;
      renderPrice(modal, modal._priceData);
    }

    // Pointer Events
    cvs.addEventListener("pointerdown", (e)=>{
      cvs.setPointerCapture(e.pointerId);
      pointers.set(e.pointerId, normXY(e));
      if (pointers.size === 1) {
        isPanning = true;
        lastPan = normXY(e);
      } else if (pointers.size === 2) {
        const arr=[...pointers.values()];
        lastCenter = centerOf(pointers);
        lastDist = dist(arr[0], arr[1]);
      }
    });
    cvs.addEventListener("pointermove", (e)=>{
      if (!pointers.has(e.pointerId)) return;
      pointers.set(e.pointerId, normXY(e));
      const st = modal._chartState[modal._priceData.period];

      if (pointers.size === 2) {
        // ピンチズーム（x・y同時）
        const nowCenter = centerOf(pointers);
        const arr = [...pointers.values()];
        const nowDist = dist(arr[0], arr[1]);

        if (lastCenter && lastDist && nowDist > 0) {
          // ズーム倍率
          const scale = nowDist / lastDist;

          // 中心基準で x をズーム
          const cxIdx = xToIndex(modal, nowCenter.x);
          let span = (st.xMax - st.xMin) / scale;
          const newXMin = cxIdx - (cxIdx - st.xMin) / scale;
          const newXMax = newXMin + span;
          st.xMin = newXMin; st.xMax = newXMax;

          // y もズーム（値換算）
          const cyVal = yToValue(modal, nowCenter.y);
          const ySpan = (st.yMax - st.yMin) / scale;
          const newYMin = cyVal - (cyVal - st.yMin) / scale;
          const newYMax = newYMin + ySpan;
          st.yMin = newYMin; st.yMax = newYMax; st.yAuto = false;

          // 中心移動に伴うパン（2本指での平行移動）
          const dx = nowCenter.x - lastCenter.x;
          const dy = nowCenter.y - lastCenter.y;
          const xPerPx = (st.xMax - st.xMin) / st.innerW;
          const yPerPx = (st.yMax - st.yMin) / st.innerH;
          st.xMin -= dx * xPerPx; st.xMax -= dx * xPerPx;
          st.yMin += dy * yPerPx; st.yMax += dy * yPerPx;

          clampView(st);
          rerender();
        }
        lastCenter = nowCenter;
        lastDist = nowDist;
      } else if (isPanning && pointers.size === 1) {
        // 1本指パン
        const now = normXY(e);
        const st = modal._chartState[modal._priceData.period];
        const dx = now.x - lastPan.x;
        const dy = now.y - lastPan.y;
        const xPerPx = (st.xMax - st.xMin) / st.innerW;
        const yPerPx = (st.yMax - st.yMin) / st.innerH;

        st.xMin -= dx * xPerPx; st.xMax -= dx * xPerPx;
        st.yMin += dy * yPerPx; st.yMax += dy * yPerPx; st.yAuto = false;

        lastPan = now;
        clampView(st);
        rerender();
      }
    });
    function up(e){
      if (pointers.has(e.pointerId)) pointers.delete(e.pointerId);
      if (pointers.size < 2){ lastCenter=null; lastDist=null; }
      if (pointers.size === 0){ isPanning=false; lastPan=null; }
    }
    cvs.addEventListener("pointerup", up);
    cvs.addEventListener("pointercancel", up);
    cvs.addEventListener("pointerleave", up);

    // ホイールズーム（PC）
    cvs.addEventListener("wheel", (e)=>{
      e.preventDefault();
      const st = modal._chartState[modal._priceData.period];
      const delta = e.deltaY;
      const scale = Math.exp(-delta * 0.0015); // スムーズに
      const rect = cvs.getBoundingClientRect();
      const x = e.clientX - rect.left, y = e.clientY - rect.top;

      const cxIdx = xToIndex(modal, x);
      let span = (st.xMax - st.xMin) / scale;
      const newXMin = cxIdx - (cxIdx - st.xMin) / scale;
      const newXMax = newXMin + span;
      st.xMin = newXMin; st.xMax = newXMax;

      const cyVal = yToValue(modal, y);
      const ySpan = (st.yMax - st.yMin) / scale;
      const newYMin = cyVal - (cyVal - st.yMin) / scale;
      const newYMax = newYMin + ySpan;
      st.yMin = newYMin; st.yMax = newYMax; st.yAuto = false;

      clampView(st);
      rerender();
    }, { passive:false });

    modal._gesturesInstalled = true;
  }

  async function openDetail(stockId, cardEl) {
    if (!stockId) return;
    const mount = ensureMount();

    removeLegacyModals();
    document.body.classList.add("hide-legacy-modals");

    // ページ全体ズームを完全禁止（スマホファースト）
    const vp = document.querySelector('meta[name="viewport"]');
    if (vp) vp.setAttribute("content", "width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no");

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
          } catch (e) {
            console.error(e);
          }
        });
      });

      // 期間チップのクリックでチャート更新（ズーム状態は期間ごとに保持）
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

          try {
            await loadPriceTab(modal, stockId, period);
          } catch (err) {
            console.error(err);
          }
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

  // 起動
  document.addEventListener("DOMContentLoaded", () => {
    removeLegacyModals();
    document.body.classList.add("hide-legacy-modals");

    document.body.addEventListener("click", (e) => {
      const card = e.target.closest(".stock-card");
      if (!card) return;
      if (e.target.closest("a")) return;          // 編集/売却リンクは通常遷移
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