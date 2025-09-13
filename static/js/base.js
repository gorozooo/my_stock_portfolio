// 下タブ：サブメニューがあるタブだけ「押せるケアレット（↓）」を
// タブ“の下”に独立ボタンとして自動挿入。↓だけで開閉、タブ本体は通常遷移。
// （装飾用の既存ケアレットは強制的に無効化／除去）

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

  /* --- 下タブ & サブメニュー（ボタンバー） --- */
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

  function closeBar() {
    if (openFor) {
      openFor.classList.remove("open");
      const caretBtn = openFor.querySelector(":scope > .tab-caret-btn");
      if (caretBtn) caretBtn.setAttribute("aria-expanded", "false");
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
          // hash や javascript: はそのまま
          if (!href || href.startsWith("#") || href.startsWith("javascript:")) return;
          // 新規タブはローダー無し
          if (target === "_blank") return;

          e.preventDefault();
          (window.__showLoading__ || ((cb)=>cb()))(() => (window.location.href = href));
        }, {passive:false});

        actionbar.appendChild(btn);
      });
    }

    // 位置：SPは左右8px・タブ上/ PCは吹き出し的に中央寄せ
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
    const caretBtn = tabItem.querySelector(":scope > .tab-caret-btn");
    if (caretBtn) caretBtn.setAttribute("aria-expanded", "true");

    actionbar.style.display = "flex";
    requestAnimationFrame(() => actionbar.classList.add("show"));

    openFor = tabItem;
    justOpenedAt = Date.now();
  }

  // 1) 装飾ケアレットの無効化 2) 「↓」ボタンをタブの“直下”に設置 3) 長押しでクイック開
  tabItems.forEach(tab => {
    const submenu = tab.querySelector(".sub-menu");
    const link    = tab.querySelector(".tab-link");

    // （重要）装飾ケアレット類は**すべて**消す
    tab.querySelectorAll(".tab-caret-btn, .tab-caret, .caret, .caret-icon, [data-caret], [data-role='caret']").forEach(n => n.remove());
    // タブに .has-sub を付けると CSS 疑似(::after)でケアレットが出るテーマがあるので **付けない**
    tab.classList.remove("has-sub");

    // サブメニュー無し → 通常遷移だけフック
    if (!submenu) {
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

    // ---------- ここからサブメニューあり ----------
    // ケアレットボタンをタブ“直下（兄弟要素）”に作る（= ボタン形式の下段）
    const caret = document.createElement("button");
    caret.type = "button";
    caret.className = "tab-caret-btn";
    caret.setAttribute("aria-expanded", "false");
    caret.setAttribute("aria-controls", "tab-actionbar");
    caret.setAttribute("aria-label", "サブメニューを開閉");
    caret.textContent = "▾";

    // 既存テーマが .tab-link の中しかスタイルしてない場合でも、
    // 下段に独立表示させたいので .tab-item の**直下**に追加
    tab.appendChild(caret);

    // ↓ でのみ開閉
    caret.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (openFor === tab) closeBar(); else openBarFor(tab);
    }, {passive:false});

    // タブ本体は通常遷移のまま
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

    // 長押しクイックアクセス（スマホ想定）
    let touchTimer = null;
    const LONG_PRESS_MS = 500;

    tab.addEventListener("touchstart", (e) => {
      if (e.target === caret) return; // ケアレット自体は通常処理
      touchTimer = setTimeout(() => {
        openBarFor(tab);
        touchTimer = null;
      }, LONG_PRESS_MS);
    }, {passive:true});

    tab.addEventListener("touchend", () => {
      if (touchTimer) { clearTimeout(touchTimer); touchTimer = null; }
    }, {passive:true});

    tab.addEventListener("touchmove", () => {
      if (touchTimer) { clearTimeout(touchTimer); touchTimer = null; }
    }, {passive:true});
  });

  // 外側クリック・Esc・リサイズで閉じる（開直後は誤閉じ防止）
  document.addEventListener("click", (e) => {
    if (!openFor) return;
    if (Date.now() - justOpenedAt < 160) return;
    const inTab = !!e.target.closest(".bottom-tab .tab-item");
    const inBar = !!e.target.closest(".tab-actionbar");
    if (!inTab && !inBar) closeBar();
  }, {passive:true});
  window.addEventListener("keydown", (e) => { if (e.key === "Escape" && openFor) closeBar(); }, {passive:true});
  window.addEventListener("resize", () => closeBar(), {passive:true});

  /* --- 現在ページ名（任意） --- */
  const cur = document.getElementById("current-page-name");
  if (cur) {
    const path = location.pathname;
    const tabLinks = document.querySelectorAll(".tab-item .tab-link");
    let found = false;
    tabLinks.forEach(tl => {
      const href = tl.getAttribute("href");
      const nameSpan = tl.querySelector("span");
      if (href && nameSpan && path.startsWith(href)) {
        cur.textContent = nameSpan.textContent;
        found = true;
      }
    });
    if (!found) cur.textContent = path.replace(/^\/|\/$/g, "") || "ホーム";
  }
});