// aiapp/static/aiapp/js/behavior_dashboard.js
(function(){
  // --- ticker (iPhone確実) ---
  var track = document.getElementById("tickerTrack");
  if (track){
    var pxPerSec = 105;
    var x = 0;
    var last = performance.now();

    function getLoopWidth(){
      return Math.max(1, track.scrollWidth / 2);
    }
    var loopW = getLoopWidth();
    window.addEventListener("resize", function(){ loopW = getLoopWidth(); });

    function step(now){
      var dt = (now - last) / 1000;
      last = now;
      if (dt > 0.05) dt = 0.05;
      x -= pxPerSec * dt;
      if (-x >= loopW) x = 0;
      track.style.transform = "translate3d(" + x.toFixed(2) + "px,0,0)";
      requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  // --- swipe pager dot sync ---
  var pages = document.getElementById("pages");
  var nav = document.getElementById("dotNav");
  if (!pages || !nav) return;

  var btns = nav.querySelectorAll("button");
  function setActive(i){
    btns.forEach(function(b, idx){
      if (idx === i) b.classList.add("active");
      else b.classList.remove("active");
    });
  }

  function scrollToPage(i){
    var w = pages.clientWidth;
    pages.scrollTo({ left: w * i, behavior: "smooth" });
    setActive(i);
  }

  btns.forEach(function(b){
    b.addEventListener("click", function(){
      var i = parseInt(b.getAttribute("data-to") || "0", 10) || 0;
      scrollToPage(i);
    });
  });

  var ticking = false;
  pages.addEventListener("scroll", function(){
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(function(){
      var w = pages.clientWidth || 1;
      var i = Math.round(pages.scrollLeft / w);
      if (i < 0) i = 0;
      if (i > 3) i = 3;
      setActive(i);
      ticking = false;
    });
  }, { passive:true });
})();