// bottom_tab.js v3 – iOS長押し安定版
document.addEventListener("DOMContentLoaded", () => {
  const submenu = document.getElementById("submenu");
  const tabs = document.querySelectorAll(".tab-btn");
  const LONG_PRESS_MS = 550;

  // ページ別メニュー
  const MENUS = {
    home: [
      { section: "クイック" },
      { label: "＋ 保有を追加", action: "add_holding" },
      { label: "実現損益を記録", action: "add_realized" },
      { label: "設定を開く", href: "/settings/trade" },
    ],
    holdings: [
      { section: "保有" },
      { label: "＋ 追加", action: "add_holding" },
      { label: "CSVエクスポート", action: "export_csv" },
      { label: "並び替え/フィルタ", action: "open_filter" },
      { section: "選択中" },
      { label: "売却（クローズ）", action: "close_position" },
      { label: "削除", action: "delete_holding", danger: true },
    ],
    trend: [
      { section: "トレンド" },
      { label: "この銘柄を監視に追加", action: "watch_symbol" },
      { label: "エントリー/ストップ計算", action: "calc_entry_stop" },
      { label: "共有リンクをコピー", action: "share_link" },
      { label: "チャート設定", action: "chart_settings" },
    ],
  };

  function renderMenu(type, anchorBtn){
    const items = MENUS[type] || [];
    submenu.innerHTML = "";
    items.forEach(it=>{
      if(it.section){
        const sec = document.createElement("div");
        sec.className = "section"; sec.textContent = it.section;
        submenu.appendChild(sec); return;
      }
      const b = document.createElement("button");
      b.className = "submenu-item" + (it.danger ? " danger" : "");
      b.textContent = it.label;
      b.addEventListener("click",(e)=>{
        e.stopPropagation(); hideMenu();
        if (it.href){ window.location.href = it.href; return; }
        window.dispatchEvent(new CustomEvent("bottomtab:action",{detail:{menu:type,action:it.action}}));
      });
      submenu.appendChild(b);
    });

    // 位置：押したボタンの中央付近
    if(anchorBtn){
      const r = anchorBtn.getBoundingClientRect();
      const left = r.left + r.width/2;
      submenu.style.left = `${left}px`;
      submenu.style.transform = "translateX(-50%)";
    }
  }

  function showMenu(type, anchorBtn){
    renderMenu(type, anchorBtn);
    submenu.classList.add("show");
    submenu.setAttribute("aria-hidden","false");
    if (navigator.vibrate) navigator.vibrate(10);
  }
  function hideMenu(){
    submenu.classList.remove("show");
    submenu.setAttribute("aria-hidden","true");
  }

  // 長押し検出（iOS安定化：suppressClickで遷移抑止）
  tabs.forEach(btn=>{
    const link = btn.dataset.link;
    const type = btn.dataset.menu;
    let timer = null;
    let longPressed = false;

    // 通常クリック
    btn.addEventListener("click",(e)=>{
      if (longPressed) { // 直前に長押し発火したら遷移しない
        e.preventDefault(); longPressed = false; return;
      }
      if (!submenu.classList.contains("show") && link) window.location.href = link;
    });

    // タッチ長押し
    btn.addEventListener("touchstart",(e)=>{
      longPressed = false;
      clearTimeout(timer);
      timer = setTimeout(()=>{
        longPressed = true;
        showMenu(type, btn);
      }, LONG_PRESS_MS);
    }, {passive:true});

    btn.addEventListener("touchend",()=> clearTimeout(timer), {passive:true});
    btn.addEventListener("touchmove",()=> clearTimeout(timer), {passive:true});
    btn.addEventListener("touchcancel",()=> clearTimeout(timer), {passive:true});

    // PC右クリック
    btn.addEventListener("contextmenu",(e)=>{
      e.preventDefault(); showMenu(type, btn);
    });
  });

  // 背景タップ/Escで閉じる
  document.addEventListener("click",(e)=>{
    if (!submenu.contains(e.target) && !e.target.classList.contains("tab-btn")) hideMenu();
  });
  document.addEventListener("keydown",(e)=>{ if(e.key==="Escape") hideMenu(); });
});