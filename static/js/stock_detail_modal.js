/* スマホファースト版：ページ拡大縮小は無効化しつつ、
   チャート内だけピンチで拡大/縮小、ドラッグで上下左右パンできるように。
   既存の「概要/価格/指標」タブ構成・データ取得はそのまま。
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

  // ========= 概要（既存） =========
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

  function ensureMount(){
    let m = document.getElementById(mountId);
    if(!m){ m = document.createElement("div"); m.id = mountId; document.body.appendChild(m); }
    return m;
  }
  function removeLegacyModals(){
    ["stock-modal","edit-modal","sell-modal"].forEach(id=>{
      const el = document.getElementById(id);
      if(el && el.parentNode) el.parentNode.removeChild(el);
    });
  }
  function escCloseOnce(e){ if(e.key === "Escape") closeDetail(); }

  // ========= 「ページのズーム禁止」ガード（モーダル表示中のみ） =========
  const zoomGuards = {
    applied:false,
    onKeydown(e){
      if (!e.ctrlKey && !e.metaKey) return;
      const k = e.key.toLowerCase();
      if (k==='+'||k==='='||k==='-'||k==='_'||k==='0'){ e.preventDefault(); }
    },
    onWheel(e){ if (e.ctrlKey) e.preventDefault(); },
    onGestureStart(e){ e.preventDefault(); }, // iOS Safari
    enable(){
      if (this.applied) return;
      document.addEventListener('keydown', this.onKeydown, { capture:true });
      document.addEventListener('wheel', this.onWheel, { passive:false, capture:true });
      document.addEventListener('gesturestart', this.onGestureStart, { passive:false, capture:true });
      this.applied = true;
    },
    disable(){
      if (!this.applied) return;
      document.removeEventListener('keydown', this.onKeydown, { capture:true });
      document.removeEventListener('wheel', this.onWheel, { capture:true });
      document.removeEventListener('gesturestart', this.onGestureStart, { capture:true });
      this.applied = false;
    }
  };

  function closeDetail(){
    const m = document.getElementById(mountId);
    if(m) m.innerHTML = "";
    document.removeEventListener("keydown", escCloseOnce);
    document.body.classList.add("hide-legacy-modals");
    zoomGuards.disable();
  }

  // ========= カードから現在株価（保険） =========
  function getCardCurrentPrice(card){
    let cp = toNum(card?.dataset?.current_price, 0);
    if (cp > 0) return cp;
    try{
      const rows = card.querySelectorAll(".stock-row");
      for(const r of rows){
        const label = r.querySelector("span:first-child")?.textContent?.trim();
        if(label && label.includes("現在株価")){
          const v = r.querySelector("span:last-child")?.textContent || "";
          const n = toNum(v,0);
          if(n>0) return n;
        }
      }
    }catch(_){}
    return 0;
  }

  // ========= データ取得 =========
  async function fetchPrice(stockId, period){
    const url = new URL(`/stocks/${stockId}/price.json`, location.origin);
    url.searchParams.set("period", (period||"1M").toUpperCase());
    const res = await fetch(url, { credentials:'same-origin' });
    if(!res.ok) throw new Error("価格データの取得に失敗しました");
    return await res.json();
  }
  async function fetchFund(stockId, cardCp){
    const url = new URL(`/stocks/${stockId}/fundamental.json`, location.origin);
    if (cardCp && cardCp > 0) url.searchParams.set("from_card_current", String(cardCp));
    const res = await fetch(url, { credentials:'same-origin' });
    if(!res.ok) throw new Error("指標データの取得に失敗しました");
    return await res.json();
  }

  // ========= 軸ラベル（簡易） =========
  function drawAxes(ctx, W, H, padX, padY, minY, maxY, leftDate, midDate, rightDate){
    ctx.save();
    ctx.fillStyle = "rgba(255,255,255,0.7)";
    ctx.font = "10px system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif";
    // X
    ctx.textAlign = "center"; ctx.textBaseline = "top";
    if (leftDate)  ctx.fillText(leftDate,  padX, H - padY + 4);
    if (midDate)   ctx.fillText(midDate,   W/2,  H - padY + 4);
    if (rightDate) ctx.fillText(rightDate, W-padX, H - padY + 4);
    // Y
    ctx.textAlign = "right"; ctx.textBaseline = "middle";
    const fmt = (v) => Math.round(v).toLocaleString();
    const innerH = H - padY*2;
    const yAt = (v) => padY + innerH*(1-(v-minY)/Math.max(1e-9,(maxY-minY)));
    [minY,(minY+maxY)/2,maxY].forEach(v=>{
      const y = yAt(v);
      ctx.fillText(fmt(v), W-2, y);
      ctx.strokeStyle = "rgba(255,255,255,0.08)";
      ctx.beginPath(); ctx.moveTo(padX,y); ctx.lineTo(W-padX,y); ctx.stroke();
    });
    ctx.restore();
  }

  // ========= チャート状態（スマホ優先：ピンチズーム & ドラッグパン） =========
  function initChartState(modal, series, period){
    const L = series.length;
    const defaultBars = { "1M": 30, "3M": 60, "1Y": 120 }[(period||"1M").toUpperCase()] || 60;
    const visible = Math.min(defaultBars, L);
    const start = Math.max(0, L - visible);
    const end   = L - 1;

    modal._chartState = {
      series,
      period,
      start, end,       // X の可視範囲（インデックス）
      yZoom: 1,         // Y ズーム倍率（>1 で拡大）
      yShiftPx: 0,      // Y パン（px）
      minBars: 10, maxBars: L,
      // ポインタ管理（スマホのピンチ用）
      pointers: new Map(),
      lastPinchDist: null,
      lastPinchMid: null,
      isDragging: false,
      lastDrag: { x:0, y:0 }
    };
  }

  function clamp(v, a, b){ return Math.max(a, Math.min(b, v)); }

  function renderPrice(modal, data){
    // 数値部
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

    // チャート
    const cvs = modal.querySelector("#price-canvas");
    if (!cvs) return;

    // スマホのピンチを優先してページスクロール/ズームを抑制
    cvs.style.touchAction = "none"; // ← これ大事（スマホでピンチ/ドラッグをCanvasへ）

    const ctx = cvs.getContext("2d");
    const Wcss = cvs.clientWidth;
    const Hcss = cvs.clientHeight;
    const dpr = window.devicePixelRatio || 1;
    cvs.width  = Math.floor(Wcss * dpr);
    cvs.height = Math.floor(Hcss * dpr);
    ctx.setTransform(1,0,0,1,0,0);
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, Wcss, Hcss);

    const series = Array.isArray(data.series) ? data.series : [];
    if (series.length < 2) return;

    // 状態（初期化 or 更新）
    if (!modal._chartState || modal._chartState.series !== series){
      initChartState(modal, series, data.period || "1M");
      // イベント（スマホ/PC共通: Pointer Events）
      attachChartInteractions(modal, cvs);
    }
    const st = modal._chartState;
    st.series = series; // 念のため最新反映

    // 可視部分のスライス
    const start = clamp(st.start, 0, series.length-2);
    const end   = clamp(st.end, start+1, series.length-1);
    const vis   = series.slice(start, end+1);

    // スケール
    const padX = 28, padY = 18;
    const innerW = Wcss - padX*2;
    const innerH = Hcss - padY*2;
    const xStep = innerW / Math.max(1, vis.length - 1);

    const hasOHLC = ["o","h","l","c"].every(k => vis[0] && (k in vis[0]));
    const yVals = hasOHLC
      ? vis.flatMap(p => [toNum(p.h), toNum(p.l)])
      : vis.map(p => toNum(p.c));
    let minY = Math.min(...yVals);
    let maxY = Math.max(...yVals);
    if (minY === maxY){ minY -= 1; maxY += 1; } // 同値ズレ防止
    const yAtRaw = (v) => padY + innerH * (1 - (v - minY) / Math.max(1e-9,(maxY-minY)));

    // Yズーム/パンは「ピクセル座標」に後処理で適用
    const centerY = padY + innerH/2;
    const applyYTransform = (yPx) => centerY + (yPx - centerY) * st.yZoom + st.yShiftPx;

    const xAt = (i) => padX + xStep * i;

    // 影（背景グリッドは軽く）
    ctx.save();
    ctx.fillStyle = "rgba(255,255,255,0.04)";
    ctx.fillRect(padX, padY, innerW, innerH);
    ctx.restore();

    if (hasOHLC){
      // ローソク足
      const bodyW = Math.max(3, Math.min(10, xStep * 0.6));
      vis.forEach((p, i) => {
        const o = toNum(p.o), h = toNum(p.h), l = toNum(p.l), c = toNum(p.c);
        const cx = xAt(i);
        const yO = applyYTransform(yAtRaw(o));
        const yH = applyYTransform(yAtRaw(h));
        const yL = applyYTransform(yAtRaw(l));
        const yC = applyYTransform(yAtRaw(c));
        const yTop = Math.min(yO, yC);
        const yBot = Math.max(yO, yC);
        const isBull = c >= o;
        const col = isBull ? "rgba(244,67,54,1)" : "rgba(76,175,80,1)";
        const colWick = isBull ? "rgba(244,67,54,0.9)" : "rgba(76,175,80,0.9)";

        // ヒゲ
        ctx.strokeStyle = colWick; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(cx, yH); ctx.lineTo(cx, yL); ctx.stroke();

        // 実体
        ctx.fillStyle = col;
        const hBody = Math.max(1, yBot - yTop);
        ctx.fillRect(cx - bodyW/2, yTop, bodyW, hBody);
      });
    }else{
      // 終値ライン + エリア
      const ys = vis.map(p => toNum(p.c));
      const grad = ctx.createLinearGradient(0, padY, 0, padY + innerH);
      grad.addColorStop(0, "rgba(0,200,255,0.35)");
      grad.addColorStop(1, "rgba(0,200,255,0.00)");

      ctx.beginPath();
      ctx.moveTo(xAt(0), applyYTransform(yAtRaw(ys[0])));
      ys.forEach((v,i)=> ctx.lineTo(xAt(i), applyYTransform(yAtRaw(v))));
      ctx.lineTo(xAt(ys.length-1), applyYTransform(yAtRaw(minY)));
      ctx.lineTo(xAt(0),            applyYTransform(yAtRaw(minY)));
      ctx.closePath();
      ctx.fillStyle = grad; ctx.fill();

      ctx.beginPath();
      ctx.moveTo(xAt(0), applyYTransform(yAtRaw(ys[0])));
      ys.forEach((v,i)=> ctx.lineTo(xAt(i), applyYTransform(yAtRaw(v))));
      ctx.strokeStyle = "rgba(0,200,255,0.85)";
      ctx.lineWidth = 2; ctx.stroke();
    }

    // 軸
    const dates = vis.map(p=>p.t);
    drawAxes(ctx, Wcss, Hcss, padX, padY, minY, maxY,
      dates[0], dates[Math.floor(dates.length/2)], dates[dates.length-1]);

    // 保存（再描画用にセット）
    modal._chartState._render = () => renderPrice(modal, data);
  }

  // ========= チャートの操作イベント（スマホ優先：Pointer Events） =========
  function attachChartInteractions(modal, canvas){
    const st = modal._chartState;
    const cvs = canvas;

    // Wheel（PC用ズーム）— スマホは無視
    const onWheel = (e) => {
      // ページズームは禁止済み。ここではチャートのXズームのみ
      if (e.ctrlKey) return; // ページズームは prevent 済
      e.preventDefault();
      const dir = Math.sign(e.deltaY);
      const L = st.series.length;
      const currBars = st.end - st.start + 1;
      const focusPx = e.offsetX; // マウス位置を基準にズーム
      const padX = 28;
      const innerW = cvs.clientWidth - padX*2;
      const rel = clamp((focusPx - padX)/Math.max(1,innerW), 0, 1);
      const focusIdx = Math.round(st.start + rel * (currBars-1));

      const zoomStep = 0.15;
      const newBars = clamp(Math.round(dir > 0 ? currBars*(1+zoomStep) : currBars*(1-zoomStep)), st.minBars, st.maxBars);
      const half = Math.floor(newBars * rel);
      st.start = clamp(focusIdx - half, 0, L - newBars);
      st.end   = st.start + newBars - 1;
      // 縦パン（Shift + ホイール）オプション
      if (e.shiftKey){
        st.yShiftPx += e.deltaY * 0.3;
      }
      st._render && st._render();
    };

    // Pointer（スマホ/PC共通）
    const onPointerDown = (e) => {
      cvs.setPointerCapture(e.pointerId);
      st.pointers.set(e.pointerId, { x:e.clientX, y:e.clientY });
      if (st.pointers.size === 1){
        st.isDragging = true;
        st.lastDrag = { x:e.clientX, y:e.clientY };
      }else if (st.pointers.size === 2){
        // ピンチ初期化
        const pts = Array.from(st.pointers.values());
        st.lastPinchDist = dist(pts[0], pts[1]);
        st.lastPinchMid  = mid(pts[0], pts[1]);
      }
      e.preventDefault();
    };

    const onPointerMove = (e) => {
      if (!st.pointers.has(e.pointerId)) return;
      st.pointers.set(e.pointerId, { x:e.clientX, y:e.clientY });

      const pts = Array.from(st.pointers.values());
      if (pts.length === 2){
        // ピンチズーム & 2本指ドラッグ（Yパン）
        const dNow  = dist(pts[0], pts[1]);
        const dPrev = st.lastPinchDist || dNow;
        const scale = clamp(dNow / dPrev, 0.5, 2.0);

        const midNow  = mid(pts[0], pts[1]);
        const midPrev = st.lastPinchMid || midNow;

        // X方向：バー数でズーム（中心は画面の中点付近に）
        const L = st.series.length;
        const currBars = st.end - st.start + 1;
        let newBars = clamp(Math.round(currBars / scale), st.minBars, st.maxBars);

        // 中心は現在の可視範囲の中央寄りに
        const focusIdx = Math.round((st.start + st.end)/2);
        const half = Math.floor(newBars/2);
        st.start = clamp(focusIdx - half, 0, L - newBars);
        st.end   = st.start + newBars - 1;

        // Y方向：ピンチでズーム、2本指の中点移動でパン
        st.yZoom = clamp(st.yZoom * scale, 0.5, 5); // 縮小~拡大
        st.yShiftPx += (midNow.y - midPrev.y);      // 上下ドラッグで縦パン

        st.lastPinchDist = dNow;
        st.lastPinchMid  = midNow;
        st._render && st._render();
        e.preventDefault();
        return;
      }

      if (st.isDragging && pts.length === 1){
        // 1本指ドラッグ：左右パン（X）、上下ドラッグ：縦パン（Y）
        const dx = e.clientX - st.lastDrag.x;
        const dy = e.clientY - st.lastDrag.y;
        st.lastDrag = { x:e.clientX, y:e.clientY };

        // X パン：ピクセル → バー数に換算
        const padX = 28;
        const innerW = cvs.clientWidth - padX*2;
        const currBars = st.end - st.start + 1;
        const barPx = innerW / Math.max(1, currBars-1);
        const shiftBars = Math.round(-dx / Math.max(1, barPx));
        if (shiftBars){
          const L = st.series.length;
          let s = clamp(st.start + shiftBars, 0, L - currBars);
          st.start = s; st.end = s + currBars - 1;
        }

        // Y パン（上下）：ピクセルそのまま
        st.yShiftPx += dy;

        st._render && st._render();
        e.preventDefault();
      }
    };

    const onPointerUp = (e) => {
      st.pointers.delete(e.pointerId);
      if (st.pointers.size < 2){
        st.lastPinchDist = null;
        st.lastPinchMid  = null;
      }
      if (st.pointers.size === 0){
        st.isDragging = false;
      }
      e.preventDefault();
    };

    cvs.addEventListener('wheel', onWheel, { passive:false });
    cvs.addEventListener('pointerdown', onPointerDown, { passive:false });
    cvs.addEventListener('pointermove', onPointerMove, { passive:false });
    cvs.addEventListener('pointerup', onPointerUp, { passive:false });
    cvs.addEventListener('pointercancel', onPointerUp, { passive:false });

    function dist(a,b){ const dx=a.x-b.x, dy=a.y-b.y; return Math.hypot(dx,dy); }
    function mid(a,b){ return { x:(a.x+b.x)/2, y:(a.y+b.y)/2 }; }
  }

  // ========= 指標タブ描画（既存簡易） =========
  async function loadFundamentalTab(modal, stockId, cardCp){
    if (modal.dataset.fundLoaded === "1") return;
    const d = await fetchFund(stockId, cardCp);
    const setText = (sel, valStr) => {
      const el = modal.querySelector(sel); if (!el) return;
      el.textContent = (valStr===null||valStr===undefined||valStr==="") ? "—" : String(valStr);
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

  async function loadPriceTab(modal, stockId, period="1M"){
    modal._priceCache = modal._priceCache || {};
    if (!modal._priceCache[period]) modal._priceCache[period] = fetchPrice(stockId, period);
    const data = await modal._priceCache[period];
    renderPrice(modal, data);
  }

  // ========= モーダル起動 =========
  async function openDetail(stockId, cardEl){
    if (!stockId) return;
    const mount = ensureMount();

    removeLegacyModals();
    document.body.classList.add("hide-legacy-modals");

    // ページの拡大縮小はモーダル中は無効化（スマホピンチはキャンバスにだけ効く）
    zoomGuards.enable();

    const cardCp = getCardCurrentPrice(cardEl);
    const cardUp = toNum(cardEl?.dataset?.unit_price, 0);
    const cardShares = toNum(cardEl?.dataset?.shares, 0);
    const cardPosition = (cardEl?.dataset?.position || "買い");
    const optimisticCp = cardCp > 0 ? cardCp : cardUp;

    try{
      const htmlRes = await fetch(`/stocks/${stockId}/detail_fragment/`, { credentials:"same-origin" });
      if(!htmlRes.ok) throw new Error("モーダルの読み込みに失敗しました");
      const html = await htmlRes.text();

      mount.innerHTML = "";
      mount.innerHTML = html;

      const modal = mount.querySelector("#detail-modal");
      if(!modal) throw new Error("モーダルが生成できませんでした");

      // 閉じる
      modal.querySelectorAll("[data-dm-close]").forEach(el=> el.addEventListener("click", closeDetail));
      document.addEventListener("keydown", escCloseOnce);

      // タブ切替（価格/指標は lazy）
      modal.querySelectorAll(".detail-tab").forEach((btn)=>{
        btn.addEventListener("click", async ()=>{
          if (btn.disabled) return;
          const name = btn.getAttribute("data-tab");
          modal.querySelectorAll(".detail-tab").forEach(b=>b.classList.toggle("is-active", b===btn));
          modal.querySelectorAll(".detail-panel").forEach(p=>p.classList.toggle("is-active", p.getAttribute("data-panel")===name));
          try{
            if (name==="price"){
              const activeChip = modal.querySelector(".price-range-chips .chip.is-active");
              const period = activeChip?.dataset?.range || "1M";
              await loadPriceTab(modal, stockId, period);
            }else if(name==="fundamental"){
              await loadFundamentalTab(modal, stockId, cardCp);
            }
          }catch(e){ console.error(e); }
        });
      });

      // 期間チップ（ある場合）
      const chipsWrap = modal.querySelector(".price-range-chips");
      if (chipsWrap){
        chipsWrap.addEventListener("click", async (e)=>{
          const btn = e.target.closest(".chip");
          if(!btn) return;
          const period = (btn.dataset.range||"1M").toUpperCase();
          chipsWrap.querySelectorAll(".chip").forEach(c=>{
            c.classList.toggle("is-active", c===btn);
            c.setAttribute("aria-selected", c===btn ? "true":"false");
          });
          try{ await loadPriceTab(modal, stockId, period); }catch(err){ console.error(err); }
        });
      }

      // 概要：まずは楽観レンダ
      const ovWrap = modal.querySelector('[data-panel="overview"]');
      if (ovWrap){
        ovWrap.innerHTML = optimisticOverviewHTML({
          broker: cardEl?.dataset?.broker || "",
          account_type: cardEl?.dataset?.account || "",
          position: cardPosition,
          shares: cardShares,
          unit_price: cardUp,
          current_price: optimisticCp,
          total_cost: cardShares * cardUp,
          purchase_date: "",
          note: ""
        });
      }

      // 概要：確定値
      const url = new URL(`/stocks/${stockId}/overview.json`, location.origin);
      if (cardCp > 0) url.searchParams.set("from_card_current", String(cardCp));
      const res = await fetch(url, { credentials:'same-origin' });
      if(!res.ok) throw new Error("概要データの取得に失敗しました");
      const d = await res.json();
      const fixed = { ...d };
      if (toNum(fixed.current_price, 0) <= 0 && cardCp > 0) fixed.current_price = cardCp;
      if (toNum(fixed.total_cost, 0) <= 0) fixed.total_cost = toNum(fixed.shares, 0) * toNum(fixed.unit_price, 0);
      if (ovWrap) ovWrap.innerHTML = optimisticOverviewHTML(fixed);
    }catch(err){
      console.error(err);
      alert("詳細の読み込みでエラーが発生しました。時間をおいて再度お試しください。");
      closeDetail();
    }
  }

  // ========= 起動 =========
  document.addEventListener("DOMContentLoaded", ()=>{
    removeLegacyModals();
    document.body.classList.add("hide-legacy-modals");

    // カードクリックで起動（スマホ配慮）
    document.body.addEventListener("click", (e)=>{
      const card = e.target.closest(".stock-card");
      if(!card) return;
      if (e.target.closest("a")) return;
      if (card.classList.contains("swiped")) return;
      const id = card.dataset.id;
      if(!id || id==="0") return;
      openDetail(id, card);
    });

    // キーボード起動（PC）
    document.body.addEventListener("keydown", (e)=>{
      if (e.key!=="Enter" && e.key!==" ") return;
      const card = e.target.closest?.(".stock-card"); if(!card) return;
      const id = card.dataset.id; if(!id || id==="0") return;
      e.preventDefault();
      openDetail(id, card);
    });
  });
})();