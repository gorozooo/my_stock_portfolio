/* ==========================
   スマホファースト設計、HTML/CSS/JS分離
   “一部が表示されない”を防ぐための防御実装付き
   - データ欠落の無害化（safeGet）
   - ローカルストレージの範囲外をクランプ
   - 高さ/スクロールの再計算を随所で実施
   - 例外捕捉でJS停止を防ぐ
========================== */

document.addEventListener("DOMContentLoaded", () => {
  const DEBUG = false; // ← true にすると簡易デバッグバッジとログが出ます

  /* -------------------------------
   * 安全に dataset 文字列を取得する util
   * ----------------------------- */
  const safeGet = (el, key, fallback = "") => {
    if (!el) return fallback;
    const v = el.dataset?.[key];
    // "None" や "null" が来た場合も空扱いにする
    if (v === undefined || v === null) return fallback;
    if (String(v).toLowerCase() === "none" || String(v).toLowerCase() === "null") return fallback;
    return String(v);
  };

  /* -------------------------------
   * タブ & セクション取得
   * ----------------------------- */
  const tabs     = Array.from(document.querySelectorAll(".broker-tab"));
  const wrapper  = document.getElementById("broker-horizontal-wrapper");
  const sections = Array.from(document.querySelectorAll(".broker-section"));
  if (!wrapper || sections.length === 0) return;

  // ビュー幅に合わせた中央寄せ
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

  const clampIndex = (i) => Math.min(Math.max(i, 0), sections.length - 1);

  const setActiveTab = (index, { scroll = true, smooth = true, save = true } = {}) => {
    index = clampIndex(index);
    tabs.forEach(t => t.classList.remove("active"));
    if (tabs[index]) tabs[index].classList.add("active");
    if (scroll) scrollToSectionCenter(index, smooth);
    if (save)   localStorage.setItem("activeBrokerIndex", String(index));
  };

  // 起動時：保存 index を安全に復元
  const savedIndexRaw = parseInt(localStorage.getItem("activeBrokerIndex") ?? "0", 10);
  const savedIndex = isNaN(savedIndexRaw) ? 0 : clampIndex(savedIndexRaw);
  setTimeout(() => setActiveTab(savedIndex, { scroll: true, smooth: false, save: false }), 80);

  // タブ操作
  tabs.forEach((tab, i) => {
    tab.addEventListener("click", () => setActiveTab(i));
    tab.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setActiveTab(i); }
      if (e.key === "ArrowRight") setActiveTab(clampIndex(i + 1));
      if (e.key === "ArrowLeft")  setActiveTab(clampIndex(i - 1));
    });
  });

  // セクション可視範囲→タブに反映（見切れで「無い」ように見える問題を軽減）
  const io = new IntersectionObserver((entries) => {
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
      const idx = sections.indexOf(visible[0].el);
      if (idx >= 0) setActiveTab(idx, { scroll: false, smooth: false, save: true });
    }
  }, { root: wrapper, threshold: 0.6 });
  sections.forEach(sec => io.observe(sec));

  // 画面復帰/リサイズ/向き変更で中央寄せ再計算
  const reCenter = () => {
    const idx = clampIndex(parseInt(localStorage.getItem("activeBrokerIndex") ?? "0", 10));
    scrollToSectionCenter(idx, false);
  };
  window.addEventListener("pageshow", (e) => { if (e.persisted) reCenter(); });
  window.addEventListener("orientationchange", () => setTimeout(reCenter, 60));
  let resizeTimer = 0;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(reCenter, 120);
  });

  /* -------------------------------
   * モーダル共通（安全に開閉）
   * ----------------------------- */
  const stockModal = document.getElementById("stock-modal");
  const editModal  = document.getElementById("edit-modal");
  const sellModal  = document.getElementById("sell-modal");

  const openModal = (modal) => {
    if (!modal) return;
    modal.style.display = "block";
    modal.setAttribute("aria-hidden", "false");
    const focusable = modal.querySelectorAll("button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])");
    (focusable[0] || modal).focus();
    modal.dataset.open = "1";
  };
  const closeModal = (modal) => {
    if (!modal) return;
    modal.style.display = "none";
    modal.setAttribute("aria-hidden", "true");
    modal.dataset.open = "";
  };
  const setupModal = (modal) => {
    if (!modal) return;
    const closeBtn = modal.querySelector(".modal-close");
    closeBtn?.addEventListener("click", () => closeModal(modal));
    modal.addEventListener("click", (e) => { if (e.target === modal) closeModal(modal); });
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
  [stockModal, editModal, sellModal].forEach(setupModal);

  /* -------------------------------
   * カード → 詳細モーダル
   * ----------------------------- */
  document.querySelectorAll(".stock-card").forEach(card => {
    const cardId = safeGet(card, "id");
    card.addEventListener("click", () => {
      try {
        if (!stockModal) return;
        if (card.classList.contains("swiped")) return;
        const modalBody    = stockModal.querySelector("#modal-body");
        const modalEditBtn = stockModal.querySelector("#edit-stock-btn");
        const modalSellBtn = stockModal.querySelector("#sell-stock-btn");

        const name          = safeGet(card, "name", "—");
        const ticker        = safeGet(card, "ticker", "—");
        const shares        = safeGet(card, "shares", "0");
        const unit_price    = safeGet(card, "unit_price", "0");
        const current_price = safeGet(card, "current_price", unit_price);
        const profit        = safeGet(card, "profit", "0");
        const profit_rate   = safeGet(card, "profit_rate", "0");

        modalBody.innerHTML = `
          <h3 id="modal-title">${escapeHTML(name)} (${escapeHTML(ticker)})</h3>
          <div class="stock-detail-rows">
            <p><span>株数</span><span>${escapeHTML(shares)}株</span></p>
            <p><span>取得単価</span><span>¥${escapeHTML(unit_price)}</span></p>
            <p><span>現在株価</span><span>¥${escapeHTML(current_price)}</span></p>
            <p class="${Number(profit) >= 0 ? "positive" : "negative"}">
              <span>損益</span><span>¥${escapeHTML(profit)} (${escapeHTML(profit_rate)}%)</span>
            </p>
          </div>
        `;
        if (modalEditBtn) modalEditBtn.dataset.id = cardId;
        if (modalSellBtn) modalSellBtn.dataset.id = cardId;
        openModal(stockModal);
      } catch (err) {
        console.error("[modal-open] error:", err);
      }
    });
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); card.click(); }
    });
  });

  /* -------------------------------
   * 編集/売却モーダル（開く）
   * ----------------------------- */
  const editForm = editModal?.querySelector("#edit-form") || null;
  const sellForm = sellModal?.querySelector("#sell-form") || null;

  const openEditModalWith = (stock) => {
    if (!editModal) return;
    const hName = editModal.querySelector("#edit-name");
    const hCode = editModal.querySelector("#edit-code");
    if (hName) hName.textContent = stock.name || "";
    if (hCode) hCode.textContent = stock.ticker || "";
    if (editForm) {
      editForm.elements["stock_id"].value   = stock.id || "";
      editForm.elements["name"].value       = stock.name || "";
      editForm.elements["ticker"].value     = stock.ticker || "";
      editForm.elements["shares"].value     = stock.shares || "";
      editForm.elements["unit_price"].value = stock.unit_price || "";
      editForm.elements["account"].value    = stock.account || "";
      editForm.elements["position"].value   = stock.position || "買";
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

  stockModal?.querySelector("#edit-stock-btn")?.addEventListener("click", (e) => {
    e.stopPropagation();
    const stockId = e.currentTarget.dataset.id;
    const card = document.querySelector(`.stock-card[data-id='${CSS.escape(stockId)}']`);
    if (!card) return;
    openEditModalWith({
      id: safeGet(card, "id"),
      name: safeGet(card, "name"),
      ticker: safeGet(card, "ticker"),
      shares: safeGet(card, "shares"),
      unit_price: safeGet(card, "unit_price"),
      account: safeGet(card, "account"),
      position: safeGet(card, "position", "買")
    });
  });

  stockModal?.querySelector("#sell-stock-btn")?.addEventListener("click", (e) => {
    e.stopPropagation();
    const stockId = e.currentTarget.dataset.id;
    const card = document.querySelector(`.stock-card[data-id='${CSS.escape(stockId)}']`);
    if (!card) return;
    openSellModalWith({
      id: safeGet(card, "id"),
      name: safeGet(card, "name"),
      shares: safeGet(card, "shares")
    });
  });

  /* -------------------------------
   * カード横スワイプ + アクション
   * ----------------------------- */
  document.querySelectorAll(".stock-card").forEach(card => {
    let startX = 0, startY = 0, isDragging = false;
    let actions = card.querySelector(".card-actions");
    if (!actions) {
      actions = document.createElement("div");
      actions.className = "card-actions";
      actions.innerHTML = `
        <button class="edit-btn" type="button" aria-label="編集">編集</button>
        <button class="sell-btn" type="button" aria-label="売却">売却</button>`;
      card.appendChild(actions);
    }

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
      if (Math.abs(dy) > Math.abs(dx)) return;
      if (dx < -50) card.classList.add("swiped");
      else if (dx > 50) card.classList.remove("swiped");
    }, { passive: true });

    actions.querySelector(".edit-btn")?.addEventListener("click", (e) => {
      e.stopPropagation();
      openEditModalWith({
        id: safeGet(card, "id"),
        name: safeGet(card, "name"),
        ticker: safeGet(card, "ticker"),
        shares: safeGet(card, "shares"),
        unit_price: safeGet(card, "unit_price"),
        account: safeGet(card, "account"),
        position: safeGet(card, "position", "買")
      });
    });

    actions.querySelector(".sell-btn")?.addEventListener("click", (e) => {
      e.stopPropagation();
      openSellModalWith({
        id: safeGet(card, "id"),
        name: safeGet(card, "name"),
        shares: safeGet(card, "shares")
      });
    });
  });

  /* -------------------------------
   * 送信（雛形）：fetchに置き換え可能
   * ----------------------------- */
  const getCsrf = () => document.querySelector("input[name='csrfmiddlewaretoken']")?.value || "";

  if (editForm) {
    editForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        // 実運用：fetch(editForm.action, { method:'POST', headers:{'X-CSRFToken': getCsrf()}, body: new FormData(editForm) })
        const data = Object.fromEntries(new FormData(editForm).entries());
        closeModal(editModal);
        // 画面の最低限更新（例外が出てもアプリが止まらないよう try 内で）
        const card = document.querySelector(`.stock-card[data-id='${CSS.escape(data.stock_id)}']`);
        if (card) {
          card.dataset.shares = data.shares ?? "";
          card.dataset.unit_price = data.unit_price ?? "";
          // 表示テキストも更新
          card.querySelectorAll(".stock-row").forEach(row => {
            const label = row.querySelector("span:first-child")?.textContent?.trim();
            if (label === "株数")        row.querySelector("span:last-child").textContent = `${data.shares}株`;
            if (label === "取得単価")    row.querySelector("span:last-child").textContent = `${Number(data.unit_price||0).toLocaleString()}円`;
          });
        }
      } catch (err) {
        console.error("[edit submit] error:", err);
        alert("保存に失敗しました。通信環境をご確認ください。");
      }
    });
    editModal?.querySelector("#edit-cancel-btn")?.addEventListener("click", () => closeModal(editModal));
  }

  if (sellForm) {
    sellForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        // 実運用：fetch(sellForm.action, { method:'POST', headers:{'X-CSRFToken': getCsrf()}, body: new FormData(sellForm) })
        const data = Object.fromEntries(new FormData(sellForm).entries());
        closeModal(sellModal);
        document.querySelector(`.stock-card[data-id='${CSS.escape(data.stock_id)}']`)?.closest(".stock-card-wrapper")?.remove();
      } catch (err) {
        console.error("[sell submit] error:", err);
        alert("売却に失敗しました。通信環境をご確認ください。");
      }
    });
    sellModal?.querySelector("#sell-cancel-btn")?.addEventListener("click", () => closeModal(sellModal));
  }

  /* -------------------------------
   * 簡易デバッグ（見えないときの可視化支援）
   * ----------------------------- */
  if (DEBUG) {
    // 各セクションごとのカード枚数をバッジ表示
    sections.forEach((sec, i) => {
      const c = sec.querySelectorAll(".stock-card").length;
      const badge = document.createElement("div");
      badge.textContent = `#${i} : ${c} cards`;
      Object.assign(badge.style, {
        position: "sticky", top: "0", left: "0", zIndex: "5",
        background: "rgba(0,123,255,.6)", color: "#fff",
        fontSize: "12px", padding: "2px 6px", borderRadius: "0 0 6px 0"
      });
      sec.prepend(badge);
    });
    console.log("[DEBUG] sections:", sections.length, "tabs:", tabs.length);
  }
});