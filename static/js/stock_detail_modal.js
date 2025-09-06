/* 詳細モーダル（段階導入：まずは「概要」だけ）
   - 旧モーダルを物理的に除去して“チラ見え”防止
   - カードの data-* を使って“即時プレビュー”を描画（体感を速く）
   - その後 /overview.json を取得して確定値に置き換え
   - ★ モーダルの現在株価は「カード側の現在株価」に合わせて補正
*/
(function () {
  const mountId = "detail-modal-mount";

  // ------- 小ユーティリティ -------
  const toNum = (v, d = 0) => {
    const n = Number(v);
    return Number.isFinite(n) ? n : d;
  };
  const yen = (n) => {
    try {
      return "¥" + Math.round(toNum(n, 0)).toLocaleString();
    } catch {
      return "¥0";
    }
  };
  const num = (n) => {
    try {
      return toNum(n, 0).toLocaleString();
    } catch {
      return "0";
    }
  };

  // position === "売り" のときは空売りの評価損益
  function calcOverview({ shares, unit_price, current_price, total_cost, position }) {
    const s  = Math.max(0, toNum(shares, 0));
    const up = Math.max(0, toNum(unit_price, 0));
    const cp = Math.max(0, toNum(current_price, 0));
    const tc = Math.max(0, toNum(total_cost, s * up)); // 念のため再計算フォールバック
    const mv = cp * s;
    const pl = (position === "売り") ? (up - cp) * s : (mv - tc);
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

  // 旧モーダルを安全に除去（チラ見え防止）
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

  async function openDetail(stockId, cardEl) {
    if (!stockId) return;
    const mount = ensureMount();

    // 旧モーダル排除 & ボディ側の再表示ブロック
    removeLegacyModals();
    document.body.classList.add("hide-legacy-modals");

    // --- カード側の“現在株価”を取得（ここをソース・オブ・トゥルースにする） ---
    const cardCp = toNum(cardEl?.dataset.current_price, 0);
    const cardUp = toNum(cardEl?.dataset.unit_price, 0);
    const cardShares = toNum(cardEl?.dataset.shares, 0);
    const cardPosition = (cardEl?.dataset.position || "買い");

    // 現在株価のフォールバック規則：
    // 1) カードに current_price があればそれを採用
    // 2) 無ければ unit_price を一時表示に使う（後でJSONで上書き）
    const optimisticCp = cardCp > 0 ? cardCp : cardUp;

    try {
      // 1) HTML断片（新モーダルの器）を取得
      const htmlRes = await fetch(`/stocks/${stockId}/detail_fragment/`, { credentials: "same-origin" });
      if (!htmlRes.ok) throw new Error("モーダルの読み込みに失敗しました");
      const html = await htmlRes.text();

      // 2) 差し替え
      mount.innerHTML = "";
      mount.innerHTML = html;

      const modal = mount.querySelector("#detail-modal");
      if (!modal) throw new Error("モーダルが生成できませんでした");

      // 閉じる
      modal.querySelectorAll("[data-dm-close]").forEach((el) => {
        el.addEventListener("click", () => closeDetail());
      });
      document.addEventListener("keydown", escCloseOnce);

      // タブ切替
      modal.querySelectorAll(".detail-tab").forEach((btn) => {
        btn.addEventListener("click", () => {
          if (btn.disabled) return;
          const name = btn.getAttribute("data-tab");
          modal
            .querySelectorAll(".detail-tab")
            .forEach((b) => b.classList.toggle("is-active", b === btn));
          modal
            .querySelectorAll(".detail-panel")
            .forEach((p) => p.classList.toggle("is-active", p.getAttribute("data-panel") === name));
        });
      });

      // 3) 概要パネルを“即時プレビュー”で先に埋める
      const ovWrap = modal.querySelector('[data-panel="overview"]');
      if (ovWrap && cardEl) {
        const optimistic = {
          broker:      cardEl.dataset.broker || "",
          account_type:cardEl.dataset.account || "",
          position:    cardPosition,
          shares:      cardShares,
          unit_price:  cardUp,
          current_price: optimisticCp,
          total_cost:  cardShares * cardUp,
          purchase_date: "",
          note: ""
        };
        ovWrap.innerHTML = optimisticOverviewHTML(optimistic);
      }

      // 4) 本番データで上書き
      //    ★ サーバの /overview.json は from_card_current を受けられる想定
      const url = new URL(`/stocks/${stockId}/overview.json`, window.location.origin);
      if (cardCp > 0) url.searchParams.set("from_card_current", String(cardCp));
      const res = await fetch(url.toString(), { credentials: "same-origin" });
      if (!res.ok) throw new Error("概要データの取得に失敗しました");
      const d = await res.json();

      // 念のためクライアント側でも最終補正：
      // サーバが current_price を返せなかった場合、カードの値で上書き
      const fixed = { ...d };
      if (toNum(fixed.current_price, 0) <= 0 && cardCp > 0) {
        fixed.current_price = cardCp;
      }
      // 取得額が無い/ズレていれば補正
      if (toNum(fixed.total_cost, 0) <= 0) {
        fixed.total_cost = toNum(fixed.shares, 0) * toNum(fixed.unit_price, 0);
      }
      // 市場価値/損益は最新 current_price で再計算
      const { market_value, profit_loss } = calcOverview(fixed);
      fixed.market_value = market_value;
      fixed.profit_loss  = profit_loss;

      if (ovWrap) ovWrap.innerHTML = optimisticOverviewHTML(fixed);
    } catch (err) {
      console.error(err);
      alert("詳細の読み込みでエラーが発生しました。時間をおいて再度お試しください。");
      closeDetail();
    }
  }

  // ===== 一覧カードから起動 =====
  document.addEventListener("DOMContentLoaded", () => {
    // 初回ロード時に旧モーダルを物理除去
    removeLegacyModals();
    document.body.classList.add("hide-legacy-modals");

    // カード本体タップで新モーダルを開く（スワイプボタンは除外）
    document.body.addEventListener("click", (e) => {
      const card = e.target.closest(".stock-card");
      if (!card) return;

      // 右側の a（編集/売却）リンクは通常遷移を許可
      const a = e.target.closest("a");
      if (a) return;

      if (card.classList.contains("swiped")) return; // スワイプ中は誤タップ防止

      const id = card.dataset.id;
      if (!id || id === "0") {
        console.warn("card dataset.id が不正");
        return;
      }
      openDetail(id, card);
    });

    // Enter/Spaceで開けるように（アクセシビリティ）
    document.body.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" && e.key !== " ") return;
      const card = e.target.closest && e.target.closest(".stock-card");
      if (!card) return;

      const id = card.dataset.id;
      if (!id || id === "0") return;
      e.preventDefault();
      openDetail(id, card);
    });
  });
})();