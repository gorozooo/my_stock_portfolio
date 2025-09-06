/* 詳細モーダル（概要 + 価格 + 指標）
   - 概要: /overview.json（from_card_current付き）で確定値
   - 価格: 初回 or 期間変更で /price.json?period= を取得 → Canvas 描画
   - 指標: 初回クリックで /fundamental.json を取得（配当利回り・DPS含む）
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

  function escCloseOnce(e) {
    if (e.key === "Escape") closeDetail();
  }

  function closeDetail() {
    const m = document.getElementById(mountId);
    if (m) m.innerHTML = "";
    document.removeEventListener("keydown", escCloseOnce);
    document.body.classList.add("hide-legacy-modals");
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

  // ===== 価格タブ（期間対応） =====
  async function fetchPrice(stockId, period) {
    const url = new URL(`/stocks/${stockId}/price.json`, window.location.origin);
    url.searchParams.set("period", period);
    const res = await fetch(url.toString(), { credentials: "same-origin" });
    if (!res.ok) throw new Error("価格データの取得に失敗しました");
    return await res.json();
  }

  function renderPrice(modal, d) {
    // 数値表示
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

    // チャート
    const cvs = modal.querySelector("#price-canvas");
    if (!cvs) return;
    const ctx = cvs.getContext("2d");
    const Wcss = cvs.clientWidth;
    const Hcss = cvs.clientHeight;
    const dpr = window.devicePixelRatio || 1;
    cvs.width  = Math.floor(Wcss * dpr);
    cvs.height = Math.floor(Hcss * dpr);
    ctx.setTransform(1,0,0,1,0,0);
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, Wcss, Hcss);

    const ys = (d.series || []).map(p => toNum(p.c)).filter(v => Number.isFinite(v));
    if (ys.length < 2) return;

    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    const padX = 12, padY = 10;
    const innerW = Wcss - padX * 2, innerH = Hcss - padY * 2;

    const xAt = (i) => padX + (innerW * i / (ys.length - 1));
    const yAt = (v) => padY + (innerH * (1 - (v - minY) / Math.max(1e-9, (maxY - minY))));

    // グラデ塗りつぶし
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

  async function loadPriceTab(modal, stockId, period = "1M") {
    // 簡易キャッシュ（モーダル単位）
    modal._priceCache = modal._priceCache || {};
    if (!modal._priceCache[period]) {
      modal._priceCache[period] = fetchPrice(stockId, period);
    }
    const data = await modal._priceCache[period];
    renderPrice(modal, data);
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
              // 現在の選択期間を見てロード
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

      // 期間チップのクリックでチャート更新
      const chipsWrap = modal.querySelector(".price-range-chips");
      if (chipsWrap) {
        chipsWrap.addEventListener("click", async (e) => {
          const btn = e.target.closest(".chip");
          if (!btn) return;
          const period = (btn.dataset.range || "1M").toUpperCase();

          // 見た目更新
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