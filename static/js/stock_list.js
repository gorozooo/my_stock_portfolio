/* ==========================
   スマホファースト設計、HTML/CSS/JS分離
   タブ切替でセクション中央寄せ
   リロード/復帰/向き変更でも中央寄せを維持
   横スワイプでタブ同期（スクロール位置→タブ反映）
   モーダル：Esc/外側クリックで閉じる、フォーカストラップ
   カード：左スワイプでアクション表示、右スワイプで閉じる
   送信処理は後段に差し替え可能（fetch雛形同梱）
========================== */

document.addEventListener("DOMContentLoaded", () => {
  /* -------------------------------
   * 要素取得（存在チェックを丁寧に）
   * ----------------------------- */
  const tabs = Array.from(document.querySelectorAll(".broker-tab"));
  const wrapper = document.getElementById("broker-horizontal-wrapper");
  const sections = Array.from(document.querySelectorAll(".broker-section"));

  // モーダル
  const stockModal = document.getElementById("stock-modal");
  const editModal = document.getElementById("edit-modal");
  const sellModal = document.getElementById("sell-modal");

  // モーダル内フォーム（存在しないケースも考慮）
  const editForm = editModal?.querySelector("#edit-form") || null;
  const sellForm = sellModal?.querySelector("#sell-form") || null;

  if (!wrapper || sections.length === 0) return; // 早期リターン

  /* -------------------------------
   * util: HTMLエスケープ
   * ----------------------------- */
  const escapeHTML = (str) =>
    String(str ?? "").replace(/[&<>"']/g, (m) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[m]));

  /* -------------------------------
   * スクロール：対象セクションを中央に寄せる
   * ----------------------------- */
  const scrollToSectionCenter = (index, smooth = true) => {
    const target = sections[index];
    if (!target) return;
    const wrapperWidth   = wrapper.clientWidth;
    const sectionRect    = target.getBoundingClientRect();
    const wrapperRect    = wrapper.getBoundingClientRect();
    const sectionLeftAbs = sectionRect.left - wrapperRect.left + wrapper.scrollLeft;
    let left = sectionLeftAbs - (wrapperWidth / 2) + (sectionRect.width / 2);
    const max = wrapper.scrollWidth - wrapperWidth;
    if (left < 0) left = 0;
    if (left > max) left = max;
    wrapper.scrollTo({ left, behavior: smooth ? "smooth" : "auto" });
  };

  /* -------------------------------
   * タブのアクティブ切り替え + 中央寄せ + 永続化
   * ----------------------------- */
  const setActiveTab = (index, opts = { scroll: true, smooth: true, save: true }) => {
    tabs.forEach((t) => t.classList.remove("active"));
    if (tabs[index]) tabs[index].classList.add("active");
    if (opts.scroll) scrollToSectionCenter(index, opts.smooth);
    if (opts.save)   localStorage.setItem("activeBrokerIndex", String(index));
  };

  /* -------------------------------
   * 起動時：前回タブを復元（存在しなければ0）
   * ----------------------------- */
  const clampIndex = (i) => Math.min(Math.max(i, 0), sections.length - 1);
  const savedIndex = clampIndex(parseInt(localStorage.getItem("activeBrokerIndex") ?? "0", 10));
  // レイアウト確定後の自然な中央寄せ（小さなタイムアウト）
  setTimeout(() => setActiveTab(savedIndex, { scroll: true, smooth: false, save: false }), 80);

  /* -------------------------------
   * タブクリック/キーボード操作
   * ----------------------------- */
  tabs.forEach((tab, i) => {
    tab.addEventListener("click", () => setActiveTab(i));
    tab.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault(); setActiveTab(i);
      }
      if (e.key === "ArrowRight") setActiveTab(clampIndex(i + 1));
      if (e.key === "ArrowLeft")  setActiveTab(clampIndex(i - 1));
    });
  });

  /* -------------------------------
   * 横スワイプ時：見えているセクションを自動でタブに反映
   *  - IntersectionObserver で最も中央に近い要素を検出
   * ----------------------------- */
  const io = new IntersectionObserver((entries) => {
    // 現在viewportに入っているセクションを中心位置に近い順にソート
    const centerX = wrapper.scrollLeft + (wrapper.clientWidth / 2);
    const visible = entries
      .filter(e => e.isIntersecting)
      .map(e => {
        const el = e.target;
        const rect = el.getBoundingClientRect();
        const leftAbs = rect.left - wrapper.getBoundingClientRect().left + wrapper.scrollLeft;
        const mid = leftAbs + rect.width / 2;
        return { el, dist: Math.abs(mid - centerX) };
      })
      .sort((a, b) => a.dist - b.dist);

    if (visible.length) {
      const index = sections.indexOf(visible[0].el);
      if (index >= 0) setActiveTab(index, { scroll: false, smooth: false, save: true });
    }
  }, {
    root: wrapper,
    threshold: 0.6,  // セクションの6割以上見えたら「対象」とする
  });

  sections.forEach(sec => io.observe(sec));

  /* -------------------------------
   * 画面復帰/向き変更/リサイズでも中央寄せ維持
   * ----------------------------- */
  const reCenter = () => {
    const idx = clampIndex(parseInt(localStorage.getItem("activeBrokerIndex") ?? "0", 10));
    scrollToSectionCenter(idx, false);
  };
  window.addEventListener("pageshow", (e) => { if (e.persisted) reCenter(); });
  window.addEventListener("orientationchange", () => setTimeout(reCenter, 60));
  let resizeTimer = 0;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(reCenter, 100);
  });

  /* -------------------------------
   * モーダル共通：開閉/ESC/外側クリック
   *  + フォーカストラップ（アクセシビリティ）
   * ----------------------------- */
  const modals = [stockModal, editModal, sellModal].filter(Boolean);

  const openModal = (modal) => {
    modal.style.display = "block";
    modal.setAttribute("aria-hidden", "false");
    // 最初のフォーカス対象
    const focusable = modal.querySelectorAll("button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])");
    (focusable[0] || modal).focus();
    modal.dataset.open = "1";
  };
  const closeModal = (modal) => {
    modal.style.display = "none";
    modal.setAttribute("aria-hidden", "true");
    modal.dataset.open = "";
  };

  const setupModal = (modal) => {
    const closeBtn = modal.querySelector(".modal-close");
    closeBtn?.addEventListener("click", () => closeModal(modal));
    modal.addEventListener("click", (e) => { if (e.target === modal) closeModal(modal); });
    // フォーカストラップ
    modal.addEventListener("keydown", (e) => {
      if (e.key === "Escape") return closeModal(modal);
      if (e.key !== "Tab") return;
      const f = Array.from(modal.querySelectorAll("button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])"))
        .filter(el => !el.hasAttribute("disabled"));
      if (!f.length) return;
      const first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    });
  };

  modals.forEach(setupModal);

  /* -------------------------------
   * 株カードクリック→詳細モーダル
   *  - 「スワイプ中(swiped)」は無視
   * ----------------------------- */
  document.querySelectorAll(".stock-card").forEach(card => {
    const cardId = card.dataset.id;

    card.addEventListener("click", () => {
      if (!stockModal) return;
      if (card.classList.contains("swiped")) return; // スワイプ表示中はカードタップで詳細を開かない

      const modalBody   = stockModal.querySelector("#modal-body");
      const modalEditBtn= stockModal.querySelector("#edit-stock-btn");
      const modalSellBtn= stockModal.querySelector("#sell-stock-btn");

      modalBody.innerHTML = `
        <h3 id="modal-title">${escapeHTML(card.dataset.name)} (${escapeHTML(card.dataset.ticker)})</h3>
        <div class="stock-detail-rows">
          <p><span>株数</span><span>${escapeHTML(card.dataset.shares)}株</span></p>
          <p><span>取得単価</span><span>¥${escapeHTML(card.dataset.unit_price)}</span></p>
          <p><span>現在株価</span><span>¥${escapeHTML(card.dataset.current_price)}</span></p>
          <p class="${Number(card.dataset.profit) >= 0 ? "positive" : "negative"}">
            <span>損益</span><span>¥${escapeHTML(card.dataset.profit)} (${escapeHTML(card.dataset.profit_rate)}%)</span>
          </p>
        </div>
      `;
      modalEditBtn.dataset.id = cardId;
      modalSellBtn.dataset.id = cardId;
      openModal(stockModal);
    });

    // キーボードでも開ける
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); card.click(); }
    });
  });

  /* -------------------------------
   * 編集/売却モーダルを開く関数
   *  - 見出し（編集モーダル上部に非編集で表示）も更新
   * ----------------------------- */
  const openEditModalWith = (stock) => {
    if (!editModal) return;

    // 見出し（非編集）：銘柄名 + コード
    const hName = editModal.querySelector("#edit-name");
    const hCode = editModal.querySelector("#edit-code");
    if (hName) hName.textContent = stock.name || "";
    if (hCode) hCode.textContent = stock.ticker || "";

    // フォーム要素
    if (editForm) {
      editForm.elements["stock_id"].value   = stock.id || "";
      editForm.elements["name"].value       = stock.name || "";   // hidden
      editForm.elements["ticker"].value     = stock.ticker || ""; // hidden
      editForm.elements["shares"].value     = stock.shares || "";
      editForm.elements["unit_price"].value = stock.unit_price || "";
      editForm.elements["account"].value    = stock.account || "";
      editForm.elements["position"].value   = stock.position || "買";
      // 最初の編集項目にフォーカス
      editForm.elements["shares"]?.focus();
    }

    openModal(editModal);
  };

  const openSellModalWith = (stock) => {
    if (!sellModal || !sellForm) return;
    sellForm.elements["stock_id"].value = stock.id || "";
    sellForm.elements["name"].value     = stock.name || "";
    sellForm.elements["shares"].value   = stock.shares || "";
    sellForm.elements["shares"]?.focus();
    openModal(sellModal);
  };

  // 詳細モーダル内のボタン
  stockModal?.querySelector("#edit-stock-btn")?.addEventListener("click", (e) => {
    e.stopPropagation();
    const stockId = e.currentTarget.dataset.id;
    const card = document.querySelector(`.stock-card[data-id='${stockId}']`);
    if (!card) return;
    openEditModalWith({
      id: card.dataset.id,
      name: card.dataset.name,
      ticker: card.dataset.ticker,
      shares: card.dataset.shares,
      unit_price: card.dataset.unit_price,
      account: card.dataset.account,
      position: card.dataset.position
    });
  });

  stockModal?.querySelector("#sell-stock-btn")?.addEventListener("click", (e) => {
    e.stopPropagation();
    const stockId = e.currentTarget.dataset.id;
    const card = document.querySelector(`.stock-card[data-id='${stockId}']`);
    if (!card) return;
    openSellModalWith({
      id: card.dataset.id,
      name: card.dataset.name,
      shares: card.dataset.shares
    });
  });

  /* -------------------------------
   * カード横スワイプ + アクション（編集/売却）
   * ----------------------------- */
  document.querySelectorAll(".stock-card").forEach(card => {
    let startX = 0, startY = 0, isDragging = false;

    // アクション領域が無ければ生成（編集/売却ボタン）
    let actions = card.querySelector(".card-actions");
    if (!actions) {
      actions = document.createElement("div");
      actions.className = "card-actions";
      actions.innerHTML = `
        <button class="edit-btn" type="button" aria-label="編集">編集</button>
        <button class="sell-btn" type="button" aria-label="売却">売却</button>`;
      card.appendChild(actions);
    }

    // スワイプジェスチャー（縦スクロール優先のため、Y移動優勢なら無視）
    card.addEventListener("touchstart", (e) => {
      const t = e.touches[0];
      startX = t.pageX; startY = t.pageY; isDragging = true;
    }, { passive: true });

    card.addEventListener("touchend", (e) => {
      if (!isDragging) return;
      isDragging = false;
      const t = e.changedTouches[0];
      const dx = t.pageX - startX;
      const dy = t.pageY - startY;
      if (Math.abs(dy) > Math.abs(dx)) return; // 縦優勢＝スワイプ扱いしない
      if (dx < -50) card.classList.add("swiped");   // 左スワイプで表示
      else if (dx > 50) card.classList.remove("swiped"); // 右スワイプで閉じる
    }, { passive: true });

    // アクションボタン：編集
    actions.querySelector(".edit-btn")?.addEventListener("click", (e) => {
      e.stopPropagation();
      openEditModalWith({
        id: card.dataset.id,
        name: card.dataset.name,
        ticker: card.dataset.ticker,
        shares: card.dataset.shares,
        unit_price: card.dataset.unit_price,
        account: card.dataset.account,
        position: card.dataset.position
      });
    });
    // アクションボタン：売却
    actions.querySelector(".sell-btn")?.addEventListener("click", (e) => {
      e.stopPropagation();
      openSellModalWith({
        id: card.dataset.id,
        name: card.dataset.name,
        shares: card.dataset.shares
      });
    });
  });

  /* -------------------------------
   * フォーム送信（雛形）
   *  - 実運用ではURL/CSRFトークン等を差し替え
   *  - 成功後はモーダルを閉じ、必要に応じてUI更新
   * ----------------------------- */
  const getCsrf = () => {
    const el = document.querySelector("input[name='csrfmiddlewaretoken']");
    return el?.value || "";
  };

  // 編集フォーム
  if (editForm) {
    editForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const data = Object.fromEntries(new FormData(editForm).entries());
      try {
        // 送信例：/stocks/<id>/edit/ にPOST（要バックエンド実装）
        /*
        const res = await fetch(`/stocks/${encodeURIComponent(data.stock_id)}/edit/`, {
          method: "POST",
          headers: { "X-CSRFToken": getCsrf() },
          body: new FormData(editForm)
        });
        if (!res.ok) throw new Error("保存に失敗しました");
        */
        // デモ：即時成功扱い → モーダル閉じる
        closeModal(editModal);
        // 画面の数値を最低限更新（株数/単価）
        const card = document.querySelector(`.stock-card[data-id='${CSS.escape(data.stock_id)}']`);
        if (card) {
          card.dataset.shares = data.shares;
          card.dataset.unit_price = data.unit_price;
          // 表示中のテキストも更新（任意）
          const rows = card.querySelectorAll(".stock-row");
          rows.forEach(row => {
            const label = row.querySelector("span:first-child")?.textContent?.trim();
            if (label === "株数") row.querySelector("span:last-child").textContent = `${data.shares}株`;
            if (label === "取得単価") row.querySelector("span:last-child").textContent = `${Number(data.unit_price).toLocaleString()}円`;
          });
        }
      } catch (err) {
        console.error(err);
        alert("保存に失敗しました。通信環境をご確認ください。");
      }
    });
    // キャンセル
    editModal?.querySelector("#edit-cancel-btn")?.addEventListener("click", () => closeModal(editModal));
  }

  // 売却フォーム
  if (sellForm) {
    sellForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const data = Object.fromEntries(new FormData(sellForm).entries());
      try {
        // 送信例：/stocks/<id>/sell/ にPOST（要バックエンド実装）
        /*
        const res = await fetch(`/stocks/${encodeURIComponent(data.stock_id)}/sell/`, {
          method: "POST",
          headers: { "X-CSRFToken": getCsrf() },
          body: new FormData(sellForm)
        });
        if (!res.ok) throw new Error("売却に失敗しました");
        */
        closeModal(sellModal);
        // デモ：即時削除
        document.querySelector(`.stock-card[data-id='${CSS.escape(data.stock_id)}']`)?.closest(".stock-card-wrapper")?.remove();
      } catch (err) {
        console.error(err);
        alert("売却に失敗しました。通信環境をご確認ください。");
      }
    });
    // キャンセル
    sellModal?.querySelector("#sell-cancel-btn")?.addEventListener("click", () => closeModal(sellModal));
  }
});