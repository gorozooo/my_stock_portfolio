// bottom_tab.js – Haptics + BottomSheet + Icon/Color + Drag Follow
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

  // ページ別メニュー
  const MENUS = {
    home: [
      { section: "クイック" },
      { label: "保有を追加",              action: "add_holding",    icon: "➕", tone: "add" },
      { label: "実現損益を記録",          action: "add_realized",   icon: "✍️", tone: "action" },
      { label: "設定を開く",              href: "/settings/trade",  icon: "⚙️", tone: "info" },
    ],
    holdings: [
      { section: "保有" },
      { label: "＋ 追加",                action: "add_holding",     icon: "📥", tone: "add" },
      { label: "CSVエクスポート",        action: "export_csv",      icon: "🧾", tone: "info" },
      { label: "並び替え/フィルタ",      action: "open_filter",     icon: "🧮", tone: "action" },
      { section: "選択中" },
      { label: "売却（クローズ）",       action: "close_position",  icon: "💱", tone: "action" },
      { label: "削除",                   action: "delete_holding",  icon: "🗑️", tone: "danger" },
    ],
    trend: [
      { section: "トレンド" },
      { label: "監視に追加",             action: "watch_symbol",    icon: "👁️", tone: "add" },
      { label: "エントリー/ストップ計算", action: "calc_entry_stop", icon: "🎯", tone: "info" },
      { label: "共有リンクをコピー",     action: "share_link",      icon: "🔗", tone: "info" },
      { label: "チャート設定",           action: "chart_settings",  icon: "🛠️", tone: "action" },
    ],
  };

  /* ---------- Bottom Sheet Rendering ---------- */
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
    submenu.style.transform = "translateY(0)";
    submenu.setAttribute("aria-hidden","false");
    btn.classList.add("shake");
    setTimeout(()=>btn.classList.remove("shake"), 360);
    if (navigator.vibrate) navigator.vibrate(10);
    document.documentElement.style.overflow = "hidden";
    document.body.style.overflow = "hidden";
  }

  function hideMenu(){
    mask.classList.remove("show");
    submenu.classList.remove("dragging");
    submenu.classList.remove("show");
    submenu.style.transform = ""; // CSSに戻す
    submenu.setAttribute("aria-hidden","true");
    document.documentElement.style.overflow = "";
    document.body.style.overflow = "";
  }

  mask.addEventListener("click", hideMenu);

  /* ---------- Drag Follow (Swipe to Close) ---------- */
  let drag = {
    startY: 0,
    lastY: 0,
    startTime: 0,
    lastTime: 0,
    dy: 0,
    vY: 0,
    active: false
  };

  // ドラッグ開始は「シートが最上部にスクロールされている時のみ」
  function canStartDrag() {
    return submenu.scrollTop <= 0;
  }

  function onDragStart(e){
    const y = (e.touches ? e.touches[0].clientY : e.clientY);
    drag.startY = y;
    drag.lastY = y;
    drag.startTime = drag.lastTime = performance.now();
    drag.dy = 0; drag.vY = 0; drag.active = false;
  }

  function onDragMove(e){
    const y = (e.touches ? e.touches[0].clientY : e.clientY);
    const now = performance.now();
    const dy = Math.max(0, y - drag.startY); // 上方向は0、下のみ
    const dt = Math.max(1, now - drag.lastTime);
    const vy = (y - drag.lastY) / dt;        // px/ms

    // シートが最上部かつ下方向に動いたらドラッグ発火（スクロールを奪う）
    if (!drag.active && dy > 0 && canStartDrag()) {
      drag.active = true;
      submenu.classList.add("dragging");
    }

    if (drag.active){
      e.preventDefault(); // ページスクロール抑止
      drag.dy = dy;
      drag.vY = vy;
      drag.lastY = y;
      drag.lastTime = now;

      // 追従（少しラバー感）
      const follow = dy * 0.98;
      submenu.style.transform = `translateY(${follow}px)`;

      // マスクの不透明度も連動（最大 35% → 下げると薄く）
      const h = submenu.getBoundingClientRect().height || window.innerHeight * 0.7;
      const ratio = Math.min(1, follow / (h * 0.9));
      mask.style.opacity = String(1 - ratio * 0.9);
    }
  }

  function onDragEnd(){
    if (!drag.active){
      // ドラッグ未発火 → 何もしない（スクロールだった）
      return;
    }
    submenu.classList.remove("dragging");

    const CLOSE_DISTANCE = Math.min(window.innerHeight * 0.25, 220); // 距離しきい値
    const CLOSE_VELOCITY = 0.8 / 1000; // px/ms を 1/ms に換算（0.8px/ms ≒ 800px/s）

    const shouldClose = (drag.dy > CLOSE_DISTANCE) || (drag.vY > CLOSE_VELOCITY);

    if (shouldClose){
      // 下へアニメ → 終了後にhide
      submenu.style.transition = "transform .18s ease";
      submenu.style.transform = `translateY(110%)`;
      mask.classList.remove("show");
      submenu.addEventListener("transitionend", function te(){
        submenu.removeEventListener("transitionend", te);
        submenu.style.transition = "";
        hideMenu();
      });
    } else {
      // 元に戻す
      submenu.style.transition = "transform .18s ease";
      submenu.style.transform = "translateY(0)";
      mask.style.opacity = "";
      submenu.addEventListener("transitionend", function te2(){
        submenu.removeEventListener("transitionend", te2);
        submenu.style.transition = "";
      });
    }
  }

  // シート領域でのイベント登録（ボディではなくシートに限定）
  submenu.addEventListener("touchstart", onDragStart, {passive:true});
  submenu.addEventListener("touchmove",  onDragMove,  {passive:false});
  submenu.addEventListener("touchend",   onDragEnd,   {passive:true});
  submenu.addEventListener("touchcancel",onDragEnd,   {passive:true});

  // マウスでもドラッグできるようにしておく（任意）
  let mouseDown = false;
  submenu.addEventListener("mousedown",(e)=>{ mouseDown = true; onDragStart(e); });
  window.addEventListener("mousemove",(e)=>{ if(mouseDown) onDragMove(e); });
  window.addEventListener("mouseup",()=>{ if(mouseDown){ mouseDown=false; onDragEnd(); } });

  /* ---------- Long-Press on Tabs ---------- */
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
      timer = setTimeout(()=>{ longPressed = true; showMenu(type, btn); }, LONG_PRESS_MS);
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

  /* ---------- Active Tab Highlighter ---------- */
  (function activateTab() {
    const tabs = document.querySelectorAll(".tab-btn");
    if (!tabs.length) return;

    function norm(path) {
      try {
        const u = new URL(path, window.location.origin);
        let p = u.pathname;
        if (!p.endsWith("/")) p += "/";
        return p;
      } catch { return "/"; }
    }
    function setActiveByPath(pathname) {
      const here = norm(pathname || location.pathname);
      let best = null, bestLen = -1;
      tabs.forEach(btn => {
        const raw = btn.dataset.link || "/";
        const link = norm(raw);
        const isHome = link === "/";
        const hit = isHome ? (here === "/") : (here.startsWith(link));
        if (hit && link.length > bestLen) { best = btn; bestLen = link.length; }
      });
      tabs.forEach(b => b.classList.toggle("active", b === best));
    }

    setActiveByPath(location.pathname);
    window.addEventListener("popstate", () => setActiveByPath(location.pathname));
    document.querySelectorAll(".tab-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
      });
    });
  })();
});