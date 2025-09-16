// bottom_tab.js – clamp付きポジショニング
document.addEventListener("DOMContentLoaded", () => {
  const submenu = document.getElementById("submenu");
  const tabs = document.querySelectorAll(".tab-btn");
  const LONG_PRESS_MS = 550;

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

  function renderMenu(type){
    const items = MENUS[type] || [];
    submenu.innerHTML = "";
    items.forEach(it=>{
      if (it.section){
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
  }

  // 押したボタンの真上に出しつつ、左右は画面内にクランプ
  function positionMenu(anchorBtn){
    const pad = 12;                          // 画面端からの余白
    const vw = window.innerWidth || document.documentElement.clientWidth;
    const r = anchorBtn.getBoundingClientRect();
    const center = r.left + r.width / 2;

    // 一旦表示して幅を測る（不可視で）
    submenu.style.visibility = "hidden";
    submenu.classList.add("show");
    const w = submenu.offsetWidth;
    // 幅が画面より大きすぎる場合は縮める
    if (w > vw - pad*2) submenu.style.maxWidth = (vw - pad*2) + "px";

    const clamped = Math.min(Math.max(center, pad + submenu.offsetWidth/2),
                             vw - pad - submenu.offsetWidth/2);

    submenu.style.left = clamped + "px";
    submenu.style.transform = "translateX(-50%)";
    submenu.style.visibility = "visible";
  }

  function showMenu(type, btn){
    renderMenu(type);
    positionMenu(btn);
    if (navigator.vibrate) navigator.vibrate(10);
  }
  function hideMenu(){
    submenu.classList.remove("show");
    submenu.setAttribute("aria-hidden","true");
  }

  // 長押し（+右クリック）＆クリック遷移の両立
  tabs.forEach(btn=>{
    const link = btn.dataset.link;
    const type = btn.dataset.menu;
    let timer = null, longPressed = false;

    btn.addEventListener("click",(e)=>{
      if (longPressed){ e.preventDefault(); longPressed = false; return; }
      if (!submenu.classList.contains("show") && link) window.location.href = link;
    });

    btn.addEventListener("touchstart",()=>{
      longPressed = false; clearTimeout(timer);
      timer = setTimeout(()=>{ longPressed = true; showMenu(type, btn); }, LONG_PRESS_MS);
    }, {passive:true});
    btn.addEventListener("touchend",()=> clearTimeout(timer), {passive:true});
    btn.addEventListener("touchmove",()=> clearTimeout(timer), {passive:true});
    btn.addEventListener("touchcancel",()=> clearTimeout(timer), {passive:true});

    btn.addEventListener("contextmenu",(e)=>{ e.preventDefault(); showMenu(type, btn); });
  });

  document.addEventListener("click",(e)=>{
    if (!submenu.contains(e.target) && !e.target.classList.contains("tab-btn")) hideMenu();
  });
  document.addEventListener("keydown",(e)=>{ if(e.key==="Escape") hideMenu(); });
});