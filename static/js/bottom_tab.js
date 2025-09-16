// bottom_tab.js – Haptics + BottomSheet + Icon/Color + Drag Follow + Grabber Swipe(Loop) + Toast
document.addEventListener("DOMContentLoaded", () => {
  const submenu = document.getElementById("submenu");
  const tabs = document.querySelectorAll(".tab-btn");
  const root = document.getElementById("bottomTabRoot") || document.body;
  const LONG_PRESS_MS = 550;

  // ====== Toast ======
  let toastEl = document.getElementById("btmToast");
  if (!toastEl){
    toastEl = document.createElement("div");
    toastEl.id = "btmToast";
    // 最低限のスタイル（CSS無くても動くようにインラインで）
    toastEl.style.position = "fixed";
    toastEl.style.left = "50%";
    toastEl.style.bottom = "80px";
    toastEl.style.transform = "translateX(-50%) translateY(20px)";
    toastEl.style.background = "rgba(30,32,46,.95)";
    toastEl.style.color = "#fff";
    toastEl.style.padding = "8px 12px";
    toastEl.style.fontSize = "13px";
    toastEl.style.borderRadius = "10px";
    toastEl.style.border = "1px solid rgba(255,255,255,.08)";
    toastEl.style.boxShadow = "0 6px 20px rgba(0,0,0,.35)";
    toastEl.style.opacity = "0";
    toastEl.style.pointerEvents = "none";
    toastEl.style.transition = "opacity .18s ease, transform .18s ease";
    toastEl.style.zIndex = "10005";
    document.body.appendChild(toastEl);
  }
  function showToast(msg){
    toastEl.textContent = msg;
    toastEl.style.opacity = "1";
    toastEl.style.transform = "translateX(-50%) translateY(0)";
    setTimeout(()=>{
      toastEl.style.opacity = "0";
      toastEl.style.transform = "translateX(-50%) translateY(20px)";
    }, 1200);
  }

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

  /* ===== タブ配列/現在位置/遷移（循環） ===== */
  function getTabsArray(){ return Array.from(document.querySelectorAll(".tab-btn")); }
  function getActiveTabIndex(){
    const arr = getTabsArray();
    const idx = arr.findIndex(t => t.classList.contains("active"));
    return Math.max(0, idx);
  }
  function gotoTab(index, vibrate = true, toast = true){
    const arr = getTabsArray();
    if (!arr.length) return;
    // 循環
    const i = (index % arr.length + arr.length) % arr.length;
    const btn = arr[i];
    const link = btn.dataset.link || "/";
    // 先にUI反映
    arr.forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    if (vibrate && navigator.vibrate) navigator.vibrate(8);
    if (toast){
      const label = btn.querySelector("span")?.textContent?.trim() || link;
      showToast(`${label} に移動`);
    }
    window.location.href = link;
  }

  /* ---------- Bottom Sheet Rendering ---------- */
  function renderMenu(type){
    const items = MENUS[type] || [];
    submenu.innerHTML = "";

    const grab = document.createElement("div");
    grab.className = "grabber";
    grab.style.cursor = "grab";
    submenu.appendChild(grab);

    // grabber に左右スワイプ（循環切替 + トースト）
    attachGrabberSwipe(grab);

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
  let drag = { startY:0, lastY:0, startTime:0, lastTime:0, dy:0, vY:0, active:false };

  function canStartDrag(){ return submenu.scrollTop <= 0; }

  function onDragStart(e){
    const y = (e.touches ? e.touches[0].clientY : e.clientY);
    drag.startY = y; drag.lastY = y;
    drag.startTime = drag.lastTime = performance.now();
    drag.dy = 0; drag.vY = 0; drag.active = false;
  }

  function onDragMove(e){
    const y = (e.touches ? e.touches[0].clientY : e.clientY);
    const now = performance.now();
    const dy = Math.max(0, y - drag.startY);
    const dt = Math.max(1, now - drag.lastTime);
    const vy = (y - drag.lastY) / dt;

    if (!drag.active && dy > 0 && canStartDrag()){
      drag.active = true;
      submenu.classList.add("dragging");
    }

    if (drag.active){
      e.preventDefault();
      drag.dy = dy; drag.vY = vy; drag.lastY = y; drag.lastTime = now;

      const follow = dy * 0.98;
      submenu.style.transform = `translateY(${follow}px)`;

      const h = submenu.getBoundingClientRect().height || window.innerHeight * 0.7;
      const ratio = Math.min(1, follow / (h * 0.9));
      mask.style.opacity = String(1 - ratio * 0.9);
    }
  }

  function onDragEnd(){
    if (!drag.active) return;
    submenu.classList.remove("dragging");

    const CLOSE_DISTANCE = Math.min(window.innerHeight * 0.25, 220);
    const CLOSE_VELOCITY = 0.8 / 1000; // px/ms

    const shouldClose = (drag.dy > CLOSE_DISTANCE) || (drag.vY > CLOSE_VELOCITY);

    if (shouldClose){
      submenu.style.transition = "transform .18s ease";
      submenu.style.transform = `translateY(110%)`;
      mask.classList.remove("show");
      submenu.addEventListener("transitionend", function te(){
        submenu.removeEventListener("transitionend", te);
        submenu.style.transition = "";
        hideMenu();
      });
    } else {
      submenu.style.transition = "transform .18s ease";
      submenu.style.transform = "translateY(0)";
      mask.style.opacity = "";
      submenu.addEventListener("transitionend", function te2(){
        submenu.removeEventListener("transitionend", te2);
        submenu.style.transition = "";
      });
    }
  }

  submenu.addEventListener("touchstart", onDragStart, {passive:true});
  submenu.addEventListener("touchmove",  onDragMove,  {passive:false});
  submenu.addEventListener("touchend",   onDragEnd,   {passive:true});
  submenu.addEventListener("touchcancel",onDragEnd,   {passive:true});
  let mouseDown = false;
  submenu.addEventListener("mousedown",(e)=>{ mouseDown = true; onDragStart(e); });
  window.addEventListener("mousemove",(e)=>{ if(mouseDown) onDragMove(e); });
  window.addEventListener("mouseup",()=>{ if(mouseDown){ mouseDown=false; onDragEnd(); } });

  /* ---------- Long-Press on Tabs ---------- */
  tabs.forEach(btn=>{
    const link = btn.dataset.link;
    const type = btn.dataset.menu;
    let timer = null, longPressed = false, moved = false;

    btn.addEventListener("click",(e)=>{
      if (longPressed){ e.preventDefault(); longPressed = false; return; }
      if (!submenu.classList.contains("show") && link) window.location.href = link;
    });

    btn.addEventListener("touchstart",(e)=>{
      e.preventDefault(); // iOS コールアウト抑止
      longPressed = false; moved = false; clearTimeout(timer);
      timer = setTimeout(()=>{ longPressed = true; showMenu(type, btn); }, LONG_PRESS_MS);
    }, {passive:false});

    btn.addEventListener("touchmove",()=>{ moved = true; clearTimeout(timer); }, {passive:true});
    btn.addEventListener("touchcancel",()=> clearTimeout(timer), {passive:true});
    btn.addEventListener("touchend",()=>{
      clearTimeout(timer);
      if (!longPressed && !moved && link) window.location.href = link;
    }, {passive:true});

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

  /* ---------- Grabber Swipe (左右でタブ循環) ---------- */
  function attachGrabberSwipe(grabber){
    const X_THRESH = 48;      // 最低スワイプ距離(px)
    const ANGLE_TAN = 1.2;    // 横優位判定 |dx| >= 1.2*|dy|

    let sx=0, sy=0, lx=0, ly=0, active=false;

    function start(e){
      const t = e.touches ? e.touches[0] : e;
      sx = lx = t.clientX; sy = ly = t.clientY; active = true;
    }
    function move(e){
      if (!active) return;
      const t = e.touches ? e.touches[0] : e;
      lx = t.clientX; ly = t.clientY;
    }
    function end(){
      if (!active) return; active = false;
      const dx = lx - sx, dy = ly - sy;
      if (Math.abs(dx) < X_THRESH) return;
      if (Math.abs(dx) < Math.abs(dy) * ANGLE_TAN) return;

      const cur = getActiveTabIndex();
      if (dx < 0){
        // 左へ → 次（循環）
        gotoTab(cur + 1, true, true);
      } else {
        // 右へ → 前（循環）
        gotoTab(cur - 1, true, true);
      }
    }

    grabber.addEventListener("touchstart", start, {passive:true});
    grabber.addEventListener("touchmove",  move,  {passive:true});
    grabber.addEventListener("touchend",   end,   {passive:true});
    grabber.addEventListener("mousedown",  (e)=>{ e.preventDefault(); start(e); });
    window.addEventListener("mousemove",   move);
    window.addEventListener("mouseup",     end);
  }
});