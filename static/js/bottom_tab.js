// bottom_tab.js – v15
// Pointer Events で nav スワイプを確実に検出 + 即時遷移
document.addEventListener("DOMContentLoaded", () => {
  const submenu = document.getElementById("submenu");
  const tabs = document.querySelectorAll(".tab-btn");
  const root = document.getElementById("bottomTabRoot") || document.body;
  const LONG_PRESS_MS = 500;

  /* ===== Toast ===== */
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
  const showToast = (msg)=>{
    toastEl.textContent = msg;
    toastEl.style.opacity = "1";
    toastEl.style.transform = "translate(-50%, 0)";
    setTimeout(()=>{
      toastEl.style.opacity = "0";
      toastEl.style.transform = "translate(-50%, 24px)";
    }, 1100);
  };

  /* ===== Mask ===== */
  let mask = document.querySelector(".btm-mask");
  if (!mask){
    mask = document.createElement("div");
    mask.className = "btm-mask";
    root.appendChild(mask);
  }
  mask.addEventListener("click", hideMenu);
  document.querySelectorAll(".bottom-tab, .submenu").forEach(el=>{
    el.addEventListener("contextmenu", e => e.preventDefault());
  });

  /* ===== Menus（省略：あなたの現行 MENUS をそのまま使う） ===== */
  const MENUS = {
    home: [
      { section: "クイック" },
      { label: "保有を追加",              action: "add_holding",    icon: "➕", tone: "add" },
      { label: "実現損益を記録",          action: "add_realized",   icon: "✍️", tone: "action" },
      { label: "設定を開く",              href: "/settings/trade/", icon: "⚙️", tone: "info" },
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

  /* ===== Helpers ===== */
  const normPath = (p)=>{
    try{
      const u = new URL(p, window.location.origin);
      let path = u.pathname;
      if (path !== "/" && !path.endsWith("/")) path += "/";
      return path;
    }catch{ return "/"; }
  };
  const getTabsArray = ()=> Array.from(document.querySelectorAll(".tab-btn"));
  const getActiveTabIndex = ()=>{
    const arr = getTabsArray();
    const idx = arr.findIndex(t => t.classList.contains("active"));
    return Math.max(0, idx);
  };
  const navigateTo = (rawLink)=>{
    const link = normPath(rawLink || "/");
    // 即時に確実な方法で遷移（iOSでも安定）
    try { window.location.href = link; return; } catch(e){}
    try { location.assign(link); return; } catch(e){}
    try { window.open(link, "_self"); return; } catch(e){}
    // 最後の保険
    try { history.pushState({}, "", link); location.reload(); } catch(e){ location.replace(link); }
  };

  function gotoTab(index, vibrate = true, toast = true){
    const arr = getTabsArray();
    if (!arr.length) return;
    const i = (index % arr.length + arr.length) % arr.length;
    const btn = arr[i];
    const link = normPath(btn.dataset.link || "/");
    hideMenu(true);
    arr.forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    if (vibrate && navigator.vibrate) navigator.vibrate(8);
    if (toast){
      const label = btn.querySelector("span")?.textContent?.trim() || link;
      showToast(`${label} に移動`);
    }
    // ごく短いハイライト
    btn.style.transition = "background-color .15s ease";
    const oldBg = btn.style.backgroundColor;
    btn.style.backgroundColor = "rgba(255,255,255,.08)";
    setTimeout(()=>{ btn.style.backgroundColor = oldBg || ""; }, 160);
    // 遅延なしで即遷移
    navigateTo(link);
  }

  /* ===== Bottom Sheet（ドラッグで閉じる） ===== */
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
        if (it.href){ navigateTo(it.href); return; }
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
    submenu.style.transform = "";
    submenu.setAttribute("aria-hidden","true");
    const unlock = ()=>{ document.documentElement.style.overflow=""; document.body.style.overflow=""; };
    if (!soft){ unlock(); } else { setTimeout(unlock, 0); }
  }

  // ドラッグして閉じる（縦）
  (function enableDragClose(){
    let startY=0, lastY=0, active=false;
    const CLOSE_DISTANCE = 200;
    submenu.addEventListener("pointerdown", (e)=>{
      if (submenu.scrollTop > 0) return; // 内部スクロール中は無効
      startY = lastY = e.clientY; active = true;
      submenu.setPointerCapture(e.pointerId);
    });
    submenu.addEventListener("pointermove", (e)=>{
      if (!active) return;
      const dy = Math.max(0, e.clientY - startY);
      lastY = e.clientY;
      submenu.style.transform = `translateY(${dy*0.98}px)`;
      submenu.classList.add("dragging");
    });
    const end = ()=>{
      if (!active) return;
      active = false;
      submenu.classList.remove("dragging");
      const dy = Math.max(0, lastY - startY);
      if (dy > CLOSE_DISTANCE){
        submenu.style.transition = "transform .16s ease";
        submenu.style.transform = "translateY(110%)";
        setTimeout(()=>{ submenu.style.transition=""; hideMenu(); }, 170);
      }else{
        submenu.style.transition = "transform .16s ease";
        submenu.style.transform = "translateY(0)";
        setTimeout(()=>{ submenu.style.transition=""; }, 170);
      }
    };
    submenu.addEventListener("pointerup", end);
    submenu.addEventListener("pointercancel", end);
  })();

  /* ===== 長押しメニュー（tab） ===== */
  tabs.forEach(btn=>{
    const link = btn.dataset.link;
    const type = btn.dataset.menu;
    let timer = null, longPressed = false, moved = false;

    // 通常タップで遷移
    btn.addEventListener("click",(e)=>{
      if (longPressed){ e.preventDefault(); longPressed = false; return; }
      if (!submenu.classList.contains("show") && link) navigateTo(link);
    });

    // 長押し
    btn.addEventListener("pointerdown",(e)=>{
      longPressed = false; moved = false; clearTimeout(timer);
      timer = setTimeout(()=>{ longPressed = true; showMenu(type, btn); }, LONG_PRESS_MS);
    });
    btn.addEventListener("pointermove",()=>{ moved = true; });
    ["pointerup","pointercancel","pointerleave"].forEach(ev=>{
      btn.addEventListener(ev, ()=>{
        clearTimeout(timer);
        if (!longPressed && !moved && link) navigateTo(link);
      });
    });

    // 右クリック
    btn.addEventListener("contextmenu",(e)=>{ e.preventDefault(); showMenu(type, btn); });
  });

  /* ===== Active Tab 表示 ===== */
  (function activateTab() {
    const tabs = document.querySelectorAll(".tab-btn");
    if (!tabs.length) return;
    function setActiveByPath(pathname) {
      const here = normPath(pathname || location.pathname);
      let best = null, bestLen = -1;
      tabs.forEach(btn => {
        const raw = btn.dataset.link || "/";
        const link = normPath(raw);
        const isHome = link === "/";
        const hit = isHome ? (here === "/") : (here.startsWith(link));
        if (hit && link.length > bestLen) { best = btn; bestLen = link.length; }
      });
      tabs.forEach(b => b.classList.toggle("active", b === best));
    }
    setActiveByPath(location.pathname);
    window.addEventListener("popstate", () => setActiveByPath(location.pathname));
  })();

  /* ===== 下タブ全体でスワイプ → タブ循環 ===== */
  (function attachNavSwipe(){
    const nav = document.querySelector(".btm-nav");
    if (!nav) return;

    // ブラウザジェスチャに奪われにくくする
    nav.style.touchAction = "none";

    const X_THRESH = 12;     // 最小距離
    const ANGLE_TAN = 0.45;  // 横優位
    let sx=0, sy=0, lx=0, ly=0, active=false;

    nav.addEventListener("pointerdown", (e)=>{
      active = true;
      sx = lx = e.clientX; sy = ly = e.clientY;
      nav.setPointerCapture(e.pointerId);
    });
    nav.addEventListener("pointermove", (e)=>{
      if (!active) return;
      lx = e.clientX; ly = e.clientY;
    });
    const end = ()=>{
      if (!active) return; active = false;
      const dx = lx - sx, dy = ly - sy;
      if (Math.abs(dx) >= X_THRESH && Math.abs(dx) >= Math.abs(dy)*ANGLE_TAN){
        const cur = getActiveTabIndex();
        if (dx < 0) gotoTab(cur + 1, true, true);
        else        gotoTab(cur - 1, true, true);
      }
    };
    nav.addEventListener("pointerup", end);
    nav.addEventListener("pointercancel", end);
    nav.addEventListener("pointerleave", end);
  })();
});