// bottom_tab.js – v10
// Haptics + BottomSheet + Icon/Color + Drag Follow + Grabber Swipe(Loop) + Toast
document.addEventListener("DOMContentLoaded", () => {
  const submenu = document.getElementById("submenu");
  const tabs = document.querySelectorAll(".tab-btn");
  const root = document.getElementById("bottomTabRoot") || document.body;
  const LONG_PRESS_MS = 500;

  // ====== Toast ======
  let toastEl = document.getElementById("btmToast");
  if (!toastEl){
    toastEl = document.createElement("div");
    toastEl.id = "btmToast";
    Object.assign(toastEl.style, {
      position:"fixed", left:"50%", bottom:"84px", transform:"translate(-50%, 24px)",
      background:"rgba(30,32,46,.96)", color:"#fff", padding:"8px 12px", fontSize:"13px",
      borderRadius:"10px", border:"1px solid rgba(255,255,255,.08)",
      boxShadow:"0 10px 28px rgba(0,0,0,.45)", opacity:"0", pointerEvents:"none",
      transition:"opacity .16s ease, transform .16s ease", zIndex:"10006"
    });
    document.body.appendChild(toastEl);
  }
  function showToast(msg){
    toastEl.textContent = msg;
    toastEl.style.opacity = "1";
    toastEl.style.transform = "translate(-50%, 0)";
    setTimeout(()=>{
      toastEl.style.opacity = "0";
      toastEl.style.transform = "translate(-50%, 24px)";
    }, 1200);
  }

  // ====== マスク ======
  let mask = document.querySelector(".btm-mask");
  if (!mask){
    mask = document.createElement("div");
    mask.className = "btm-mask";
    root.appendChild(mask);
  }
  mask.addEventListener("click", hideMenu);

  // iOS コンテキストメニュー抑止
  document.querySelectorAll(".bottom-tab, .submenu").forEach(el => {
    el.addEventListener("contextmenu", e => e.preventDefault());
  });

  // ====== ページ別メニュー ======
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

  // ===== タブ配列/現在位置/遷移（循環＋トースト） =====
  function getTabsArray(){ return Array.from(document.querySelectorAll(".tab-btn")); }
  function getActiveTabIndex(){
    const arr = getTabsArray();
    const idx = arr.findIndex(t => t.classList.contains("active"));
    return Math.max(0, idx);
  }
  function gotoTab(index, vibrate = true, toast = true){
  const arr = getTabsArray();
  if (!arr.length) return;
  const i = (index % arr.length + arr.length) % arr.length;
  const btn = arr[i];
  const link = btn.dataset.link || "/";

  hideMenu(true);

  arr.forEach(b => b.classList.remove("active"));
  btn.classList.add("active");

  if (vibrate && navigator.vibrate) navigator.vibrate(8);
  if (toast){
    const label = btn.querySelector("span")?.textContent?.trim() || link;
    showToast(`${label} に移動`);
  }

  console.log("gotoTab", link);  // ← 動作確認用
  setTimeout(()=> location.assign(link), 120); // ← 確実に遷移
}

  // ===== メニュー描画 =====
  function renderMenu(type){
    const items = MENUS[type] || [];
    submenu.innerHTML = "";

    const grab = document.createElement("div");
    grab.className = "grabber";
    grab.style.cursor = "grab";
    submenu.appendChild(grab);

    // grabber スワイプ（左右で循環切替）
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

  function hideMenu(soft=false){
    mask.classList.remove("show");
    submenu.classList.remove("dragging");
    submenu.classList.remove("show");
    submenu.style.transform = ""; // CSSに戻す
    submenu.setAttribute("aria-hidden","true");
    if (!soft){
      document.documentElement.style.overflow = "";
      document.body.style.overflow = "";
    } else {
      // soft のときもスクロール解除（確実に）
      setTimeout(()=>{ document.documentElement.style.overflow=""; document.body.style.overflow=""; }, 0);
    }
  }

  // ===== Drag Follow to Close =====
  let drag = { startY:0, lastY:0, startTime:0, lastTime:0, dy:0, vY:0, active:false };
  const CLOSE_DISTANCE = 220;
  const CLOSE_VELOCITY = 0.8 / 1000; // px/ms

  function canStartDrag(){ return submenu.scrollTop <= 0; }

  function onDragStart(e){
    const t = e.touches ? e.touches[0] : e;
    drag.startY = drag.lastY = t.clientY;
    drag.startTime = drag.lastTime = performance.now();
    drag.dy = 0; drag.vY = 0; drag.active = false;
  }
  function onDragMove(e){
    const t = e.touches ? e.touches[0] : e;
    const now = performance.now();
    const dy = Math.max(0, t.clientY - drag.startY);
    const dt = Math.max(1, now - drag.lastTime);
    const vy = (t.clientY - drag.lastY) / dt;

    if (!drag.active && dy > 0 && canStartDrag()){
      drag.active = true;
      submenu.classList.add("dragging");
    }
    if (drag.active){
      e.preventDefault();
      drag.dy = dy; drag.vY = vy; drag.lastY = t.clientY; drag.lastTime = now;
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
    const shouldClose = (drag.dy > CLOSE_DISTANCE) || (drag.vY > CLOSE_VELOCITY);
    if (shouldClose){
      submenu.style.transition = "transform .16s ease";
      submenu.style.transform = `translateY(110%)`;
      mask.classList.remove("show");
      submenu.addEventListener("transitionend", function te(){
        submenu.removeEventListener("transitionend", te);
        submenu.style.transition = "";
        hideMenu();
      });
    } else {
      submenu.style.transition = "transform .16s ease";
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

  // ===== Long-Press on Tabs =====
  tabs.forEach(btn=>{
    const link = btn.dataset.link;
    const type = btn.dataset.menu;
    let timer = null, longPressed = false, moved = false;

    btn.addEventListener("click",(e)=>{
      if (longPressed){ e.preventDefault(); longPressed = false; return; }
      if (!submenu.classList.contains("show") && link) window.location.href = link;
    });

    btn.addEventListener("touchstart",(e)=>{
      // 感度UPのためここでprevent（iOSコールアウト抑止 & 長押し安定）
      e.preventDefault();
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

  // ===== Active Tab Highlighter =====
  (function activateTab() {
    const tabs = document.querySelectorAll(".tab-btn");
    if (!tabs.length) return;
    function norm(path) {
      try { const u = new URL(path, window.location.origin);
        let p = u.pathname; if (!p.endsWith("/")) p += "/"; return p;
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

  // ===== Grabber Swipe（左右でタブ循環＋フリック対応） =====
  function attachGrabberSwipe(grabber){
    const X_THRESH = 20;      // ← 感度アップ（最小距離）
    const ANGLE_TAN = 0.6;    // ← 横優位判定を緩める（|dx| >= 0.6*|dy|）
    const V_THRESH = 0.6/1000; // ← 速度でも発火（約600px/s）

    let sx=0, sy=0, lx=0, ly=0, st=0, lt=0, active=false;

    function start(e){
      const t = e.touches ? e.touches[0] : e;
      // iOS の「テキスト選択/画像保存」抑止
      if (e.cancelable) e.preventDefault();
      sx = lx = t.clientX; sy = ly = t.clientY;
      st = lt = performance.now(); active = true;
    }
    function move(e){
      if (!active) return;
      const t = e.touches ? e.touches[0] : e;
      lx = t.clientX; ly = t.clientY; lt = performance.now();
    }
    function end(){
      if (!active) return; active = false;
      const dx = lx - sx, dy = ly - sy;
      const dt = Math.max(1, lt - st);
      const vx = dx / dt; // px/ms

      // 条件：距離 or 速度、かつ横優位
      const distanceOK = Math.abs(dx) >= X_THRESH;
      const velocityOK = Math.abs(vx) >= V_THRESH;
      const angleOK = Math.abs(dx) >= Math.abs(dy) * ANGLE_TAN;

      if ((distanceOK || velocityOK) && angleOK){
        const cur = getActiveTabIndex();
        if (dx < 0){ // 左→次
          gotoTab(cur + 1, true, true);
        } else {     // 右→前
          gotoTab(cur - 1, true, true);
        }
      }
    }

    grabber.addEventListener("touchstart", start, {passive:false});
    grabber.addEventListener("touchmove",  move,  {passive:true});
    grabber.addEventListener("touchend",   end,   {passive:true});
    grabber.addEventListener("mousedown",  (e)=>{ e.preventDefault(); start(e); });
    window.addEventListener("mousemove",   move);
    window.addEventListener("mouseup",     end);
  }
});