(function(){
  const rail = document.getElementById("rail");
  const dots = document.getElementById("dots");
  if (!rail || !dots) return;

  const decks = Array.from(rail.querySelectorAll(".deck"));
  if (!decks || decks.length === 0) return;

  dots.innerHTML = decks.map((_,i)=>`<span class="dot ${i===0?'on':''}" data-i="${i}"></span>`).join("");
  const dotEls = Array.from(dots.querySelectorAll(".dot"));
  if (!dotEls || dotEls.length === 0) return;

  const setOn = (idx) => {
    dotEls.forEach((d,i)=>d.classList.toggle("on", i===idx));
  };

  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

  const onScroll = () => {
    const x = rail.scrollLeft || 0;
    const w = rail.clientWidth || 1;
    const idx = Math.round(x / w);
    setOn(clamp(idx, 0, decks.length - 1));
  };

  rail.addEventListener("scroll", () => {
    window.requestAnimationFrame(onScroll);
  }, { passive: true });

  dotEls.forEach(el=>{
    el.addEventListener("click", ()=>{
      const i = parseInt(el.getAttribute("data-i") || "0", 10);
      const w = rail.clientWidth || 1;
      rail.scrollTo({ left: i * w, behavior: "smooth" });
    });
  });
})();