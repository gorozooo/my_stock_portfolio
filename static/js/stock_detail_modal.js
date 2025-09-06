/* 詳細モーダル（概要 + 価格）
   - 概要はカード値で即時描画 → /overview.json（from_card_current付き）で確定値
   - 価格はタブ初回クリックで /price.json を取得して Canvas にミニチャート描画
*/
(function () {
  const mountId = "detail-modal-mount";

  // --------- 共通ユーティリティ ---------
  const toNum = (v, d = 0) => {
    const n = Number(String(v ?? "").replace(/[^\d.-]/g, ""));
    return Number.isFinite(n) ? n : d;
  };
  const yen = (n) => "¥" + Math.round(toNum(n)).toLocaleString();
  const num = (n) => toNum(n).toLocaleString();

  function calcOverview({ shares, unit_price, current_price, total_cost, position }) {
    const s  = Math.max(0, toNum(shares));
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

  function escCloseOnce(e) {
    if (e.key === "Escape") closeDetail();
  }

  function closeDetail() {
    const m = document.getElementById(mountId);
    if (m) m.innerHTML = "";
    document.removeEventListener("keydown", escCloseOnce);
    document.body.classList.add("hide-legacy-modals");
  }

  // --- カードから“現在株価”を確実に拾う（data属性 → テキスト救済の順で） ---
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

  // ===== 価格タブ関連 =====

  // 履歴から 52週高安を算出（APIが無い場合の保険）
  function calc52wFromHistory(history) {
    if (!Array.isArray(history) || history.length === 0) {
      return { high: null, low: null };
    }
    const tail = history.slice(-260); // おおよそ1年弱
    let hi = -Infinity, lo = Infinity;
    for (const p of tail) {
      const c = Number(p.c ?? p.close ?? p.price ?? 0);
      if (!Number.isFinite(c)) continue;
      if (c > hi) hi = c;
      if (c < lo) lo = c;
    }
    if (hi === -Infinity) hi = null;
    if (lo === Infinity)  lo = null;
    return { high: hi, low: lo };
  }

  // Canvas に軽量折れ線（DPR 対応）
  function drawLine(canvas, history) {
    if (!canvas) return;

    // 実測サイズ
    const cssW = canvas.clientWidth || 320;
    const cssH = canvas.clientHeight || 120;
    const dpr  = window.devicePixelRatio || 1;

    // 内部ピクセルを DPR 倍に
    canvas.width  = Math.max(1, Math.floor(cssW * dpr));
    canvas.height = Math.max(1, Math.floor(cssH * dpr));

    const ctx = canvas.getContext("2d");
    ctx.setTransform(1,0,0,1,0,0); // リセット
    ctx.scale(dpr, dpr);

    const W = cssW, H = cssH;

    const ys = (Array.isArray(history) ? history : [])
      .map(d => Number(d.c ?? d.close ?? d.price ?? 0))
      .filter(Number.isFinite);

    ctx.clearRect(0, 0, W, H);
    if (ys.length < 2) return;

    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    const padX = 12, padY = 10;
    const innerW = W - padX * 2, innerH = H - padY * 2;

    const xAt = (i) => padX + (innerW * i / (ys.length - 1));
    const yAt = (v) => padY + (innerH * (1 - (v - minY) / Math.max(1e-9, (maxY - minY))));

    // 塗りつぶし
    const grad = ctx.createLinearGradient(0, padY, 0, padY + innerH);
    grad.addColorStop(0, "rgba(0,200,255,0.35)");
    grad.addColorStop(1, "rgba(0,200,255,0.00)");
    ctx.beginPath();
    ctx.moveTo(xAt(0), yAt(ys[0]));
    ys.forEach((v, i) => ctx.lineTo(xAt(i), yAt(v)));
    ctx.lineTo(xAt(ys.length - 1), padY + innerH);
    ctx.lineTo(xAt(0), padY + innerH);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();

    // ライン
    ctx.beginPath();
    ctx.moveTo(xAt(0), yAt(ys[0]));
    ys.forEach((v, i) => ctx.lineTo(xAt(i), yAt(v)));
    ctx.strokeStyle = "rgba(0,200,255,0.85)";
    ctx.lineWidth = 2;
    ctx.stroke();
  }

  // 価格タブを初回だけロード
  async function loadPriceTab(modal, stockId) {
    if (modal.dataset.priceLoaded === "1") return;

    const url = new URL(`/stocks/${stockId}/price.json`, window.location.origin);
    const res = await fetch(url.toString(), { credentials: "same-origin" });
    if (!res.ok) throw new Error("価格データの取得に失敗しました");
    const d = await res.json();

    // 要素参照
    const lastEl = modal.querySelector("#price-last");
    const chgEl  = modal.querySelector("#price-chg");
    const h52El  = modal.querySelector("#price-52h");
    const l52El  = modal.querySelector("#price-52l");
    const cvs    = modal.querySelector("#price-canvas");

    // 最新値・前日比
    const last = toNum(d.last ?? d.last_close ?? d.current_price);
    const prev = toNum(d.prev_close ?? d.previous_close);
    const chg  = (prev > 0) ? last - prev : toNum(d.change);
    const chgp = (prev > 0) ? ((last - prev) / prev * 100) : toNum(d.change_pct);

    if (lastEl) lastEl.textContent = yen(last);
    if (chgEl) {
      const sign = (chg >= 0 ? "+" : "");
      chgEl.textContent = `${sign}${Math.round(chg).toLocaleString()} / ${ (Number.isFinite(chgp)? chgp.toFixed(2):"0.00") }%`;
      chgEl.style.color = (chg >= 0) ? "#6aff6a" : "#ff6a6a";
      chgEl.style.fontWeight = "800";
    }

    // 52週高安：APIが無ければ履歴から算出
    let h52 = d.high_52w ?? d.fifty_two_week_high ?? null;
    let l52 = d.low_52w  ?? d.fifty_two_week_low  ?? null;

    const history = Array.isArray(d.series) ? d.series : (Array.isArray(d.history) ? d.history : []);
    if ((h52 == null || l52 == null) && history.length > 0) {
      const { high, low } = calc52wFromHistory(history);
      if (h52 == null) h52 = high;
      if (l52 == null) l52 = low;
    }
    if (h52El) h52El.textContent = (h52 != null) ? yen(h52) : "—";
    if (l52El) l52El.textContent = (l52 != null) ? yen(l52) : "—";

    // チャート描画
    if (cvs) drawLine(cvs, history);

    modal.dataset.priceLoaded = "1";
  }

  // --------- メインフロー ---------
  async function openDetail(stockId, cardEl) {
    if (!stockId) return;
    const mount = ensureMount();

    // 旧モーダルを確実に排除
    removeLegacyModals();
    document.body.classList.add("hide-legacy-modals");

    // カードから即時プレビュー値
    const cardCp   = getCardCurrentPrice(cardEl);
    const cardUp   = toNum(cardEl?.dataset?.unit_price, 0);
    const cardSh   = toNum(cardEl?.dataset?.shares, 0);
    const position = (cardEl?.dataset?.position || "買い");
    const optimisticCp = cardCp > 0 ? cardCp : cardUp;

    try {
      // フラグメント読み込み
      const htmlRes = await fetch(`/stocks/${stockId}/detail_fragment/`, { credentials: "same-origin" });
      if (!htmlRes.ok) throw new Error("モーダルの読み込みに失敗しました");
      const html = await htmlRes.text();

      mount.innerHTML = "";
      mount.innerHTML = html;

      const modal = mount.querySelector("#detail-modal");
      if (!modal) throw new Error("モーダルが生成できませんでした");

      // ここで必ず stockId を埋め込む（タブ側が参照）
      modal.setAttribute("data-stock-id", String(stockId));

      // 閉じる（×相当 & フッター）
      modal.querySelectorAll("[data-dm-close]").forEach((el) => el.addEventListener("click", closeDetail));
      document.addEventListener("keydown", escCloseOnce);

      // タブ切替（価格タブは lazy load）
      modal.querySelectorAll(".detail-tab").forEach((btn) => {
        btn.addEventListener("click", async () => {
          if (btn.disabled) return;
          const name = btn.getAttribute("data-tab");
          modal.querySelectorAll(".detail-tab").forEach((b) => b.classList.toggle("is-active", b === btn));
          modal.querySelectorAll(".detail-panel").forEach((p) =>
            p.classList.toggle("is-active", p.getAttribute("data-panel") === name)
          );
          if (name === "price") {
            try { await loadPriceTab(modal, stockId); } catch (e) { console.error(e); }
          }
        });
      });

      // 概要：即時プレビュー描画
      const ovWrap = modal.querySelector('[data-panel="overview"]');
      if (ovWrap) {
        const optimistic = {
          broker: cardEl?.dataset?.broker || "",
          account_type: cardEl?.dataset?.account || "",
          position,
          shares: cardSh,
          unit_price: cardUp,
          current_price: optimisticCp,
          total_cost: cardSh * cardUp,
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

  // --------- 起動 ---------
  document.addEventListener("DOMContentLoaded", () => {
    removeLegacyModals();
    document.body.classList.add("hide-legacy-modals");

    // カードクリックで開く（編集/売却のリンクは素通し）
    document.body.addEventListener("click", (e) => {
      const card = e.target.closest(".stock-card");
      if (!card) return;
      if (e.target.closest("a")) return;
      if (card.classList.contains("swiped")) return;
      const id = card.dataset.id;
      if (!id || id === "0") return;
      openDetail(id, card);
    });

    // キーボード操作（Enter/Space）
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