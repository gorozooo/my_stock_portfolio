// 下タブ：全タブにケアレット領域を用意（サブメニュー無しは透明プレースホルダー）。
// 重なり防止のため、各タブは「上=ボタン固定高」「下=ケアレット固定高」の2段構成。
// サブメニューはボタンバーとして、下タブの実高さ + 余白に追従表示。

document.addEventListener("DOMContentLoaded", function () {
  /* --- 軽量ローディング（必要なら残す） --- */
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
      requestAnimationFrame(()=>{ loading.style.opacity="1"; if(cb) setTimeout(cb,40); });
    }
    function hideLoading(){ loading.style.opacity="0"; setTimeout(()=>{ loading.style.display="none"; },200); }

    window.__showLoading__ = showLoading;
    showLoading(); window.addEventListener("load", hideLoading, {passive:true});
    window.addEventListener("beforeunload", ()=> showLoading(), {passive:true});
    window.addEventListener("pageshow", e=>{ if(e.persisted) hideLoading(); }, {passive:true});
  })();

  /* --- 下タブ & サブメニュー --- */
  const tabBar   = document.querySelector(".bottom-tab");
  const tabItems = document.querySelectorAll(".bottom-tab .tab-item");
  if (!tabBar || !tabItems.length) return;

  // 共用アクションバー
  const actionbar = document.createElement("div");
  actionbar.className = "tab-actionbar";
  actionbar.id = "tab-actionbar";
  actionbar.setAttribute("role", "menu");
  document.body.appendChild(actionbar);

  let openFor = null;
  let justOpenedAt = 0;

  const getBarOffset = () => {
    // 下タブの実高さ + 12px だけ上に
    const r = tabBar.getBoundingClientRect();
    return Math.round(r.height + 12);
  };

  const closeBar = () => {
    if (openFor) {
      openFor.classList.remove("open");
      const caret = openFor.querySelector(":scope > .tab-caret-btn");
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
  };

  const openBarFor = (tabItem) => {
    const submenu = tabItem.querySelector(".sub-menu");
    if (!submenu) return;

    if (openFor && openFor !== tabItem) closeBar();

    actionbar.innerHTML = "";
    const links = submenu.querySelectorAll("a");
    if (!links.length) {
      const none = document.createElement("span");
      none.className = "ab-btn";
      none.textContent = "メニューなし";
      none.setAttribute("role", "menuitem");
      actionbar.appendChild(none);
    } else {
      links.forEach(a => {
        const href   = a.getAttribute("href") || "#";
        const txt    = (a.textContent || "").trim();
        const target = a.getAttribute("target") || "";

        const btn = document.createElement("a");
        btn.className = "ab-btn";
        btn.href = href;
        btn.textContent = txt;
        btn.setAttribute("role", "menuitem");

        btn.addEventListener("click", (e) => {
          if (!href || href.startsWith("#") || href.startsWith("javascript:")) return;
          if (target === "_blank") return;
          e.preventDefault();
          (window.__showLoading__ || ((cb)=>cb()))(() => (window.location.href = href));
        }, {passive:false});

        actionbar.appendChild(btn);
      });
    }

    // 位置決め：SP=下タブ上 / PC=吹き出し風（中央寄せ）
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
      actionbar.style.bottom = getBarOffset() + "px";
    }

    tabItem.classList.add("open");
    const caret = tabItem.querySelector(":scope > .tab-caret-btn");
    if (caret) caret.setAttribute("aria-expanded", "true");

    actionbar.style.display = "flex";
    requestAnimationFrame(() => actionbar.classList.add("show"));

    openFor = tabItem;
    justOpenedAt = Date.now();
  };

  // 既存の飾りケアレットを全削除 → 全タブに確実にケアレット領域を配置
  tabItems.forEach(tab => {
    tab.querySelectorAll(".tab-caret-btn, .tab-caret, .caret, .caret-icon, [data-caret], [data-role='caret']")
      .forEach(n => n.remove());

    const submenu = tab.querySelector(".sub-menu");
    const link    = tab.querySelector(".tab-link");

    // 上段ボタン：通常遷移（ローダー付）
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

    // 下段ケアレット：全タブに必ず追加（無い場合は透明プレースホルダー）
    const caret = document.createElement("button");
    caret.type = "button";
    caret.className = "tab-caret-btn";
    caret.setAttribute("aria-controls", "tab-actionbar");
    caret.setAttribute("aria-label", "サブメニューを開閉");
    caret.textContent = "▾";
    tab.appendChild(caret);

    if (submenu) {
      tab.classList.add("has-sub");
      caret.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (openFor === tab) closeBar(); else openBarFor(tab);
      }, {passive:false});

      // 長押しでクイックアクセス（SP）
      let tmr = null;
      const LONG_MS = 500;
      tab.addEventListener("touchstart", (e) => {
        if (e.target === caret) return;
        tmr = setTimeout(() => { openBarFor(tab); tmr = null; }, LONG_MS);
      }, {passive:true});
      tab.addEventListener("touchend",   () => { if (tmr) { clearTimeout(tmr); tmr = null; } }, {passive:true});
      tab.addEventListener("touchmove",  () => { if (tmr) { clearTimeout(tmr); tmr = null; } }, {passive:true});
    } else {
      // 透明プレースホルダー：高さは確保、見た目/クリック無効
      caret.classList.add("is-placeholder");
      caret.setAttribute("aria-hidden", "true");
    }
  });

  // 外側クリック・Esc・リサイズ・向き変更で閉じる
  document.addEventListener("click", (e) => {
    if (!openFor) return;
    if (Date.now() - justOpenedAt < 160) return;
    const inTab = !!e.target.closest(".bottom-tab .tab-item");
    const inBar = !!e.target.closest(".tab-actionbar");
    if (!inTab && !inBar) closeBar();
  }, {passive:true});
  window.addEventListener("keydown", (e) => { if (e.key === "Escape" && openFor) closeBar(); }, {passive:true});
  window.addEventListener("resize", () => closeBar(), {passive:true});
  window.addEventListener("orientationchange", () => closeBar(), {passive:true});

  // 画面回転やソフトキーボード出現で下タブ高さが変わったら再配置
  const ro = new ResizeObserver(() => {
    if (actionbar.classList.contains("show") && !window.matchMedia("(min-width:768px)").matches) {
      actionbar.style.bottom = getBarOffset() + "px";
    }
  });
  ro.observe(tabBar);
});