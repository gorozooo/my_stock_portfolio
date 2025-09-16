// bottom_tab.js – clamp付きポジショニング + iOS長押し抑止版
document.addEventListener("DOMContentLoaded", () => {
  const submenu = document.getElementById("submenu");
  const tabs = document.querySelectorAll(".tab-btn");
  const LONG_PRESS_MS = 550;

  // 端末のコンテキストメニューを下タブ/サブメニュー領域で無効化
  document.querySelectorAll(".bottom-tab, .submenu").forEach(el => {
    el.addEventListener("contextmenu", e => e.preventDefault());
  });

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
    const pad = 12;
    const vw = window.innerWidth || document.documentElement.clientWidth;
    const r = anchorBtn.getBoundingClientRect();
    const center = r.left + r.width / 2;

    // 一旦表示して幅を測る（不可視で）
    submenu.style.visibility = "hidden";
    submenu.classList.add("show");
    const w = submenu.offsetWidth;
    if (w > vw - pad*2) submenu.style.maxWidth = (vw - pad*2) + "px";

    const clamped = Math.min(
      Math.max(center, pad + submenu.offsetWidth / 2),
      vw - pad - submenu.offsetWidth / 2
    );

    submenu.style.left = clamped + "px";
    submenu.style.transform = "translateX(-50%)";
    submenu.style.visibility = "visible";
  }

  function showMenu(type, btn){
    renderMenu(type);
    positionMenu(btn);
    submenu.setAttribute("aria-hidden","false");
    if (navigator.vibrate) navigator.vibrate(10);
  }
  function hideMenu(){
    submenu.classList.remove("show");
    submenu.setAttribute("aria-hidden","true");
  }

  // 長押し（+右クリック）＆クリック遷移の両立 + iOSロングタップ抑止
  tabs.forEach(btn=>{
    const link = btn.dataset.link;
    const type = btn.dataset.menu;
    let timer = null, longPressed = false, moved = false;

    // 通常クリック
    btn.addEventListener("click",(e)=>{
      if (longPressed){ e.preventDefault(); longPressed = false; return; }
      if (!submenu.classList.contains("show") && link) window.location.href = link;
    });

    // iOSの「コピー/調べる」を出さないために preventDefault を使用（passive:false）
    btn.addEventListener("touchstart",(e)=>{
      e.preventDefault();                // ← 既定のロングタップ動作を抑止
      longPressed = false; moved = false;
      clearTimeout(timer);
      timer = setTimeout(()=>{
        longPressed = true;
        showMenu(type, btn);
      }, LONG_PRESS_MS);
    }, {passive:false});

    btn.addEventListener("touchmove",()=>{ moved = true; clearTimeout(timer); }, {passive:true});
    btn.addEventListener("touchcancel",()=> clearTimeout(timer), {passive:true});

    btn.addEventListener("touchend",()=>{
      clearTimeout(timer);
      // 長押しでなければ自前で遷移（ブラウザ既定クリックは使わない）
      if (!longPressed && !moved && link) {
        window.location.href = link;
      }
    }, {passive:true});

    // PC右クリック
    btn.addEventListener("contextmenu",(e)=>{ e.preventDefault(); showMenu(type, btn); });
  });

  // 背景タップ/Escで閉じる
  document.addEventListener("click",(e)=>{
    if (!submenu.contains(e.target) && !e.target.classList.contains("tab-btn")) hideMenu();
  });
  document.addEventListener("keydown",(e)=>{ if(e.key==="Escape") hideMenu(); });
});