// 下タブ：必ず“押せるケアレット”を自動挿入し、そのボタンでのみサブメニュー開閉。
// 既存の飾り↓は無視されます。タブ本体(.tab-link)は通常遷移のまま。

document.addEventListener("DOMContentLoaded", function () {
  /* --- ローディング（必要なら残す） --- */
  (function () {
    const style = document.createElement("style");
    style.innerHTML = `
      #loading-overlay{position:fixed;inset:0;background:rgba(10,10,20,.95);
        display:none;opacity:0;transition:opacity .22s ease;z-index:9999;
        display:flex;align-items:center;justify-content:center;flex-direction:column}
      #loading-overlay .loading-text{color:#0ff;font:700 22px/1.2 "Orbitron",system-ui;
        text-shadow:0 0 10px #0ff,0 0 20px #0ff}
      #loading-overlay .loading-bar{width:220px;height:6px;border-radius:4px;margin-top:12px;
        background:linear-gradient(90deg,#0ff,#f0f,#0ff);background-size:200% 100%;
        animation:loadslide 2s linear infinite}
      @keyframes loadslide{0%{background-position:0 0}100%{background-position:200% 0}}
    `;
    document.head.appendChild(style);

    const loading = document.createElement("div");
    loading.id = "loading-overlay";
    loading.innerHTML = `<div class="loading-text">Now Loading…</div><div class="loading-bar"></div>`;
    document.body.appendChild(loading);

    function showLoading(cb){
      loading.style.display="flex";
      requestAnimationFrame(()=>{
        loading.style.opacity="1";
        if(cb) setTimeout(cb,40);
      });
    }
    function hideLoading(){
      loading.style.opacity="0";
      setTimeout(()=>{ loading.style.display="none"; },200);
    }
    window.__showLoading__ = showLoading;

    showLoading();
    window.addEventListener("load", hideLoading, {passive:true});
    window.addEventListener("beforeunload", ()=> showLoading(), {passive:true});
    window.addEventListener("pageshow", e=>{ if(e.persisted) hideLoading(); }, {passive:true});
  })();

  /* --- 下タブ & サブメニュー --- */
  const tabBar   = document.querySelector(".bottom-tab");
  const tabItems = document.querySelectorAll(".bottom-tab .tab-item");
  if (!tabBar || !tabItems.length) return;

  // アクションバー（共用）
  const actionbar = document.createElement("div");
  actionbar.className = "tab-actionbar";
  actionbar.id = "tab-actionbar";
  actionbar.setAttribute("role", "menu");
  document.body.appendChild(actionbar);

  let openFor = null;
  let justOpenedAt = 0;

  function closeBar() {
    if (openFor) {
      openFor.classList.remove("open");
      const caret = openFor.querySelector(".tab-caret-btn");
      if (caret) caret.setAttribute("aria-expanded", "false");
    }
    actionbar.classList.remove("show");
    setTimeout(() => {
      if (!actionbar.classList.contains("show")) {
        actionbar.style.display = "none";
        actionbar.innerHTML = "";
      }
    }, 140);
    openFor = null;
  }

  function openBarFor(tabItem) {
    const submenu = tabItem.querySelector(".sub-menu");
    if (!submenu) return;

    if (openFor && openFor !== tabItem) closeBar();

    // ボタン再生成
    actionbar.innerHTML = "";
    const links = submenu.querySelectorAll("a");
    if (links.length === 0) {
      const none = document.createElement("span");
      none.className = "ab-btn";
      none.textContent = "メニューなし";
      none.setAttribute("role", "menuitem");
      actionbar.appendChild(none);
    } else {
      links.forEach(a => {
        const href = a.getAttribute("href") || "#";
        const txt  = (a.textContent || "").trim();
        const target = a.getAttribute("target") || "";
        const btn  = document.createElement("a");
        btn.className = "ab-btn";
        btn.href = href;
        btn.textContent = txt;
        btn.setAttribute("role", "menuitem");

        btn.addEventListener("click", (e) => {
          // hash / javascript: はそのまま
          if (!href || href.startsWith("#") || href.startsWith("javascript:")) return;
          // 新規タブはローダーなし
          if (target === "_blank") return;

          e.preventDefault();
          (window.__showLoading__ || ((cb)=>cb()))(() => (window.location.href = href));
        }, {passive:false});

        actionbar.appendChild(btn);
      });
    }

    // 位置決め
    const rect = tabItem.getBoundingClientRect();
    if (window.matchMedia("(min-width: 768px)").matches) {
      const width = Math.min(560, Math.max(260, rect.width * 1.7));
      const left  = Math.min(Math.max(8, rect.left + rect.width/2 - width/2), window.innerWidth - width - 8);
      actionbar.style.left   = left + "px";
      actionbar.style.right  = "auto";
      actionbar.style.width  = width + "px";
      actionbar.style.bottom = (window.innerHeight - rect.top + 10) + "px";
    } else {
      actionbar.style.left   = "8px";
      actionbar.style.right  = "8px";
      actionbar.style.width  = "auto";
      actionbar.style.bottom = "calc(96px + env(safe-area-inset-bottom,0))";
    }

    tabItem.classList.add("open");
    const caret = tabItem.querySelector(".tab-caret-btn");
    if (caret) caret.setAttribute("aria-expanded", "true");

    actionbar.style.display = "flex";
    requestAnimationFrame(() => actionbar.classList.add("show"));

    openFor = tabItem;
    justOpenedAt = Date.now();
  }

  // すべてのタブ：サブメニュー有ならケアレットを必ず挿入
  tabItems.forEach(tab => {
    const submenu = tab.querySelector(".sub-menu");
    const link = tab.querySelector(".tab-link");

    if (!submenu) {
      // サブメニュー無し → 既存の飾りケアレットは排除
      tab.classList.remove("has-sub", "open");
      tab.querySelectorAll(".tab-caret-btn, .tab-caret, .caret, .caret-icon, [data-caret], [data-role='caret']").forEach(n => n.remove());
      // 通常遷移（変更なし）
      if (link) {
        link.addEventListener("click", (e) => {
          const href = link.getAttribute("href");
          const target = link.getAttribute("target") || "";
          if (!href || href.startsWith("#") || href.startsWith("javascript:")) return;
          if (target === "_blank") return;
          e.preventDefault();
          (window.__showLoading__ || ((cb)=>cb()))(() => (window.location.href = href));
        }, {passive:false});
      }
      return;
    }

    tab.classList.add("has-sub");

    // 既存飾りを削除して、押せるケアレットを注入（重複注入防止）
    tab.querySelectorAll(".tab-caret-btn, .tab-caret, .caret, .caret-icon, [data-caret], [data-role='caret']").forEach(n => n.remove());
    const caret = document.createElement("button");
    caret.type = "button";
    caret.className = "tab-caret-btn";
    caret.setAttribute("aria-expanded", "false");
    caret.setAttribute("aria-controls", "tab-actionbar");
    caret.setAttribute("aria-label", "サブメニューを開閉");
    caret.textContent = "▾";
    (link || tab).appendChild(caret);

    // ケアレットでのみ開閉
    caret.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (openFor === tab) closeBar(); else openBarFor(tab);
    }, {passive:false});

    // タブ本体は通常遷移
    if (link) {
      link.addEventListener("click", (e) => {
        const href = link.getAttribute("href");
        const target = link.getAttribute("target") || "";
        if (!href || href.startsWith("#") || href.startsWith("javascript:")) return;
        if (target === "_blank") return;
        e.preventDefault();
        (window.__showLoading__ || ((cb)=>cb()))(() => (window.location.href = href));
      }, {passive:false});
    }
  });

  // 外側クリック・ESC・リサイズで閉じる
  document.addEventListener("click", (e) => {
    if (!openFor) return;
    if (Date.now() - justOpenedAt < 160) return;
    const inTab = !!e.target.closest(".bottom-tab .tab-item");
    const inBar = !!e.target.closest(".tab-actionbar");
    if (!inTab && !inBar) closeBar();
  }, {passive:true});

  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && openFor) closeBar();
  }, {passive:true});

  window.addEventListener("resize", () => closeBar(), {passive:true});

  /* --- 現在ページ名（任意） --- */
  const currentURL = location.pathname;
  const currentPageNameEl = document.getElementById("current-page-name");
  if (currentPageNameEl) {
    const tabLinks = document.querySelectorAll(".tab-item .tab-link");
    let found = false;
    tabLinks.forEach(tl => {
      const href = tl.getAttribute("href");
      const nameSpan = tl.querySelector("span");
      if (href && nameSpan && currentURL.startsWith(href)) {
        currentPageNameEl.textContent = nameSpan.textContent;
        found = true;
      }
    });
    if (!found) currentPageNameEl.textContent = currentURL.replace(/^\/|\/$/g, "") || "ホーム";
  }
});