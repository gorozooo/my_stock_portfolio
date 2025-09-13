// base.js — ローディング復活 + 下タブ/サブメニューを data-tabkey で厳密ひも付け
document.addEventListener("DOMContentLoaded", () => {
  /* =========================
     ローディング（最小CSSをインライン注入）
  ========================= */
  (function initLoader() {
    const style = document.createElement("style");
    style.innerHTML = `
      #loading-overlay{
        position:fixed; inset:0; z-index:9999;
        background:rgba(10,10,20,.95);
        display:none; opacity:0;
        transition:opacity .22s ease;
        display:flex; align-items:center; justify-content:center; flex-direction:column
      }
      #loading-overlay .loading-text{
        color:#0ff; font:700 22px/1.2 "Orbitron",system-ui;
        text-shadow:0 0 10px #0ff,0 0 20px #0ff
      }
      #loading-overlay .loading-bar{
        width:220px; height:6px; border-radius:4px; margin-top:12px;
        background:linear-gradient(90deg,#0ff,#f0f,#0ff); background-size:200% 100%;
        animation:loadslide 2s linear infinite
      }
      @keyframes loadslide { 0%{background-position:0 0} 100%{background-position:200% 0} }
    `;
    document.head.appendChild(style);

    const overlay = document.createElement("div");
    overlay.id = "loading-overlay";
    overlay.innerHTML = `
      <div class="loading-text">Now Loading…</div>
      <div class="loading-bar"></div>
    `;
    document.body.appendChild(overlay);

    function show(cb) {
      overlay.style.display = "flex";
      requestAnimationFrame(() => {
        overlay.style.opacity = "1";
        if (cb) setTimeout(cb, 40);
      });
    }
    function hide() {
      overlay.style.opacity = "0";
      setTimeout(() => { overlay.style.display = "none"; }, 200);
    }
    // グローバル公開
    window.__loader = { show, hide };

    // 初期表示 → window.load で閉じる
    show();
    window.addEventListener("load", hide, { passive: true });
    window.addEventListener("beforeunload", () => show(), { passive: true });
    window.addEventListener("pageshow", (e) => { if (e.persisted) hide(); }, { passive: true });
  })();

  // 以降、遷移はコレ経由にする
  function go(href) {
    if (!href || href.startsWith("#") || href.startsWith("javascript:")) return;
    if (window.__loader && typeof window.__loader.show === "function") {
      window.__loader.show(() => (window.location.href = href));
    } else {
      window.location.href = href;
    }
  }

  /* =========================
     下タブ & サブメニュー（data-tabkey で厳密リンク）
  ========================= */
  const tabBar = document.querySelector(".bottom-tab");
  if (!tabBar) return;

  // ケアレット行（タブの直下に並べる）
  let caretRow = document.querySelector(".caret-row");
  if (!caretRow) {
    caretRow = document.createElement("div");
    caretRow.className = "caret-row";
    // 下タブの直後に置くと高さ揃えが安定
    tabBar.insertAdjacentElement("afterend", caretRow);
  }

  // サブメニュー用ボタンバー（共用）
  let actionbar = document.querySelector(".tab-actionbar");
  if (!actionbar) {
    actionbar = document.createElement("div");
    actionbar.className = "tab-actionbar";
    document.body.appendChild(actionbar);
  }

  let openKey = null;
  const map = new Map(); // key -> { tab, link, submenu, caretBtn }

  function rebuild() {
    caretRow.innerHTML = "";
    map.clear();

    const tabs = Array.from(tabBar.querySelectorAll(".tab-item"));
    let seq = 0;

    tabs.forEach((tab) => {
      let key = tab.dataset.tabkey;
      if (!key) {
        key = `t${Date.now().toString(36)}_${(seq++).toString(36)}`;
        tab.dataset.tabkey = key;
      }

      // 古い飾りケアレットは除去
      tab
        .querySelectorAll(".tab-caret, .caret, .caret-icon, [data-caret], [data-role='caret']")
        .forEach((n) => n.remove());

      const link = tab.querySelector(".tab-link");
      const submenu = tab.querySelector(".sub-menu");

      // ケアレット列：常にタブ数ぶん作る（高さ揃え）
      const cell = document.createElement("div");
      cell.className = "caret-cell";

      let caretBtn = null;
      if (submenu) {
        caretBtn = document.createElement("button");
        caretBtn.type = "button";
        caretBtn.className = "caret-btn";
        caretBtn.textContent = "▾";
        caretBtn.setAttribute("aria-expanded", "false");
        caretBtn.dataset.tabkey = key;
        cell.appendChild(caretBtn);
      } else {
        const ph = document.createElement("div");
        ph.className = "caret-placeholder";
        cell.appendChild(ph);
      }
      caretRow.appendChild(cell);

      map.set(key, { tab, link, submenu, caretBtn });
    });

    // ケアレットで開閉
    map.forEach(({ caretBtn }, key) => {
      if (!caretBtn) return;
      caretBtn.onclick = (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (openKey === key) hideBar();
        else showBar(key);
      };
    });

    // タブ本体は通常遷移（常に go() 経由）
    map.forEach(({ link }) => {
      if (!link) return;
      link.onclick = (e) => {
        const href = link.getAttribute("href");
        const target = link.getAttribute("target") || "";
        if (!href || href.startsWith("#") || href.startsWith("javascript:") || target === "_blank") return;
        e.preventDefault();
        go(href);
      };
    });

    if (openKey && !map.has(openKey)) hideBar();
  }

  function showBar(key) {
    const rec = map.get(key);
    if (!rec || !rec.submenu) return;

    actionbar.innerHTML = "";
    const links = rec.submenu.querySelectorAll("a");
    if (links.length === 0) {
      const none = document.createElement("span");
      none.className = "ab-btn";
      none.textContent = "メニューなし";
      actionbar.appendChild(none);
    } else {
      links.forEach((a) => {
        const href = a.getAttribute("href") || "#";
        const label = (a.textContent || "").trim();
        const target = a.getAttribute("target") || "";
        const btn = document.createElement("a");
        btn.className = "ab-btn";
        btn.href = href;
        btn.textContent = label;
        btn.onclick = (e) => {
          if (!href || href.startsWith("#") || href.startsWith("javascript:") || target === "_blank") return;
          e.preventDefault();
          go(href);
        };
        actionbar.appendChild(btn);
      });
    }

    // ケアレット状態
    map.forEach(({ caretBtn }) => { if (caretBtn) caretBtn.setAttribute("aria-expanded", "false"); });
    if (rec.caretBtn) rec.caretBtn.setAttribute("aria-expanded", "true");

    // 表示（位置はCSS固定配置のため不要）
    actionbar.style.display = "flex";
    requestAnimationFrame(() => actionbar.classList.add("show"));
    openKey = key;
  }

  function hideBar() {
    actionbar.classList.remove("show");
    map.forEach(({ caretBtn }) => { if (caretBtn) caretBtn.setAttribute("aria-expanded", "false"); });
    setTimeout(() => {
      if (!actionbar.classList.contains("show")) actionbar.style.display = "none";
    }, 160);
    openKey = null;
  }

  // 外側クリック / Esc / リサイズで閉じる
  document.addEventListener("click", (e) => {
    if (!openKey) return;
    const inBar  = !!e.target.closest(".tab-actionbar");
    const inRow  = !!e.target.closest(".caret-row");
    const inTabs = !!e.target.closest(".bottom-tab");
    if (!inBar && !inRow && !inTabs) hideBar();
  }, { passive: true });

  window.addEventListener("keydown", (e) => { if (e.key === "Escape" && openKey) hideBar(); }, { passive: true });
  window.addEventListener("resize", hideBar, { passive: true });

  // タブDOMの変化に追従
  const mo = new MutationObserver(() => rebuild());
  mo.observe(tabBar, { childList: true, subtree: true });

  // 初期構築
  rebuild();
});