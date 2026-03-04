// bottom_tab.js – Tab nav / Long-press sheet / Drag-to-close / Toast / Bounce
// 🧠 advisor タブ → aiapp タブ対応版

// 固定バーを <body> 直下へ移動して transform/backdrop-filter の影響を遮断
document.addEventListener("DOMContentLoaded", () => {
  const root = document.getElementById("bottomTabRoot");
  if (root && root.parentElement !== document.body) {
    document.body.appendChild(root);
  }
});

(function iosFixedFollowViewport(){
  const isIOS = /iP(hone|ad|od)/.test(navigator.platform) ||
                (navigator.userAgent.includes("Mac") && "ontouchend" in document);
  if (!isIOS || !window.visualViewport) return;
  const root = document.getElementById("bottomTabRoot");
  if (!root) return;

  const apply = () => {
    const vv = window.visualViewport;
    const offset = Math.max(0, (window.innerHeight - vv.height - vv.offsetTop));
    root.style.bottom = offset + "px";
  };
  visualViewport.addEventListener("resize", apply);
  visualViewport.addEventListener("scroll", apply);
  window.addEventListener("orientationchange", apply);
  apply();
})();

document.addEventListener("DOMContentLoaded", () => {
  const submenu = document.getElementById("submenu");
  const tabs    = document.querySelectorAll(".tab-btn");
  const mask    = document.querySelector(".btm-mask");
  const LONG_PRESS_MS = 500;
  if (!submenu || !mask || !tabs.length) return;

  /* --- 列数をタブ数に自動追従（CSS変数へ） --- */
  document.documentElement.style.setProperty("--tab-cols", String(tabs.length));

  /* --- Toast --- */
  let toast = document.getElementById("btmToast");
  if (!toast){
    toast = document.createElement("div");
    toast.id = "btmToast";
    Object.assign(toast.style,{
      position:"fixed",left:"50%",bottom:"84px",transform:"translate(-50%,24px)",
      background:"rgba(30,32,46,.96)",color:"#fff",padding:"8px 12px",fontSize:"13px",
      borderRadius:"10px",border:"1px solid rgba(255,255,255,.08)",
      boxShadow:"0 10px 28px rgba(0,0,0,.45)",opacity:"0",pointerEvents:"none",
      transition:"opacity .16s ease, transform .16s ease",zIndex:"100060"
    });
    document.body.appendChild(toast);
  }
  const showToast = (msg)=>{
    toast.textContent = msg;
    toast.style.opacity = "1";
    toast.style.transform = "translate(-50%,0)";
    setTimeout(()=>{ toast.style.opacity="0"; toast.style.transform="translate(-50%,24px)"; }, 1100);
  };

  /* --- Django 側から差し込めるURL辞書（フォールバック付き） --- */
  const URLS = Object.assign(
    {
      // Home
      home_base        : "/",
      home_panel_cash  : "/?panel=cash",
      home_panel_trend : "/?panel=trend",

      // AI (aiapp)
      aiapp_dashboard     : "/aiapp/",
      aiapp_root          : "/aiapp/",
      aiapp_picks         : "/aiapp/picks/",
      aiapp_behavior      : "/aiapp/behavior/",
      aiapp_settings      : "/aiapp/settings/",
      aiapp_simulate_list : "/aiapp/simulate/",

      // legacy Advisor (旧AI) – HOMEメニューからアクセスさせる
      //advisor_board    : "/advisor/board/",
      //advisor_notify   : "/advisor/notify-dashboard",
      //advisor_ab       : "/advisor/ab",
      //advisor_history  : "/advisor/policy",

      // Holdings / Realized
      holdings_base  : "/holdings/",
      holding_create : "/holdings/new/",
      realized_base  : "/realized/",

      // Trend
      trend_base: "/?panel=trend",

      // Cash
      cash_base        : "/cash/",
      cash_history     : "/cash/history/",
      cash_deposit     : "/cash/?action=deposit",
      cash_withdraw    : "/cash/?action=withdraw",
      cash_transfer    : "/cash/?action=transfer",

      // Dividends
      dividends_dashboard: "/dividends/dashboard/",
      dividends_base     : "/dividends/",
      dividend_create    : "/dividends/create/",
    },
    window.APP_URLS || {}
  );

  /* =========================
     URL方式: クエリ維持して遷移
     ========================= */
  const DEFAULTS = { sort:"updated", order:"desc" };

  function getCurrentParams(){
    const keepKeys = new Set(["sort","order","side","account","pnl","q","ticker","page","broker"]);
    const cur = new URLSearchParams(location.search);
    const out = new URLSearchParams();
    for (const [k,v] of cur.entries()){
      if (keepKeys.has(k) && v !== "") out.set(k, v);
    }
    if (!out.has("sort"))  out.set("sort",  DEFAULTS.sort);
    if (!out.has("order")) out.set("order", DEFAULTS.order);
    out.delete("page");
    return out;
  }

  function buildHoldingsURL(overrides = {}){
    const p = getCurrentParams();
    if (overrides.broker !== undefined){
      const b = String(overrides.broker||"").trim();
      if (b) p.set("broker", b); else p.delete("broker");
    }
    if (overrides.sort)  p.set("sort",  overrides.sort);
    if (overrides.order) p.set("order", overrides.order);
    if (overrides.q)     p.set("q", overrides.q);
    const qs = p.toString();
    return qs ? `${URLS.holdings_base}?${qs}` : `${URLS.holdings_base}`;
  }

  function buildCashHistoryURL(brokerJa = ""){
    const p = new URLSearchParams();
    if (brokerJa) p.set("broker", brokerJa);
    return p.toString() ? `${URLS.cash_history}?${p.toString()}` : URLS.cash_history;
  }

  /* --- メニュー定義 --- */
  const MENUS = {
    home: [
      { section:"ホーム" },
      { label:"トレンド",        href: URLS.trend_base,           icon:"📈", tone:"info" },
      { label:"設定を開く",      href:"/settings/trade",          icon:"⚙️", tone:"info" },
    ],
    // 🧠 AIタブ → aiapp メニュー
    advisor: [
      { section:"AI" },
      { label:"ダッシュボード", href: URLS.aiapp_dashboard,     icon:"🧠", tone:"info" },
      { label:"ピックアップ10選",         href: URLS.aiapp_picks,         icon:"🎯", tone:"info" },
      { label:"シミュレーション",     href: URLS.aiapp_simulate_list, icon:"🧪", tone:"info" },
      { label:"分析",         href: URLS.aiapp_behavior,      icon:"📊", tone:"info" },
      
      { section:"デイトレ" },
      { label:"ダッシュボード",  href:"/autotrade",     icon:"📝", tone:"info" },
      { label:"検証", href:"/autotrade/lab",     icon:"📟", tone:"info" },
      
      { section:"設定" },
      { label:"設定",           href: URLS.aiapp_settings,      icon:"⚙️", tone:"info" },
      { label:"ピックアップ診断(ALL)",           href: "/aiapp/debug/picks",      icon:"⚙️", tone:"info" },
      { label:"ピックアップ診断(Top10)",           href: "/aiapp/debug/picks/?kind=top",      icon:"⚙️", tone:"info" },
    ],
    
    holdings: [
      { section:"保有" },
      { label:"新規登録",        href: URLS.holding_create,       icon:"➕", tone:"add" },
      { label:"楽天証券",        action:"goto_broker", broker:"RAKUTEN", icon:"🏯", tone:"info" },
      { label:"松井証券",        action:"goto_broker", broker:"MATSUI",  icon:"📊", tone:"info" },
      { label:"SBI証券",         action:"goto_broker", broker:"SBI",     icon:"🏦", tone:"info" },
    ],
    dividends: [
      { section:"配当" },
      { label:"配当登録",        href: URLS.dividend_create,      icon:"➕", tone:"add" },
      { label:"明細",            href: URLS.dividends_base,       icon:"📑", tone:"info" },
      { label:"カレンダー",      href:"/dividends/calendar/",     icon:"📅", tone:"info" },
      { label:"予測",            href:"/dividends/forecast/",     icon:"📈", tone:"info" },
      { label:"ダッシュボード",  href: URLS.dividends_dashboard,  icon:"🏛️", tone:"info" },
    ],
    realized: [
      { section:"実現損益" },
      { label:"期間サマリー", action:"show_summary", icon:"📊", tone:"info" },
      { label:"月別サマリー", action:"show_summary", icon:"🗓️", tone:"info" },
      { label:"ランキング",   action:"show_ranking", icon:"🏅", tone:"info" },
      { label:"明細",         action:"show_details", icon:"📑", tone:"info" },
    ],
    cash: [
      { section:"台帳" },
      { label:"台帳（すべて）",   href: buildCashHistoryURL(""),    icon:"📒", tone:"info" },
      { label:"台帳（楽天証券）", href: buildCashHistoryURL("楽天"), icon:"🏯", tone:"info" },
      { label:"台帳（松井証券）", href: buildCashHistoryURL("松井"), icon:"📊", tone:"info" },
      { label:"台帳（SBI証券）",  href: buildCashHistoryURL("SBI"),  icon:"🏦", tone:"info" },
    ],
  };

  /* --- パス正規化 & 遷移 --- */
  const normPath = (p)=>{
    try{
      const u = new URL(p, location.origin);
      let x = u.pathname;
      if (x !== "/" && !x.endsWith("/")) x += "/";
      return x;
    }catch{
      return "/";
    }
  };

  const navigateTo = (url)=>{
    const target = url || "/";
    let targetPath = "/";
    try{
      targetPath = normPath(new URL(target, location.origin).pathname);
    }catch{}
    const active = Array.from(tabs).find(b => normPath(b.dataset.link||"/") === targetPath);
    if (active){
      tabs.forEach(b=> b.classList.remove("active"));
      active.classList.add("active");
      if (navigator.vibrate) navigator.vibrate(8);
      const label = active.querySelector("span")?.textContent?.trim() || "";
      showToast(`${label} に移動`);
    }
    setTimeout(()=>{ location.href = target; }, 60);
  };

  /* --- バウンス --- */
  const triggerBounce = (btn)=>{
    btn.classList.remove("pressing","clicked");
    btn.offsetWidth;
    btn.classList.add("clicked");
    setTimeout(()=> btn.classList.remove("clicked"), 220);
  };

  /* --- ボトムシート（描画/表示/非表示） --- */
  function renderMenu(type){
    const items = MENUS[type] || [];
    submenu.innerHTML = '<div class="grabber" aria-hidden="true"></div>';
    if (!items.length){
      const none = document.createElement("div");
      none.className = "section";
      none.textContent = "このタブのメニューは未設定です";
      submenu.appendChild(none);
      return;
    }
    items.forEach(it=>{
      if (it.section){
        const sec = document.createElement("div");
        sec.className = "section"; sec.textContent = it.section;
        submenu.appendChild(sec); return;
      }
      const b = document.createElement("button");
      b.className = `submenu-item tone-${it.tone||"info"}`;
      b.innerHTML = `<span class="ico">${it.icon||"•"}</span><span>${it.label}</span>`;
      b.addEventListener("click",(ev)=>{
        ev.stopPropagation(); hideMenu();
        if (it.href){ navigateTo(it.href); return; }
        window.dispatchEvent(new CustomEvent("bottomtab:action",{detail:{menu:type,action:it.action, payload:it}}));
      });
      submenu.appendChild(b);
    });
  }
  const showMenu=(type, btn)=>{
    renderMenu(type);
    mask.classList.add("show");
    submenu.classList.add("show");
    submenu.setAttribute("aria-hidden","false");
    btn?.classList.add("shake");
    setTimeout(()=>btn?.classList.remove("shake"), 320);
    if (navigator.vibrate) navigator.vibrate(10);
    document.documentElement.style.overflow="hidden";
    document.body.style.overflow="hidden";
  };
  const hideMenu=()=>{
    mask.classList.remove("show");
    submenu.classList.remove("dragging","show");
    submenu.setAttribute("aria-hidden","true");
    submenu.style.transform="";
    document.documentElement.style.overflow="";
    document.body.style.overflow="";
  };
  mask.addEventListener("click", hideMenu);
  submenu.addEventListener("contextmenu", e => e.preventDefault());

  // Drag to Close
  let drag = {startY:0, dy:0, active:false};
  const CLOSE_DISTANCE = 200;
  submenu.addEventListener("touchstart",(e)=>{
    if (!e.touches || !e.touches[0]) return;
    drag.startY = e.touches[0].clientY; drag.dy=0; drag.active=false;
  }, {passive:true});
  submenu.addEventListener("touchmove",(e)=>{
    if (!e.touches || !e.touches[0]) return;
    const dy = Math.max(0, e.touches[0].clientY - drag.startY);
    if (!drag.active && dy>0 && submenu.scrollTop<=0){
      drag.active = true; submenu.classList.add("dragging");
    }
    if (!drag.active) return;
    e.preventDefault();
    drag.dy = dy;
    submenu.style.transform = `translateY(${dy}px)`;
    const ratio = Math.min(1, dy/260);
    mask.style.opacity = String(1 - ratio*.9);
  }, {passive:false});
  function endDrag(){
    if (!drag.active) return;
    submenu.classList.remove("dragging");
    if (drag.dy > CLOSE_DISTANCE){
      submenu.style.transition="transform .16s ease"; submenu.style.transform="translateY(110%)";
      submenu.addEventListener("transitionend", function te(){
        submenu.removeEventListener("transitionend", te);
        submenu.style.transition=""; submenu.style.transform="";
        hideMenu();
      }, {once:true});
    }else{
      submenu.style.transition="transform .16s ease"; submenu.style.transform="translateY(0)";
      submenu.addEventListener("transitionend", ()=>{ submenu.style.transition=""; }, {once:true});
      mask.style.opacity="";
    }
  }
  submenu.addEventListener("touchend", endDrag, {passive:true});
  submenu.addEventListener("touchcancel", endDrag, {passive:true});

  /* --- タブ：タップ遷移 + 長押し --- */
  tabs.forEach(btn=>{
    const link = btn.dataset.link;
    const type = btn.dataset.menu; // home / advisor / holdings / dividends / realized / cash ...
    let timer=null, longPressed=false, moved=false;

    btn.addEventListener("contextmenu", e => e.preventDefault());

    // クリック
    btn.addEventListener("click",(e)=>{
      if (longPressed){ e.preventDefault(); longPressed=false; return; }

      const here = normPath(location.pathname);
      const me   = normPath(link||"/");

      // 保有：シングルタップで broker フィルタ解除して全件へ
      if (type === "holdings"){
        e.preventDefault(); triggerBounce(btn);
        navigateTo(buildHoldingsURL({ broker: "" })); return;
      }

      // 配当：シングルタップでダッシュボード
      if (type === "dividends"){
        e.preventDefault(); triggerBounce(btn);
        navigateTo(URLS.dividends_dashboard); return;
      }

      // 🧠 AI（aiapp）：シングルタップで dashboard へ
      if (type === "advisor"){
        e.preventDefault(); triggerBounce(btn);
        navigateTo(URLS.aiapp_dashboard); return;
      }

      // 既にそのタブ配下にいる → メニューを開く
      if (here.startsWith(me) && !submenu.classList.contains("show")){
        e.preventDefault(); showMenu(type, btn); return;
      }

      triggerBounce(btn);
      if (!submenu.classList.contains("show") && link) navigateTo(link);
    });

    // タッチ（長押し）
    btn.addEventListener("touchstart",(e)=>{
      e.preventDefault();
      longPressed=false; moved=false; clearTimeout(timer);
      timer=setTimeout(()=>{ longPressed=true; showMenu(type, btn); }, LONG_PRESS_MS);
    }, {passive:false});
    btn.addEventListener("touchmove",()=>{ moved=true; clearTimeout(timer); }, {passive:true});
    btn.addEventListener("touchcancel",()=> clearTimeout(timer), {passive:true});
    btn.addEventListener("touchend",()=>{
      clearTimeout(timer);
      if (longPressed || moved) return;

      if (type === "holdings"){
        navigateTo(buildHoldingsURL({ broker: "" }));
      }else if (type === "dividends"){
        navigateTo(URLS.dividends_dashboard);
      }else if (type === "advisor"){
        navigateTo(URLS.aiapp_dashboard);
      }else if (link){
        navigateTo(link);
      }
    }, {passive:true});
  });

  /* --- 初期アクティブ --- */
  (function markActive(){
    const here = normPath(location.pathname);
    const aiappRoot = (window.APP_URLS && window.APP_URLS.aiapp_root) || "/aiapp/";
    tabs.forEach(b=>{
      const link = normPath(b.dataset.link||"/");
      const isHome = link === "/";
      const isAdvisor = link === normPath(aiappRoot);
      let hit;
      if (isHome){
        hit = (here === "/");
      }else if (isAdvisor){
        // /aiapp/ 以下はすべて AI タブをアクティブ扱い
        hit = here.startsWith(normPath(aiappRoot));
      }else{
        hit = here.startsWith(link);
      }
      b.classList.toggle("active", !!hit);
    });
  })();

  /* --- サブメニューのアクション --- */
  window.addEventListener("bottomtab:action", (e)=>{
    const { menu, action, payload } = (e.detail||{});
    switch (action) {
      case "goto_broker": {
        const code = payload?.broker || "";
        const url  = buildHoldingsURL({ broker: code });
        navigateTo(url); break;
      }
      case "goto_all_brokers": {
        const url = buildHoldingsURL({ broker: "" });
        navigateTo(url); break;
      }
      default: break;
    }
  });

  window.openBottomMenu = (type = "cash") => showMenu(type, null);
});

/* ============ ヘルパ ============ */
function normPath(p){
  try{
    const u = new URL(p, location.origin);
    let x = u.pathname;
    if (x !== "/" && !x.endsWith("/")) x += "/";
    return x;
  }catch{
    return "/";
  }
}