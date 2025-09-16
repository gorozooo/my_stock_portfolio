// bottom_tab.js – Haptics風アニメ + ボトムシート + アイコン/色
document.addEventListener("DOMContentLoaded", () => {
  const submenu = document.getElementById("submenu");
  const tabs = document.querySelectorAll(".tab-btn");
  const root = document.getElementById("bottomTabRoot") || document.body;
  const LONG_PRESS_MS = 550;

  // マスク要素（なければ生成）
  let mask = document.querySelector(".btm-mask");
  if (!mask){
    mask = document.createElement("div");
    mask.className = "btm-mask";
    root.appendChild(mask);
  }

  // iOS コンテキストメニュー抑止
  document.querySelectorAll(".bottom-tab, .submenu").forEach(el => {
    el.addEventListener("contextmenu", e => e.preventDefault());
  });

  // ページ別メニュー（アイコンとtoneを追加）
  const MENUS = {
    home: [
      { section: "クイック" },
      { label: "保有を追加",        action: "add_holding",   icon: "➕", tone: "add" },
      { label: "実現損益を記録",    action: "add_realized",  icon: "✍️", tone: "action" },
      { label: "設定を開く",        href: "/settings/trade", icon: "⚙️", tone: "info" },
    ],
    holdings: [
      { section: "保有" },
      { label: "＋ 追加",            action: "add_holding",   icon: "📥", tone: "add" },
      { label: "CSVエクスポート",    action: "export_csv",    icon: "🧾", tone: "info" },
      { label: "並び替え/フィルタ",  action: "open_filter",   icon: "🧮", tone: "action" },
      { section: "選択中" },
      { label: "売却（クローズ）",   action: "close_position",icon: "💱", tone: "action" },
      { label: "削除",               action: "delete_holding",icon: "🗑️", tone: "danger" },
    ],
    trend: [
      { section: "トレンド" },
      { label: "監視に追加",          action: "watch_symbol",  icon: "👁️", tone: "add" },
      { label: "エントリー/ストップ計算", action: "calc_entry_stop", icon: "🎯", tone: "info" },
      { label: "共有リンクをコピー",  action: "share_link",    icon: "🔗", tone: "info" },
      { label: "チャート設定",        action: "chart_settings",icon: "🛠️", tone: "action" },
    ],
  };

  // ---- メニュー描画：ボトムシート（全幅） ----
  function renderMenu(type){
    const items = MENUS[type] || [];
    submenu.innerHTML = "";

    const grab = document.createElement("div");
    grab.className = "grabber";
    submenu.appendChild(grab);

    items.forEach(it=>{
      if (it.section){
        const sec = document.createElement("div");
        sec.className = "section"; sec.textContent = it.section;
        submenu.appendChild(sec); return;
      }
      const b = document.createElement("button");
      b.className = `submenu-item tone-${it.tone || "info"}`;
      b.innerHTML = `<span class="ico">${it.icon || "•"}</span><span>${it.label}</span>`;
      b.addEventListener("click",(e)=>{
        e.stopPropagation(); hideMenu();
        if (it.href){ window.location.href = it.href; return; }
        window.dispatchEvent(new CustomEvent("bottomtab:action",{detail:{menu:type,action:it.action}}));
      });
      submenu.appendChild(b);
    });
  }

  function showMenu(type, btn){
    renderMenu(type);
    mask.classList.add("show");
    submenu.classList.add("show");
    submenu.setAttribute("aria-hidden","false");
    // haptic視覚：ボタンぷるぷる
    btn.classList.add("shake");
    setTimeout(()=>btn.classList.remove("shake"), 360);
    // 小さくバイブ
    if (navigator.vibrate) navigator.vibrate(10);
    // スクロール抑止
    document.documentElement.style.overflow = "hidden";
    document.body.style.overflow = "hidden";
  }

  function hideMenu(){
    mask.classList.remove("show");
    submenu.classList.remove("show");
    submenu.setAttribute("aria-hidden","true");
    document.documentElement.style.overflow = "";
    document.body.style.overflow = "";
  }

  // マスクタップ・下スワイプで閉じる
  mask.addEventListener("click", hideMenu);
  let startY = null;
  submenu.addEventListener("touchstart",(e)=>{ startY = e.touches[0].clientY; }, {passive:true});
  submenu.addEventListener("touchmove",(e)=>{
    if (startY==null) return;
    const dy = e.touches[0].clientY - startY;
    if (dy>40) hideMenu();
  }, {passive:true});
  submenu.addEventListener("touchend",()=>{ startY=null; }, {passive:true});

  // ---- 長押し（＋右クリック）＆短押し遷移。iOSロングタップ抑止 ----
  tabs.forEach(btn=>{
    const link = btn.dataset.link;
    const type = btn.dataset.menu;
    let timer = null, longPressed = false, moved = false;

    // 通常クリック
    btn.addEventListener("click",(e)=>{
      if (longPressed){ e.preventDefault(); longPressed = false; return; }
      if (!submenu.classList.contains("show") && link) window.location.href = link;
    });

    // iOSのコピー/調べる抑止のため preventDefault（passive:false）
    btn.addEventListener("touchstart",(e)=>{
      e.preventDefault();
      longPressed = false; moved = false;
      clearTimeout(timer);
      timer = setTimeout(()=>{ longPressed = true; showMenu(type, btn); }, 550);
    }, {passive:false});

    btn.addEventListener("touchmove",()=>{ moved = true; clearTimeout(timer); }, {passive:true});
    btn.addEventListener("touchcancel",()=> clearTimeout(timer), {passive:true});
    btn.addEventListener("touchend",()=>{
      clearTimeout(timer);
      if (!longPressed && !moved && link) window.location.href = link;
    }, {passive:true});

    // デスクトップ右クリック
    btn.addEventListener("contextmenu",(e)=>{ e.preventDefault(); showMenu(type, btn); });
  });

  // 背景クリック/Escで閉じる
  document.addEventListener("click",(e)=>{
    if (!submenu.contains(e.target) && !e.target.classList.contains("tab-btn")) hideMenu();
  });
  document.addEventListener("keydown",(e)=>{ if(e.key==="Escape") hideMenu(); });
});