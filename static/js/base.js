// base.js — Loader v2（以前の体感に近い）+ 下タブ/サブメニュー（data-tabkeyで厳密ひも付け）
// 使い方：このファイルをそのまま差し替え

document.addEventListener("DOMContentLoaded", () => {
  /* =========================
     Loader v2（クリック時だけ表示/チラつき防止）
  ========================= */
  const LOADER_CFG = {
    showOnInitialLoad: false,   // 初回ロードでも出したい場合は true
    showOnBeforeUnload: true,   // 離脱時にできるだけ表示
    delayBeforeShowMs: 120,     // 表示までの遅延（短い遷移は不表示に）
    minVisibleMs: 360           // 一度出たら最低表示時間
  };

  (function initLoader() {
    // CSS
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
      @keyframes loadslide{0%{background-position:0 0}100%{background-position:200% 0}}
    `;
    document.head.appendChild(style);

    // DOM
    const overlay = document.createElement("div");
    overlay.id = "loading-overlay";
    overlay.innerHTML = `
      <div class="loading-text">Now Loading…</div>
      <div class="loading-bar"></div>
    `;
    document.body.appendChild(overlay);

    let delayTimer = null;
    let visibleAt = 0;
    let isShowing = false;

    function reallyShow(cb) {
      overlay.style.display = "flex";
      requestAnimationFrame(() => {
        overlay.style.opacity = "1";
        isShowing = true;
        visibleAt = performance.now();
        if (cb) cb();
      });
    }
    function show(cb) {
      clearTimeout(delayTimer);
      delayTimer = setTimeout(() => reallyShow(cb), LOADER_CFG.delayBeforeShowMs);
    }
    function hide(force = false) {
      clearTimeout(delayTimer);
      if (!isShowing) { overlay.style.display = "none"; return; }
      const elapsed = performance.now() - visibleAt;
      const wait = force ? 0 : Math.max(0, LOADER_CFG.minVisibleMs - elapsed);
      setTimeout(() => {
        overlay.style.opacity = "0";
        setTimeout(() => { overlay.style.display = "none"; isShowing = false; }, 200);
      }, wait);
    }

    // 公開
    window.__loader = { show, hide };

    // 初回は出さない（設定で切替）
    if (LOADER_CFG.showOnInitialLoad) {
      show();
      window.addEventListener("load", () => hide(), { passive: true });
    } else {
      window.addEventListener("pageshow", (e) => { if (e.persisted) hide(true); }, { passive: true });
    }

    if (LOADER_CFG.showOnBeforeUnload) {
      window.addEventListener("beforeunload", () => {
        clearTimeout(delayTimer);
        reallyShow();
      }, { passive: true });
    }

    // 全フォーム送信時にもローダー
    document.addEventListener("submit", () => show(), true);

    // aタグの通常遷移を go() に差し替えたい場合（全体）
    document.addEventListener("click", (e) => {
      const a = e.target.closest('a[href]');
      if (!a) return;
      // タブ本体やサブメニューは別途ハンドルしているので除外
      if (a.closest('.bottom-tab') || a.closest('.tab-actionbar')) return;
      const href = a.getAttribute('href');
      const target = a.getAttribute('target') || '';
      if (!href || href.startsWith('#') || href.startsWith('javascript:') || target === '_blank') return;
      if (a.classList.contains('no-loader')) return;
      e.preventDefault();
      go(href);
    }, true);
  })();

  // 遷移ヘルパ
  function go(href) {
    if (!href || href.startsWith("#") || href.startsWith("javascript:")) return;
    if (window.__loader) window.__loader.show(() => (window.location.href = href));
    else window.location.href = href;
  }

  /* =========================
     下タブ & サブメニュー（data-tabkeyで厳密ひも付け）
     - ケアレット行は .bottom-tab の直下に 1 行表示
     - サブメニューはボタンバー(.tab-actionbar)で表示
  ========================= */
  const tabBar = document.querySelector(".bottom-tab");
  if (!tabBar) return;

  // ケアレット行（タブの直後に配置）
  let caretRow = document.querySelector(".caret-row");
  if (!caretRow) {
    caretRow = document.createElement("div");
    caretRow.className = "caret-row";
    tabBar.insertAdjacentElement("afterend", caretRow);
  }

  // サブメニュー用ボタンバー（共用）
  let actionbar = document.querySelector(".tab-actionbar");
  if (!actionbar) {
    actionbar = document.createElement("div");
    actionbar.className = "tab-actionbar";
    actionbar.setAttribute("role", "menu");
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

      // 古い飾りケアレットは除去（テンプレ差異吸収）
      tab.querySelectorAll(".tab-caret, .caret, .caret-icon, [data-caret], [data-role='caret']").forEach(n => n.remove());

      const link = tab.querySelector(".tab-link");
      const submenu = tab.querySelector(".sub-menu");

      // ケアレット列：常にタブ数ぶん作る（高さ揃えのため）
      const cell = document.createElement("div");
      cell.className = "caret-cell";

      let caretBtn = null;
      if (submenu) {
        tab.classList.add("has-sub");
        caretBtn = document.createElement("button");
        caretBtn.type = "button";
        caretBtn.className = "caret-btn";
        caretBtn.textContent = "▾";
        caretBtn.setAttribute("aria-expanded", "false");
        caretBtn.dataset.tabkey = key;
        cell.appendChild(caretBtn);
      } else {
        tab.classList.remove("has-sub");
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

    // タブ本体は通常遷移（go 経由）
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

    // 既に開いていたキーが消えていたら閉じる
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
      none.setAttribute("role", "menuitem");
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
        btn.setAttribute("role", "menuitem");
        btn.onclick = (e) => {
          if (!href || href.startsWith("#") || href.startsWith("javascript:") || target === "_blank") return;
          e.preventDefault();
          go(href);
        };
        actionbar.appendChild(btn);
      });
    }

    // すべてのケアレットを collapsed に
    map.forEach(({ caretBtn }) => { if (caretBtn) caretBtn.setAttribute("aria-expanded", "false"); });
    if (rec.caretBtn) rec.caretBtn.setAttribute("aria-expanded", "true");

    // 表示
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

  // タブDOMの変化に追従（テンプレの差異や動的追加にも強い）
  const mo = new MutationObserver(() => rebuild());
  mo.observe(tabBar, { childList: true, subtree: true });

  // 初期構築
  rebuild();
});