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
  loading.innerHTML = `<div class="loading-text">Now Loading…</div><div class="loading-bar"></div>`;
  document.body.appendChild(loading);

  function showLoading(cb){ loading.style.display="flex"; requestAnimationFrame(()=>{ loading.style.opacity="1"; if (cb) setTimeout(cb,40); }); }
  function hideLoading(){ loading.style.opacity="0"; setTimeout(()=>{ loading.style.display="none"; },200); }
  showLoading(); window.addEventListener("load", hideLoading);
  window.addEventListener("beforeunload", ()=> showLoading());
  window.addEventListener("pageshow", e=>{ if(e.persisted) hideLoading(); });

/* =====================================
   下タブ & サブメニュー（ボタンバー版）
   - サブメニューの <a> を “ピル型ボタン” にして横並び展開
   - オーバーレイ/ボトムシート無し、外側クリックで閉じる
===================================== */
(function(){
  const tabBar = document.querySelector('.bottom-tab');
  const tabItems = document.querySelectorAll('.bottom-tab .tab-item');
  if (!tabBar || !tabItems.length) return;

  // 既存の（ポップオーバー/ボトムシート）UIがあればクリア
  document.querySelectorAll('.tab-backdrop, .bottom-sheet, .popover-menu')
    .forEach(n => n.remove());

  // アクションバーDOM（1つを共用）
  const actionbar = document.createElement('div');
  actionbar.className = 'tab-actionbar';
  document.body.appendChild(actionbar);

  // サブメニュー保有フラグ付与
  tabItems.forEach(t => { if (t.querySelector('.sub-menu')) t.classList.add('has-sub'); });

  let openFor = null;
  let justOpenedAt = 0;

  // 指のドラッグ誤反応を抑制
  const DRAG_TOL = 8;
  let downX=0, downY=0, dragging=false;

  function closeBar(){
    actionbar.classList.remove('show');
    actionbar.style.display = 'none';
    actionbar.innerHTML = '';
    if (openFor) openFor.classList.remove('open');
    openFor = null;
  }

  function openBarFor(tabItem){
    const submenu = tabItem.querySelector('.sub-menu');
    if (!submenu) return;

    // すでに別タブが開いていれば閉じる
    if (openFor && openFor !== tabItem) closeBar();

    // ボタン再生成
    actionbar.innerHTML = '';
    submenu.querySelectorAll('a').forEach(a=>{
      const btn = document.createElement('a');
      btn.className = 'ab-btn';
      btn.href = a.getAttribute('href') || '#';
      btn.textContent = a.textContent || '';
      btn.addEventListener('click', e=>{
        const href = btn.getAttribute('href');
        if (!href || href.startsWith('#') || href.startsWith('javascript:')) return;
        e.preventDefault();
        if (typeof showLoading === 'function') {
          showLoading(()=> window.location.href = href);
        } else {
          window.location.href = href;
        }
      }, {passive:false});
      actionbar.appendChild(btn);
    });

    // 位置：スマホは左右余白、デスクトップはタブの真上に幅合わせ
    const rect = tabItem.getBoundingClientRect();
    if (window.matchMedia('(min-width: 768px)').matches) {
      const width = Math.min(560, Math.max(240, rect.width * 1.6));
      const left = Math.min(Math.max(8, rect.left + rect.width/2 - width/2), window.innerWidth - width - 8);
      actionbar.style.left = left + 'px';
      actionbar.style.right = 'auto';
      actionbar.style.bottom = (window.innerHeight - rect.top + 10) + 'px';
      actionbar.style.width = width + 'px';
    } else {
      actionbar.style.left = '8px';
      actionbar.style.right = '8px';
      actionbar.style.width = 'auto';
      actionbar.style.bottom = '80px'; // 下タブの上
    }

    tabItem.classList.add('open');
    actionbar.style.display = 'flex';
    requestAnimationFrame(()=> actionbar.classList.add('show'));
    openFor = tabItem;
    justOpenedAt = Date.now();
  }

  // タブのタップ挙動：サブメニューあり→ボタンバー開閉／なし→遷移
  tabItems.forEach(tab=>{
    const tabLink = tab.querySelector('.tab-link');
    const submenu = tab.querySelector('.sub-menu');

    tab.addEventListener('pointerdown', e=>{
      downX = e.clientX; downY = e.clientY; dragging = false;
    }, {passive:true});

    tab.addEventListener('pointermove', e=>{
      if (dragging) return;
      if (Math.hypot(e.clientX - downX, e.clientY - downY) > DRAG_TOL) dragging = true;
    }, {passive:true});

    tab.addEventListener('pointerup', e=>{
      if (dragging) return;
      e.preventDefault();
      e.stopPropagation();

      if (submenu){
        if (openFor === tab) {
          closeBar();
        } else {
          openBarFor(tab);
        }
      } else if (tabLink){
        const href = tabLink.getAttribute('href');
        if (href && !href.startsWith('#') && !href.startsWith('javascript:')){
          if (typeof showLoading === 'function') {
            showLoading(()=> window.location.href = href);
          } else {
            window.location.href = href;
          }
        }
      }
    }, {passive:false});
  });

  // 外側クリックで閉じる（開直後 200ms は無視）
  document.addEventListener('click', e=>{
    if (!openFor) return;
    if (Date.now() - justOpenedAt < 200) return;
    const inTab = !!e.target.closest('.bottom-tab .tab-item');
    const inBar = !!e.target.closest('.tab-actionbar');
    if (!inTab && !inBar) closeBar();
  }, {passive:true});

  window.addEventListener('resize', closeBar);
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