/* =========================================
 * stock_list.js
 * スマホファースト / HTML・CSS・JS 分離
 * 改善まとめ：
 *  - タブ切替でセクション中央寄せ & 前回位置を保存
 *  - 口座チップ自動生成 + ScrollSpyでハイライト
 *  - 口座サマリー自動集計（取得額・評価額・損益）
 *  - 大量カード時の描画最適化（content-visibility 前提のCSS）
 *  - カード左スワイプでアクション（編集/売却）表示
 *  - モーダル改善（Esc/背景クリックで閉じる、フォーム埋め込み）
 *  - 売却モーダル：市場/指値モード切替
 * ========================================= */

document.addEventListener("DOMContentLoaded", () => {
  // -------------------------------
  // タブ & セクション
  // -------------------------------
  const tabs = Array.from(document.querySelectorAll(".broker-tab"));
  const wrapper = document.getElementById("broker-horizontal-wrapper");
  const sections = Array.from(document.querySelectorAll(".broker-section"));
  if (!wrapper || sections.length === 0) return;

  // セクション中央寄せスクロール
  const scrollToSectionCenter = (index, smooth = true) => {
    const target = sections[index];
    if (!target) return;
    const wrapperWidth = wrapper.clientWidth;
    const sectionRect = target.getBoundingClientRect();
    const wrapperRect = wrapper.getBoundingClientRect();
    const sectionLeftRelative = sectionRect.left - wrapperRect.left + wrapper.scrollLeft;
    let scrollLeft = sectionLeftRelative - (wrapperWidth / 2) + (sectionRect.width / 2);
    const maxScroll = wrapper.scrollWidth - wrapperWidth;
    scrollLeft = Math.min(Math.max(scrollLeft, 0), maxScroll);
    wrapper.scrollTo({ left: scrollLeft, behavior: smooth ? "smooth" : "auto" });
  };

  // タブのアクティブ表示
  const setActiveTab = index => {
    tabs.forEach(t => t.classList.remove("active"));
    if (tabs[index]) tabs[index].classList.add("active");
    scrollToSectionCenter(index);
    localStorage.setItem("activeBrokerIndex", String(index));
  };

  // 初期選択（前回の続き）
  const savedIndex = parseInt(localStorage.getItem("activeBrokerIndex") || "0", 10);
  setTimeout(() => setActiveTab(isNaN(savedIndex) ? 0 : savedIndex), 80);

  // タブクリック
  tabs.forEach((tab, i) => tab.addEventListener("click", () => setActiveTab(i)));

  // -------------------------------
  // モーダル共通（開閉）
  // -------------------------------
  const setupModal = modalId => {
    const modal = document.getElementById(modalId);
    if (!modal) return null;
    const closeBtn = modal.querySelector(".modal-close");
    const close = () => {
      modal.style.display = "none";
      modal.setAttribute("aria-hidden", "true");
    };
    closeBtn?.addEventListener("click", close);
    modal.addEventListener("click", e => { if (e.target === modal) close(); });
    document.addEventListener("keydown", e => { if (e.key === "Escape" && modal.style.display === "block") close(); });
    return modal;
  };

  const stockModal = setupModal("stock-modal");
  const editModal  = setupModal("edit-modal");
  const sellModal  = setupModal("sell-modal");

  const escapeHTML = str => String(str).replace(/[&<>"']/g, m =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m])
  );

  // -------------------------------
  // 口座チップの自動生成
  // -------------------------------
  sections.forEach(section => {
    const chipsWrap = section.querySelector('.account-chips');
    const headings  = section.querySelectorAll('.account-heading');
    if (!chipsWrap || headings.length === 0) return;

    chipsWrap.innerHTML = '';
    headings.forEach(h => {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'account-chip';
      chip.textContent = h.textContent.trim();
      chip.dataset.target = `#${h.id}`;
      chip.addEventListener('click', () => {
        const target = section.querySelector(chip.dataset.target);
        if (!target) return;
        // 見出し直下に口座サマリーが挿入されるので「start」でOK
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
      chipsWrap.appendChild(chip);
    });
  });

  // -------------------------------
  // ScrollSpy（見出しが見えた口座のチップをハイライト）
  // ルートは各セクションの縦スクロール領域（.broker-cards-wrapper）
  // -------------------------------
  sections.forEach(section => {
    const rootEl = section.querySelector('.broker-cards-wrapper');
    if (!rootEl) return;
    const chips = section.querySelectorAll('.account-chip');
    const io = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (!entry.isIntersecting) return;
        const id = `#${entry.target.id}`;
        chips.forEach(chip => chip.classList.toggle('is-active', chip.dataset.target === id));
      });
    }, { root: rootEl, rootMargin: '0px 0px -60% 0px', threshold: 0.1 });

    section.querySelectorAll('.account-heading').forEach(h => io.observe(h));
  });

  // -------------------------------
  // 口座サマリー（取得額・評価額・損益）を自動集計し見出し直下へ
  // -------------------------------
  sections.forEach(section => {
    const wrapperCol = section.querySelector('.broker-cards-wrapper');
    if (!wrapperCol) return;
    const children = Array.from(wrapperCol.children);

    for (let i = 0; i < children.length; i++) {
      if (!children[i].classList.contains('account-heading')) continue;

      const heading = children[i];
      let totalCost = 0;
      let totalEval = 0;
      let totalProfit = 0;

      let j = i + 1;
      for (; j < children.length; j++) {
        if (children[j].classList.contains('account-heading')) break;
        const card = children[j].querySelector('.stock-card');
        if (!card) continue;

        const shares = Number(card.dataset.shares || 0);
        const unit   = Number(card.dataset.unit_price || 0);
        const cur    = Number(card.dataset.current_price || unit);
        const profit = Number(card.dataset.profit ?? (cur * shares - unit * shares));

        totalCost   += shares * unit;
        totalEval   += shares * cur;
        totalProfit += profit;
      }

      const summary = document.createElement('div');
      summary.className = 'account-summary';
      const profitClass = totalProfit >= 0 ? 'positive' : 'negative';
      summary.innerHTML = `
        <span class="sum-label">合計：</span>
        <span>取得額 <strong>${totalCost.toLocaleString()}</strong> 円</span>
        <span>評価額 <strong>${totalEval.toLocaleString()}</strong> 円</span>
        <span class="sum-profit ${profitClass}">損益 <strong>${totalProfit.toLocaleString()}</strong> 円</span>
      `;
      heading.insertAdjacentElement('afterend', summary);

      i = j - 1; // 次のループ最適化
    }
  });

  // -------------------------------
  // 株カード：クリックで詳細モーダル
  // -------------------------------
  document.querySelectorAll(".stock-card").forEach(card => {
    const cardId = card.dataset.id;

    card.addEventListener("click", () => {
      if (card.classList.contains("swiped")) return; // スワイプ状態では開かない
      if (!stockModal) return;

      const modalBody    = stockModal.querySelector("#modal-body");
      const modalEditBtn = stockModal.querySelector("#edit-stock-btn");
      const modalSellBtn = stockModal.querySelector("#sell-stock-btn");

      modalBody.innerHTML = `
        <h3 id="modal-title">${escapeHTML(card.dataset.name)} (${escapeHTML(card.dataset.ticker)})</h3>
        <p>口座：${escapeHTML(card.dataset.account || "")}</p>
        <p>株数：${escapeHTML(card.dataset.shares || "0")}</p>
        <p>取得単価：¥${escapeHTML(card.dataset.unit_price || "0")}</p>
        <p>現在株価：¥${escapeHTML(card.dataset.current_price || card.dataset.unit_price || "0")}</p>
        <p>損益：¥${escapeHTML(card.dataset.profit || "0")} (${escapeHTML(card.dataset.profit_rate || "0")}%)</p>
      `;
      stockModal.style.display = "block";
      stockModal.setAttribute("aria-hidden", "false");

      if (modalEditBtn) modalEditBtn.dataset.id = cardId;
      if (modalSellBtn) modalSellBtn.dataset.id = cardId;
    });

    // アクセシビリティ（Enter/Spaceで開く）
    card.addEventListener("keydown", e => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); card.click(); }
    });
  });

  // -------------------------------
  // 編集・売却モーダルを開くヘルパ
  // -------------------------------
  const openEditModal = stock => {
    if (!editModal) return;
    const form = editModal.querySelector("#edit-form");
    form.elements["stock_id"].value = stock.id ?? "";
    form.elements["name"].value     = stock.name ?? "";
    form.elements["ticker"].value   = stock.ticker ?? "";
    form.elements["shares"].value   = stock.shares ?? "";
    form.elements["unit_price"].value = stock.unit_price ?? "";
    form.elements["account"].value  = stock.account ?? "";
    form.elements["position"].value = stock.position ?? "買";

    // 上部タイトル（非編集）
    editModal.querySelector("#edit-name").textContent = stock.name || "—";
    editModal.querySelector("#edit-code").textContent = stock.ticker || "—";

    editModal.style.display = "block";
    editModal.setAttribute("aria-hidden", "false");
  };

  const openSellModal = stock => {
    if (!sellModal) return;
    const form = sellModal.querySelector("#sell-form");
    form.elements["stock_id"].value = stock.id ?? "";
    form.elements["name"].value     = stock.name ?? "";
    form.elements["shares"].value   = stock.shares ?? "";
    // 初期は市場価格（指値入力は非表示）
    const limitWrap = sellModal.querySelector('#limit-input-wrap');
    if (limitWrap) limitWrap.style.display = 'none';
    const marketRadio = form.querySelector('input[name="sell_mode"][value="market"]');
    if (marketRadio) marketRadio.checked = true;

    sellModal.style.display = "block";
    sellModal.setAttribute("aria-hidden", "false");
  };

  // -------------------------------
  // 株モーダル内のボタン
  // -------------------------------
  stockModal?.querySelector("#edit-stock-btn")?.addEventListener("click", e => {
    e.stopPropagation();
    const stockId = e.currentTarget.dataset.id;
    const card = document.querySelector(`.stock-card[data-id='${CSS.escape(stockId)}']`);
    if (!card) return;
    openEditModal({
      id: card.dataset.id,
      name: card.dataset.name,
      ticker: card.dataset.ticker,
      shares: card.dataset.shares,
      unit_price: card.dataset.unit_price,
      account: card.dataset.account,
      position: card.dataset.position
    });
  });

  stockModal?.querySelector("#sell-stock-btn")?.addEventListener("click", e => {
    e.stopPropagation();
    const stockId = e.currentTarget.dataset.id;
    const card = document.querySelector(`.stock-card[data-id='${CSS.escape(stockId)}']`);
    if (!card) return;
    openSellModal({
      id: card.dataset.id,
      name: card.dataset.name,
      shares: card.dataset.shares
    });
  });

  // -------------------------------
  // カード横スワイプ + アクション（編集/売却）
  // -------------------------------
  document.querySelectorAll(".stock-card").forEach(card => {
    let startX = 0, startY = 0, isDragging = false;
    let actions = card.querySelector(".card-actions");
    if (!actions) {
      actions = document.createElement("div");
      actions.className = "card-actions";
      actions.innerHTML = `
        <button class="edit-btn">編集</button>
        <button class="sell-btn">売却</button>
      `;
      card.appendChild(actions);
    }

    card.addEventListener("touchstart", e => {
      const t = e.touches[0];
      startX = t.pageX; startY = t.pageY; isDragging = true;
    }, { passive: true });

    card.addEventListener("touchend", e => {
      if (!isDragging) return;
      isDragging = false;
      const t = e.changedTouches[0];
      const deltaX = t.pageX - startX;
      const deltaY = t.pageY - startY;
      if (Math.abs(deltaY) > Math.abs(deltaX)) return; // 縦スクロール優先
      if (deltaX < -50) card.classList.add("swiped");   // 左スワイプで表示
      else if (deltaX > 50) card.classList.remove("swiped"); // 右スワイプで閉じる
    }, { passive: true });

    actions.querySelector(".edit-btn")?.addEventListener("click", e => {
      e.stopPropagation();
      openEditModal({
        id: card.dataset.id, name: card.dataset.name, ticker: card.dataset.ticker,
        shares: card.dataset.shares, unit_price: card.dataset.unit_price,
        account: card.dataset.account, position: card.dataset.position
      });
    });

    actions.querySelector(".sell-btn")?.addEventListener("click", e => {
      e.stopPropagation();
      openSellModal({
        id: card.dataset.id, name: card.dataset.name, shares: card.dataset.shares
      });
    });
  });

  // -------------------------------
  // フォーム送信（ここではサーバPOSTの代わりにイベント発火）
  // 実運用では fetch でエンドポイントに送信 or 通常POSTへ変更
  // -------------------------------
  const editForm = document.getElementById('edit-form');
  editForm?.addEventListener("submit", e => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(editForm).entries());
    document.dispatchEvent(new CustomEvent('stockEditSubmit', { detail: data }));
    editModal.style.display = "none";
  });

  const sellForm = document.getElementById('sell-form');
  sellForm?.addEventListener("submit", e => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(sellForm).entries());
    document.dispatchEvent(new CustomEvent('stockSellSubmit', { detail: data }));
    sellModal.style.display = "none";
  });

  // 売却モードの切替（市場/指値）
  sellModal?.addEventListener('change', (e) => {
    if (e.target.name !== 'sell_mode') return;
    const wrap = sellModal.querySelector('#limit-input-wrap');
    if (wrap) wrap.style.display = (e.target.value === 'limit') ? 'block' : 'none';
  });
});