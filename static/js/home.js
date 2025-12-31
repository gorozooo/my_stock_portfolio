(function(){
  const rail = document.getElementById("rail");
  const decks = Array.from(rail.querySelectorAll(".deck"));
  const dots = document.getElementById("dots");
  if (!rail || decks.length === 0 || !dots) return;

  dots.innerHTML = decks.map((_,i)=>`<span class="dot ${i===0?'on':''}" data-i="${i}"></span>`).join("");
  const dotEls = Array.from(dots.querySelectorAll(".dot"));

  const setOn = (idx) => {
    dotEls.forEach((d,i)=>d.classList.toggle("on", i===idx));
  };

  const onScroll = () => {
    const x = rail.scrollLeft;
    const w = rail.clientWidth || 1;
    const idx = Math.round(x / w);
    setOn(Math.max(0, Math.min(decks.length-1, idx)));
  };

  rail.addEventListener("scroll", () => {
    window.requestAnimationFrame(onScroll);
  });

  dotEls.forEach(el=>{
    el.addEventListener("click", ()=>{
      const i = parseInt(el.getAttribute("data-i") || "0", 10);
      rail.scrollTo({ left: i * rail.clientWidth, behavior: "smooth" });
    });
  });
})();
