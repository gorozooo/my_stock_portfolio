document.addEventListener("DOMContentLoaded", function() {

  /* ===== 下タブ＆サブメニュー操作 ===== */
  const tabs = document.querySelectorAll('.tab-item');

  tabs.forEach(tab => {
    const subMenu = tab.querySelector('.sub-menu');
    const tabLink = tab.querySelector('.tab-link');

    if(subMenu){
      // サブメニュー初期スタイル
      subMenu.style.position = 'fixed';
      subMenu.style.opacity = '0';
      subMenu.style.transform = 'translateY(10px)';
      subMenu.style.transition = 'opacity 0.2s ease, transform 0.2s ease';
      subMenu.style.zIndex = '10000';

      // サブメニューリンククリック時は必ずページ遷移
      subMenu.querySelectorAll('a').forEach(a => {
        a.addEventListener('click', e => {
          e.stopPropagation(); // 上位クリック阻止
          const href = a.getAttribute('href');
          if(href && !href.startsWith('#') && !href.startsWith('javascript:')){
            // 即時移行
            window.location.href = href;
          }
        });
        // タッチでも確実に移行
        a.addEventListener('touchend', e => {
          e.stopPropagation();
          const href = a.getAttribute('href');
          if(href && !href.startsWith('#') && !href.startsWith('javascript:')){
            window.location.href = href;
          }
        });
      });

      // タブクリックでサブメニュー開閉
      tab.addEventListener('click', e => {
        if(e.target.closest('.sub-menu a')) return; // サブメニューリンクは無視
        const isOpen = subMenu.classList.contains('show');
        closeAllSubMenus();
        if(!isOpen) openSubMenu(subMenu, tab);
      });

      // サブメニュークリックはイベント伝播停止
      subMenu.addEventListener('click', e => e.stopPropagation());
    }

    // 下タブリンククリック（サブメニュー非表示時のみページ遷移）
    if(tabLink){
      tabLink.addEventListener('click', e => {
        if(subMenu && subMenu.classList.contains('show')){
          // サブメニューが開いている場合は閉じるだけ
          e.preventDefault();
          closeAllSubMenus();
        } else {
          const href = tabLink.getAttribute('href');
          if(href && !href.startsWith('#') && !href.startsWith('javascript:')){
            e.preventDefault();
            window.location.href = href;
          }
        }
      });
    }

    // タブ長押し対応（スマホ向け）
    let touchStartTime = 0;
    tab.addEventListener('touchstart', e => { touchStartTime = Date.now(); });
    tab.addEventListener('touchend', e => {
      const touchDuration = Date.now() - touchStartTime;
      if(touchDuration < 500 && !e.target.closest('.sub-menu a')) tab.click();
    });
  });

  // 外部クリックでサブメニュー閉じる
  ['click','touchstart'].forEach(ev => {
    document.addEventListener(ev, e => {
      if(!e.target.closest('.tab-item')) closeAllSubMenus();
    });
  });

  function openSubMenu(subMenu, tab){
    const rect = tab.getBoundingClientRect();
    const left = Math.min(rect.left, window.innerWidth - subMenu.offsetWidth - 10);
    subMenu.style.left = left + "px";
    subMenu.style.bottom = (window.innerHeight - rect.top + 10) + "px";
    requestAnimationFrame(()=>{
      subMenu.classList.add('show');
      subMenu.style.opacity = '1';
      subMenu.style.transform = 'translateY(0)';
    });
  }

  function closeAllSubMenus(){
    document.querySelectorAll('.sub-menu').forEach(sm=>{
      sm.classList.remove('show');
      sm.style.opacity='0';
      sm.style.transform='translateY(10px)';
    });
  }


  /* ===== 共通確認モーダル ===== */
  const modal = document.getElementById("confirmModal");
  if(modal){
    const btnCancel = modal.querySelector(".btn-cancel");
    const btnOk = modal.querySelector(".btn-ok");
    let okCallback = null;

    window.openConfirmModal = (message, callback)=>{
      modal.querySelector("p").textContent = message;
      okCallback = callback;
      modal.style.display = "block";
    };
    btnCancel.addEventListener("click",()=>{modal.style.display="none"; okCallback=null;});
    btnOk.addEventListener("click",()=>{modal.style.display="none"; if(typeof okCallback==="function") okCallback(); okCallback=null;});
    modal.addEventListener("click",e=>{if(e.target===modal){modal.style.display="none"; okCallback=null;}});
  }

  /* ===== ローディング画面 ===== */
  const loadingOverlay = document.createElement('div');
  Object.assign(loadingOverlay.style,{
    position:'fixed',top:'0',left:'0',width:'100%',height:'100%',
    background:'rgba(0,0,20,0.85)',display:'flex',flexDirection:'column',
    justifyContent:'center',alignItems:'center',zIndex:'9999',opacity:'0',
    transition:'opacity 0.2s ease'
  });
  document.body.appendChild(loadingOverlay);

  function showLoading(cb){loadingOverlay.style.display='flex'; requestAnimationFrame(()=>loadingOverlay.style.opacity='1'); if(cb) setTimeout(cb,50);}
  function hideLoading(){loadingOverlay.style.opacity='0'; setTimeout(()=>{loadingOverlay.style.display='none';},300);}
  window.addEventListener("load", hideLoading);
  window.addEventListener("pageshow", hideLoading);

  /* ===== 現在ページ名自動取得 ===== */
  const currentURL = location.pathname;
  const currentPageNameEl = document.getElementById("current-page-name");
  if(currentPageNameEl){
    const tabLinks=document.querySelectorAll(".tab-item .tab-link");
    let found=false;
    tabLinks.forEach(tabLink=>{
      const href=tabLink.getAttribute("href");
      const nameSpan=tabLink.querySelector("span");
      if(href && nameSpan && currentURL.startsWith(href)){
        currentPageNameEl.textContent=nameSpan.textContent;
        found=true;
      }
    });
    if(!found) currentPageNameEl.textContent=currentURL.replace(/^\/|\/$/g,"")||"ホーム";
  }

  /* ===== ローディングバーアニメーション ===== */
  const style=document.createElement('style');
  style.innerHTML=`
    @keyframes bounceText{
      0%,20%,50%,80%,100%{transform:translateY(0);}
      40%{transform:translateY(-16px);}
      60%{transform:translateY(-8px);}
    }
  `;
  document.head.appendChild(style);

});