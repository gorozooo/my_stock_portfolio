document.addEventListener("DOMContentLoaded", () => {
  const submenu = document.getElementById("submenu");
  const tabs = document.querySelectorAll(".tab-btn");
  const mask = document.querySelector(".btm-mask");

  /* ===================== Press Feedback ===================== */
  function attachPressFeedback(btn){
    const addPress = ()=> btn.classList.add("pressing");
    const clearPress = ()=>{
      btn.classList.remove("pressing");
      btn.classList.add("clicked");
      setTimeout(()=> btn.classList.remove("clicked"), 240);
    };

    if (window.PointerEvent){
      btn.addEventListener("pointerdown", addPress);
      btn.addEventListener("pointerup", clearPress);
      btn.addEventListener("pointercancel", ()=> btn.classList.remove("pressing"));
      btn.addEventListener("pointerleave", ()=> btn.classList.remove("pressing"));
    } else {
      btn.addEventListener("mousedown", addPress);
      btn.addEventListener("mouseup", clearPress);
      btn.addEventListener("mouseleave", ()=> btn.classList.remove("pressing"));
      btn.addEventListener("touchstart", addPress, {passive:true});
      btn.addEventListener("touchend", clearPress, {passive:true});
      btn.addEventListener("touchcancel", ()=> btn.classList.remove("pressing"), {passive:true});
    }
  }
  tabs.forEach(btn => attachPressFeedback(btn));

  /* ===================== Menu Show/Hide ===================== */
  function showMenu(){
    submenu.classList.add("show");
    mask.classList.add("show");
  }
  function hideMenu(){
    submenu.classList.remove("show");
    mask.classList.remove("show");
  }
  mask.addEventListener("click", hideMenu);

  /* ===================== Long Press Detection ===================== */
  const LONG_PRESS_MS = 500;
  tabs.forEach(btn=>{
    let timer, longPressed=false;

    btn.addEventListener("touchstart", ()=>{
      longPressed=false;
      clearTimeout(timer);
      timer = setTimeout(()=>{ longPressed=true; showMenu(); }, LONG_PRESS_MS);
    }, {passive:true});

    btn.addEventListener("touchend", ()=>{
      clearTimeout(timer);
      if (!longPressed){
        // 通常クリック → ページ遷移
        const link = btn.dataset.link;
        if (link) location.href = link;
      }
    }, {passive:true});
  });
});