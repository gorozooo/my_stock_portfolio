// 下タブ：サブメニューがあるタブでもタブ本体は“常に遷移”。
// サブメニューの開閉はケアレット（▾）と長押しのみ。

document.addEventListener("DOMContentLoaded", function () {
  /* ========== 軽量ローディング ========== */
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
    function hideLoading(){
      loading.style.opacity="0";
      setTimeout(()=>{ loading.style.display="none"; },200);
    }
    window.__showLoading__ = showLoading;

    // ページ入出時の表示制御
    window.addEventListener("load", hideLoading, {passive:true});
    window.addEventListener("beforeunload", ()=> showLoading(), {passive:true});
    window.addEventListener("pageshow", e=>{ if(e.persisted) hideLoading(); }, {passive:true});
  })();

  /* ========== 下タブ & サブメニュー ========== */
  const tabItems = document.querySelectorAll(".bottom-tab .tab-item");
  if (!tabItems.length) return;

  // すべて閉じる
  function closeAllSubMenus() {
    document.querySelectorAll(".bottom-tab .sub-menu.show").forEach(sm=>{
      sm.classList.remove("show");
      sm.style.opacity = "0";
      sm.style.transform = "translateY(10px)";
    });
    document.querySelectorAll(".tab-caret-btn[aria-expanded='true']").forEach(b=>{
      b.setAttribute("aria-expanded", "false");
    });
  }

  // 位置合わせして開く
  function openSubMenuFor(tabItem) {
    const subMenu = tabItem.querySelector(".sub-menu");
    if (!subMenu) return;

    // 一旦 show で幅測り
    subMenu.classList.add("show");
    subMenu.style.position = "fixed";
    subMenu.style.visibility = "hidden";
    const rect = tabItem.getBoundingClientRect();
    const w = subMenu.getBoundingClientRect().width || 160;

    const left = Math.min(Math.max(8, rect.left + rect.width/2 - w/2), window.innerWidth - w - 8);
    subMenu.style.left = left + "px";
    subMenu.style.bottom = (window.innerHeight - rect.top + 10) + "px";

    requestAnimationFrame(()=>{
      subMenu.style.visibility = "visible";
      subMenu.style.opacity = "1";
      subMenu.style.transform = "translateY(0)";
    });

    const caret = tabItem.querySelector(".tab-caret-btn");
    if (caret) caret.setAttribute("aria-expanded", "true");
  }

  // 外側クリックで閉じる
  ["click","touchstart"].forEach(ev=>{
    document.addEventListener(ev, (e)=>{
      if (!e.target.closest(".bottom-tab .tab-item") && !e.target.closest(".bottom-tab .sub-menu")) {
        closeAllSubMenus();
      }
    }, {passive:true});
  });

  tabItems.forEach(tabItem=>{
    const link = tabItem.querySelector(".tab-link");
    const subMenu = tabItem.querySelector(".sub-menu");

    // サブメニュー初期状態
    if (subMenu) {
      subMenu.classList.remove("show");
      subMenu.style.position = "fixed";
      subMenu.style.opacity = "0";
      subMenu.style.transform = "translateY(10px)";
      subMenu.style.transition = "opacity .2s ease, transform .2s ease";
      subMenu.style.zIndex = "10000";

      // サブメニュー内リンク：遷移前ローダー
      subMenu.querySelectorAll("a").forEach(a=>{
        a.addEventListener("click", (e)=>{
          const href = a.getAttribute("href");
          if (!href || href.startsWith("#") || href.startsWith("javascript:")) return;
          e.preventDefault(); e.stopPropagation();
          (window.__showLoading__ || ((cb)=>cb()))(()=> window.location.href = href);
        }, {passive:false});
      });

      // --- ケアレット（▾）を挿入：これでのみ開閉 ---
      // 既存の飾りケアレットは排除
      tabItem.querySelectorAll(".tab-caret-btn, .tab-caret, .caret, .caret-icon, [data-caret]").forEach(n=>n.remove());
      const caret = document.createElement("button");
      caret.type = "button";
      caret.className = "tab-caret-btn";
      caret.setAttribute("aria-expanded", "false");
      caret.setAttribute("aria-label", "サブメニューを開閉");
      caret.textContent = "▾";
      // 下タブの『下側』に出すイメージ：.tab-item の直下に置く
      tabItem.appendChild(caret);

      caret.addEventListener("click", (e)=>{
        e.preventDefault();
        e.stopPropagation();
        const isOpen = subMenu.classList.contains("show");
        closeAllSubMenus();
        if (!isOpen) openSubMenuFor(tabItem);
      }, {passive:false});
    } else {
      // サブメニュー無し：余計なケアレットは削除
      tabItem.querySelectorAll(".tab-caret-btn, .tab-caret, .caret, .caret-icon, [data-caret]").forEach(n=>n.remove());
    }

    // タブ本体クリックは常に遷移（サブメニュー有りでも）
    if (link) {
      link.addEventListener("click", (e)=>{
        const href = link.getAttribute("href");
        if (!href || href.startsWith("#") || href.startsWith("javascript:")) return;
        e.preventDefault();
        closeAllSubMenus();
        (window.__showLoading__ || ((cb)=>cb()))(()=> window.location.href = href);
      }, {passive:false});
    }

    // 長押し（500ms〜）でクイックオープン
    if (subMenu && link) {
      let t0 = 0, pressed = false;
      link.addEventListener("touchstart", ()=>{ t0 = Date.now(); pressed=false; }, {passive:true});
      link.addEventListener("touchend", (e)=>{
        const dur = Date.now() - t0;
        if (dur >= 500) {
          e.preventDefault(); e.stopPropagation();
          pressed = true;
          const isOpen = subMenu.classList.contains("show");
          closeAllSubMenus();
          if (!isOpen) openSubMenuFor(tabItem);
        }
      }, {passive:false});
    }
  });
});