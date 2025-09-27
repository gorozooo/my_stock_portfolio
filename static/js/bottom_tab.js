// bottom_tab.js – Tab nav / Long-press sheet / Drag-to-close / Toast / Bounce
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

  /* --- Django 側で base.html 等から差し込めるURL辞書（なければフォールバック） --- */
  const URLS = Object.assign({},
    { holding_create: "/holdings/create/" },
    window.APP_URLS || {}
  );

  /* --- メニュー定義 --- */
  const MENUS = {
    home: [
      { section:"クイック" },
      { label:"保有を追加", href: URLS.holding_create, icon:"➕", tone:"add" },
      { label:"実現損益を記録", href:"/realized/", icon:"💰", tone:"action" },
      { label:"設定を開く", href:"/settings/trade/", icon:"⚙️", tone:"info" },
    ],
    holdings: [
      { section:"保有" },
      { label:"＋ 新規登録", href: URLS.holding_create, icon:"➕", tone:"add" },
      { label:"楽天証券", action:"goto_broker", broker:"RAKUTEN", icon:"🏯", tone:"info" },
      { label:"松井証券", action:"goto_broker", broker:"MATSUI",  icon:"📊", tone:"info" },
      { label:"SBI証券", action:"goto_broker", broker:"SBI",     icon:"🏦", tone:"info" },
    ],
    pnl: [
      { section:"実現損益" },
      { label:"期間サマリー", action:"show_summary", icon:"📊", tone:"info" },
      { label:"月別サマリー", href:"/realized/monthly/", icon:"🗓️", tone:"info" },
      { label:"ランキング", action:"show_ranking", icon:"🏅", tone:"info" },
      { label:"明細", action:"show_details", icon:"📑", tone:"info" },
    ],
    trend: [
      { section:"トレンド" },
      { label:"監視に追加", action:"watch_symbol", icon:"👁️", tone:"add" },
      { label:"エントリー/ストップ計算", action:"calc_entry_stop", icon:"🎯", tone:"info" },
      { label:"共有リンクをコピー", action:"share_link", icon:"🔗", tone:"info" },
      { label:"チャート設定", action:"chart_settings", icon:"🛠️", tone:"action" },
    ],
  };
  MENUS.realized = MENUS.pnl;

  /* --- ナビゲーション --- */
  const normPath = (p)=>{
    try{ const u=new URL(p,location.origin); let x=u.pathname; if(x!=="/"&&!x.endsWith("/")) x+="/"; return x; }
    catch{ return "/"; }
  };
  const navigateTo = (link)=>{
    const url = link || "/";
    let targetPath="/";
    try{ targetPath=normPath(new URL(url,location.origin).pathname);}catch{}
    const active = Array.from(tabs).find(b=>normPath(b.dataset.link||"/")===targetPath);
    if (active){
      tabs.forEach(b=>b.classList.remove("active"));
      active.classList.add("active");
      if (navigator.vibrate) navigator.vibrate(8);
      const label=active.querySelector("span")?.textContent?.trim()||"";
      showToast(`${label} に移動`);
    }
    setTimeout(()=>{ location.href=url; },60);
  };

  /* --- クイックバー保存値を利用して broker 遷移 --- */
  function readQB(){ try{ return JSON.parse(localStorage.getItem("holdings.qb.v1")||"{}"); }catch{return{};} }
  function buildQS(obj){ return new URLSearchParams(obj).toString(); }
  function gotoHoldingsWith(patch){
    const st=Object.assign({
      broker:"",account:"",side:"",pnl:"",
      sort:"updated",order:"desc",ticker:""
    },readQB(),patch||{});
    const qs=buildQS(st);
    navigateTo(`/holdings/?${qs}`);
  }

  /* --- ボトムシート --- */
  function renderMenu(type){
    const items = MENUS[type]||MENUS.realized||[];
    submenu.innerHTML='<div class="grabber" aria-hidden="true"></div>';
    if(!items.length){
      const none=document.createElement("div");
      none.className="section"; none.textContent="このタブのメニューは未設定です";
      submenu.appendChild(none); return;
    }
    items.forEach(it=>{
      if(it.section){
        const sec=document.createElement("div");
        sec.className="section"; sec.textContent=it.section;
        submenu.appendChild(sec); return;
      }
      const b=document.createElement("button");
      b.className=`submenu-item tone-${it.tone||"info"}`;
      b.innerHTML=`<span class="ico">${it.icon||"•"}</span><span>${it.label}</span>`;
      b.addEventListener("click",(ev)=>{
        ev.stopPropagation(); hideMenu();
        if(it.href){ navigateTo(it.href); return; }
        window.dispatchEvent(new CustomEvent("bottomtab:action",{detail:{menu:type,action:it.action,broker:it.broker}}));
      });
      submenu.appendChild(b);
    });
  }
  const showMenu=(type,btn)=>{
    renderMenu(type);
    mask.classList.add("show");
    submenu.classList.add("show");
    submenu.setAttribute("aria-hidden","false");
    btn?.classList.add("shake");
    setTimeout(()=>btn?.classList.remove("shake"),320);
    if(navigator.vibrate) navigator.vibrate(10);
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
  mask.addEventListener("click",hideMenu);
  submenu.addEventListener("contextmenu",e=>e.preventDefault());

  // Drag to Close
  let drag={startY:0,dy:0,active:false};
  const CLOSE_DISTANCE=200;
  submenu.addEventListener("touchstart",(e)=>{
    if(!e.touches||!e.touches[0])return;
    drag.startY=e.touches[0].clientY; drag.dy=0; drag.active=false;
  },{passive:true});
  submenu.addEventListener("touchmove",(e)=>{
    if(!e.touches||!e.touches[0])return;
    const dy=Math.max(0,e.touches[0].clientY-drag.startY);
    if(!drag.active&&dy>0&&submenu.scrollTop<=0){
      drag.active=true; submenu.classList.add("dragging");
    }
    if(!drag.active)return;
    e.preventDefault(); drag.dy=dy;
    submenu.style.transform=`translateY(${dy}px)`;
    const ratio=Math.min(1,dy/260); mask.style.opacity=String(1-ratio*.9);
  },{passive:false});
  function endDrag(){
    if(!drag.active)return;
    submenu.classList.remove("dragging");
    if(drag.dy>CLOSE_DISTANCE){
      submenu.style.transition="transform .16s ease"; submenu.style.transform="translateY(110%)";
      submenu.addEventListener("transitionend",function te(){
        submenu.removeEventListener("transitionend",te);
        submenu.style.transition=""; submenu.style.transform=""; hideMenu();
      },{once:true});
    }else{
      submenu.style.transition="transform .16s ease"; submenu.style.transform="translateY(0)";
      submenu.addEventListener("transitionend",()=>{submenu.style.transition="";},{once:true});
      mask.style.opacity="";
    }
  }
  submenu.addEventListener("touchend",endDrag,{passive:true});
  submenu.addEventListener("touchcancel",endDrag,{passive:true});

  /* --- タブ処理 --- */
  tabs.forEach(btn=>{
    const link=btn.dataset.link;
    const type=btn.dataset.menu;
    let timer=null,longPressed=false,moved=false;
    btn.addEventListener("contextmenu",e=>e.preventDefault());
    btn.addEventListener("click",(e)=>{
      if(longPressed){e.preventDefault(); longPressed=false; return;}
      const here=normPath(location.pathname);
      const me=normPath(link||"/");
      if(here.startsWith(me)&&!submenu.classList.contains("show")){
        e.preventDefault(); showMenu(type,btn); return;
      }
      triggerBounce(btn);
      if(!submenu.classList.contains("show")&&link) navigateTo(link);
    });
    btn.addEventListener("touchstart",(e)=>{
      e.preventDefault(); longPressed=false; moved=false; clearTimeout(timer);
      timer=setTimeout(()=>{longPressed=true; showMenu(type,btn);},LONG_PRESS_MS);
    },{passive:false});
    btn.addEventListener("touchmove",()=>{moved=true; clearTimeout(timer);},{passive:true});
    btn.addEventListener("touchcancel",()=>clearTimeout(timer),{passive:true});
    btn.addEventListener("touchend",()=>{
      clearTimeout(timer);
      if(!longPressed&&!moved&&link) navigateTo(link);
    },{passive:true});
  });

  /* --- 初期アクティブ --- */
  (function markActive(){
    const here=normPath(location.pathname);
    tabs.forEach(b=>{
      const link=normPath(b.dataset.link||"/");
      const isHome=link==="/";
      const hit=isHome?(here==="/"):here.startsWith(link);
      b.classList.toggle("active",!!hit);
    });
  })();

  /* --- サブメニューアクション --- */
  window.addEventListener("bottomtab:action",(e)=>{
    const {action,broker}=(e.detail||{});
    switch(action){
      case "add_holding": navigateTo(URLS.holding_create); break;
      case "goto_broker": gotoHoldingsWith({broker}); break;
      case "export_csv": alert("CSVエクスポートは未実装です"); break;
      case "open_filter": document.getElementById("qb")?.scrollIntoView({behavior:"smooth",block:"start"}); break;
      default: break;
    }
  });

  window.openBottomMenu=(type="realized")=>showMenu(type,null);
});