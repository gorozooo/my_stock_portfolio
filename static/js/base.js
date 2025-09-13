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

  /* =========================
     下タブ & サブメニュー
     - 1タップで必ず開く（遷移はしない）
     - 外側タップ閉じは250msガード
  ========================= */
  const tabBar = document.querySelector('.bottom-tab');
  const tabItems = document.querySelectorAll('.bottom-tab .tab-item');
  if (!tabBar || !tabItems.length) return;

  // サブメニューの有無で見た目フラグ
  tabItems.forEach(t => { if (t.querySelector('.sub-menu')) t.classList.add('has-sub'); });

  // 共有UI
  const backdrop = document.createElement('div');
  backdrop.className = 'tab-backdrop';
  document.body.appendChild(backdrop);

  const sheet = document.createElement('div');
  sheet.className = 'bottom-sheet';
  sheet.setAttribute('role','dialog');
  sheet.setAttribute('aria-modal','true');
  sheet.innerHTML = `<ul class="sub-menu" role="menu"></ul>`;
  const sheetList = sheet.querySelector('.sub-menu');
  document.body.appendChild(sheet);

  const pop = document.createElement('div');
  pop.className = 'popover-menu';
  pop.setAttribute('role','menu');
  pop.innerHTML = `<ul class="sub-menu"></ul>`;
  const popList = pop.querySelector('.sub-menu');
  document.body.appendChild(pop);

  let openFor = null;
  let lastFocus = null;
  let justOpenedAt = 0; // 直後閉じ防止ガード

  const DRAG_TOL = 8; // ドラッグ誤反応防止
  let downX=0, downY=0, dragging=false;

  function isDesktop(){ return window.matchMedia('(min-width: 768px)').matches; }

  function cloneMenuItems(fromMenu, toList){
    toList.innerHTML = '';
    fromMenu.querySelectorAll('a').forEach(a=>{
      const li = document.createElement('li');
      const link = document.createElement('a');
      link.href = a.getAttribute('href') || '#';
      link.textContent = a.textContent || '';
      link.addEventListener('click', ev=>{
        const href = link.getAttribute('href');
        if (href && !href.startsWith('#') && !href.startsWith('javascript:')) {
          ev.preventDefault();
          showLoading(()=> window.location.href = href);
        }
      }, {passive:false});
      li.appendChild(link);
      toList.appendChild(li);
    });
  }

  function closeMenus(){
    document.querySelectorAll('.bottom-tab .tab-item.open').forEach(t=> t.classList.remove('open'));
    backdrop.classList.remove('show');
    sheet.classList.remove('show');
    pop.classList.remove('show');
    sheetList.innerHTML = '';
    popList.innerHTML = '';
    openFor = null;
    if (lastFocus) { try{ lastFocus.focus({preventScroll:true}); }catch{} lastFocus = null; }
    document.removeEventListener('keydown', onKeydown);
  }

  function onKeydown(e){ if (e.key === 'Escape'){ e.preventDefault(); closeMenus(); } }

  function openMenuFor(tabItem){
    const submenu = tabItem.querySelector('.sub-menu');
    if (!submenu) return;

    if (openFor && openFor !== tabItem) closeMenus();

    lastFocus = tabItem.querySelector('.tab-link') || tabItem;
    document.addEventListener('keydown', onKeydown);

    tabItem.classList.add('open');
    backdrop.classList.add('show');
    openFor = tabItem;
    justOpenedAt = Date.now();

    if (isDesktop()){
      cloneMenuItems(submenu, popList);
      const rect = tabItem.getBoundingClientRect();
      const width = Math.max(180, Math.min(260, rect.width*1.4));
      // 画面内に収まるように位置調整
      const left = Math.min(Math.max(8, rect.left + rect.width/2 - width/2), window.innerWidth - width - 8);
      pop.style.left = left + 'px';
      pop.style.top  = (rect.top - 12) + 'px';
      pop.style.width = width + 'px';
      pop.classList.add('show');
      const first = pop.querySelector('a'); if(first) first.focus({preventScroll:true});
    }else{
      cloneMenuItems(submenu, sheetList);
      sheet.classList.add('show');
      const first = sheet.querySelector('a'); if(first) first.focus({preventScroll:true});
    }
  }

  // 各タブの挙動（1タップで開く）
  tabItems.forEach(tab=>{
    const tabLink = tab.querySelector('.tab-link');
    const submenu = tab.querySelector('.sub-menu');

    // ポインタダウンでドラッグ判定開始
    tab.addEventListener('pointerdown', e=>{
      downX = e.clientX; downY = e.clientY; dragging = false;
    }, {passive:true});
    tab.addEventListener('pointermove', e=>{
      if (dragging) return;
      if (Math.hypot(e.clientX - downX, e.clientY - downY) > DRAG_TOL) dragging = true;
    }, {passive:true});

    tab.addEventListener('pointerup', e=>{
      if (dragging) return; // スクロール/ドラッグは無視
      e.preventDefault();
      e.stopPropagation();

      if (submenu){
        // サブメニューがあるタブはナビせず、必ず開く
        if (tab.classList.contains('open')){
          closeMenus();
        }else{
          openMenuFor(tab);
        }
      }else if (tabLink){
        const href = tabLink.getAttribute('href');
        if (href && !href.startsWith('#') && !href.startsWith('javascript:')){
          showLoading(()=> window.location.href = href);
        }
      }
    }, {passive:false});
  });

  // 外側クリックで閉じる（開いた直後250msは無効化）
  function guardedClose(e){
    if (Date.now() - justOpenedAt < 250) return;
    if (!e.target.closest('.bottom-tab') &&
        !e.target.closest('.bottom-sheet') &&
        !e.target.closest('.popover-menu')) {
      closeMenus();
    }
  }
  backdrop.addEventListener('click', guardedClose, {passive:true});
  document.addEventListener('click', guardedClose, {passive:true});

  window.addEventListener('resize', closeMenus);

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