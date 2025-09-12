// base-bottomtab.js
document.addEventListener("DOMContentLoaded", function () {
  /* =========================
     軽量ローディング（必要最低限）
  ========================= */
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
  loading.innerHTML = `<div class="loading-text" aria-live="polite">Now Loading…</div><div class="loading-bar" aria-hidden="true"></div>`;
  document.body.appendChild(loading);

  function showLoading(cb){
    loading.style.display = "flex";
    requestAnimationFrame(()=>{
      loading.style.opacity = "1";
      if (typeof cb === "function") setTimeout(cb, 40);
    });
  }
  function hideLoading(){
    loading.style.opacity = "0";
    setTimeout(()=>{ loading.style.display = "none"; }, 200);
  }
  window.__showLoading__ = showLoading; // ほかのスクリプトからも呼べるように

  // 初回/遷移フック
  showLoading();
  window.addEventListener("load", hideLoading, { passive: true });
  window.addEventListener("beforeunload", ()=> showLoading(), { passive: true });
  window.addEventListener("pageshow", e=>{ if(e.persisted) hideLoading(); }, { passive: true });

  /* =====================================
     下タブ & サブメニュー（ボタンバー版）
     - “↓（ケアレト）”を押した時だけ開閉
     - サブメニューの <a> をピル型ボタンに変換して並べる
     - オーバーレイ無し、外側クリック/ESCで閉じる
  ===================================== */
  (function(){
    const tabBar   = document.querySelector('.bottom-tab');
    const tabItems = document.querySelectorAll('.bottom-tab .tab-item');
    if (!tabBar || !tabItems.length) return;

    // 既存の（ポップオーバー/ボトムシート）UIがあればクリア
    document.querySelectorAll('.tab-backdrop, .bottom-sheet, .popover-menu').forEach(n => n.remove());

    // アクションバーDOM（共用・1つ）
    const actionbar = document.createElement('div');
    actionbar.className = 'tab-actionbar';
    actionbar.setAttribute('role', 'group');
    actionbar.setAttribute('aria-label', 'クイックアクション');
    document.body.appendChild(actionbar);

    // has-sub 付与 & ケアレトボタン注入
    tabItems.forEach(t => {
      const hasSub = !!t.querySelector('.sub-menu');
      if (hasSub) {
        t.classList.add('has-sub');
        if (!t.querySelector('.tab-caret')) {
          const caretBtn = document.createElement('button');
          caretBtn.type = 'button';
          caretBtn.className = 'tab-caret';
          caretBtn.setAttribute('aria-label', 'メニューを開閉');
          caretBtn.setAttribute('aria-expanded', 'false');
          caretBtn.textContent = '▾';
          t.appendChild(caretBtn);
        }
      }
    });

    let openFor = null;          // 開いているタブ要素
    let justOpenedAt = 0;        // 直後の外側クリック誤判定防止
    const OPEN_IGNORE_MS = 200;

    // 位置決め & 表示
    function openBarFor(tabItem){
      const submenu = tabItem.querySelector('.sub-menu');
      if (!submenu) return;

      // 別タブが開いている場合は閉じる
      if (openFor && openFor !== tabItem) closeBar();

      // ボタン群再生成
      actionbar.innerHTML = '';
      submenu.querySelectorAll('a').forEach(a=>{
        const href = a.getAttribute('href') || '#';
        const txt  = (a.textContent || '').trim();
        const btn  = document.createElement('a');
        btn.className = 'ab-btn';
        btn.href = href;
        btn.textContent = txt;
        btn.addEventListener('click', (e)=>{
          if (!href || href.startsWith('#') || href.startsWith('javascript:')) return;
          e.preventDefault();
          (window.__showLoading__ || showLoading)(()=> window.location.href = href);
        }, { passive: false });
        actionbar.appendChild(btn);
      });

      // 位置：モバイル＝左右余白、PC＝該当タブの上に幅合わせ
      const rect = tabItem.getBoundingClientRect();
      const isDesktop = window.matchMedia('(min-width: 768px)').matches;
      if (isDesktop) {
        const width = Math.min(560, Math.max(260, rect.width * 1.7));
        const left  = Math.min(Math.max(8, rect.left + rect.width/2 - width/2), window.innerWidth - width - 8);
        actionbar.style.left   = left + 'px';
        actionbar.style.right  = 'auto';
        actionbar.style.width  = width + 'px';
        actionbar.style.bottom = (window.innerHeight - rect.top + 10) + 'px';
      } else {
        actionbar.style.left   = '8px';
        actionbar.style.right  = '8px';
        actionbar.style.width  = 'auto';
        actionbar.style.bottom = 'calc(86px + env(safe-area-inset-bottom,0) + 8px)'; // 下タブの上
      }

      tabItem.classList.add('open');
      const caret = tabItem.querySelector('.tab-caret');
      if (caret) caret.setAttribute('aria-expanded', 'true');

      actionbar.style.display = 'flex';
      requestAnimationFrame(()=> actionbar.classList.add('show'));

      openFor = tabItem;
      justOpenedAt = Date.now();
    }

    function closeBar(){
      if (openFor) {
        openFor.classList.remove('open');
        const caret = openFor.querySelector('.tab-caret');
        if (caret) caret.setAttribute('aria-expanded', 'false');
      }
      actionbar.classList.remove('show');
      // アニメ後に display: none
      setTimeout(()=>{
        if (!actionbar.classList.contains('show')) {
          actionbar.style.display = 'none';
          actionbar.innerHTML = '';
        }
      }, 180);
      openFor = null;
    }

    // ケアレト（↓）のクリックで開閉。タブ本体は通常遷移
    tabItems.forEach(tab=>{
      const tabLink = tab.querySelector('.tab-link');
      const submenu = tab.querySelector('.sub-menu');
      const caret   = tab.querySelector('.tab-caret');

      if (caret && submenu){
        // マウス/タップ両対応
        ['click','pointerup','keydown'].forEach(ev=>{
          caret.addEventListener(ev, (e)=>{
            // キーボードは Enter/Space のみ反応
            if (e.type === 'keydown' && !(e.key === 'Enter' || e.key === ' ')) return;
            e.preventDefault(); e.stopPropagation();
            if (openFor === tab) closeBar(); else openBarFor(tab);
          }, { passive: false });
        });
      }

      if (tabLink){
        // ↓以外のクリックは普通に遷移
        tabLink.addEventListener('click', (e)=>{
          if (e.target.closest('.tab-caret')) return;
          const href = tabLink.getAttribute('href');
          if (href && !href.startsWith('#') && !href.startsWith('javascript:')){
            e.preventDefault();
            (window.__showLoading__ || showLoading)(()=> window.location.href = href);
          }
        }, { passive: false });
      }
    });

    // 外側クリックで閉じる（開直後は無視）
    document.addEventListener('click', (e)=>{
      if (!openFor) return;
      if (Date.now() - justOpenedAt < OPEN_IGNORE_MS) return;
      const inTab = !!e.target.closest('.bottom-tab .tab-item');
      const inBar = !!e.target.closest('.tab-actionbar');
      if (!inTab && !inBar) closeBar();
    }, { passive: true });

    // ESCで閉じる
    window.addEventListener('keydown', (e)=>{
      if (e.key === 'Escape' && openFor) closeBar();
    }, { passive: true });

    // リサイズで閉じる（配置が変わるため）
    window.addEventListener('resize', closeBar, { passive: true });
  })();

  /* =========================
     現在ページ名（任意）
  ========================= */
  const currentURL = location.pathname;
  const currentPageNameEl = document.getElementById("current-page-name");
  if (currentPageNameEl){
    const tabLinks = document.querySelectorAll(".tab-item .tab-link");
    let found = false;
    tabLinks.forEach(tl=>{
      const href = tl.getAttribute("href");
      const nameSpan = tl.querySelector("span");
      if (href && nameSpan && currentURL.startsWith(href)) {
        currentPageNameEl.textContent = nameSpan.textContent;
        found = true;
      }
    });
    if (!found) currentPageNameEl.textContent = currentURL.replace(/^\/|\/$/g,"") || "ホーム";
  }
});