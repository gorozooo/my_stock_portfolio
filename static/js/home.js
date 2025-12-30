(function(){
  // ----------------------------
  // Deck HUD (現在のセクション名)
  // ----------------------------
  const rail = document.getElementById("deckRail");
  const hudText = document.getElementById("hudText");

  if (rail && hudText){
    const decks = Array.from(rail.querySelectorAll(".deck"));
    const obs = new IntersectionObserver((entries)=>{
      let best = null;
      for (const e of entries){
        if (!e.isIntersecting) continue;
        if (!best || e.intersectionRatio > best.intersectionRatio) best = e;
      }
      if (best){
        const t = best.target.querySelector(".deck-title");
        hudText.textContent = t ? t.textContent.trim() : "Swipe";
      }
    }, { root: rail, threshold: [0.35,0.5,0.65] });

    decks.forEach(d => obs.observe(d));
  }

  // ----------------------------
  // Ticker (テロップ生成)
  // ----------------------------
  const track = document.getElementById("tickerTrack");

  function pickText(){
    const out = [];

    // サーバ描画済みのNEWS/TRNDSから拾う（見出しのみ）
    const newsDeck = document.querySelector('.deck[data-key="news_trends"]');
    if (newsDeck){
      // 注目セクター（chip）※「まだデータなし」も含むので filter
      const sectorChips = Array.from(newsDeck.querySelectorAll(".chip"))
        .map(x => x.textContent.replace(/\s+/g,' ').trim())
        .filter(t => t && !t.includes("まだデータなし"))
        .slice(0, 4);

      if (sectorChips.length) out.push("注目: " + sectorChips.join(" / "));

      // ニュース（2番目のpanel想定）
      const newsTitles = Array.from(newsDeck.querySelectorAll(".panel:nth-of-type(2) .li-title"))
        .map(x => x.textContent.trim())
        .filter(Boolean)
        .slice(0, 4);
      newsTitles.forEach(t => out.push("NEWS: " + t));

      // トレンド（3番目のpanel想定）
      const trendTitles = Array.from(newsDeck.querySelectorAll(".panel:nth-of-type(3) .li-title"))
        .map(x => x.textContent.trim())
        .filter(Boolean)
        .slice(0, 4);
      trendTitles.forEach(t => out.push("TREND: " + t));
    }

    // AI BRIEFの仮一言
    const brief = document.querySelector('.deck[data-key="ai_brief"] .quote');
    if (brief){
      const q = (brief.textContent || "").trim();
      if (q) out.unshift("AI: " + q);
    }

    if (!out.length){
      out.push("AI: 今日は“固定ルールで淡々と”。");
      out.push("NEWS: 取得待ち（5分キャッシュ）");
    }
    return out;
  }

  function buildTicker(){
    if (!track) return;

    const texts = pickText();

    // 2周分作って“途切れ”をなくす
    const items = texts.concat(texts).map(t => {
      const div = document.createElement("div");
      div.className = "ticker-item";
      div.textContent = t;
      return div;
    });

    track.innerHTML = "";
    items.forEach(i => track.appendChild(i));

    // 距離に応じて速度を調整
    requestAnimationFrame(()=>{
      const w = track.scrollWidth || 1;
      const dur = Math.max(18, Math.min(55, w / 120)); // 120px/s相当
      track.style.setProperty("--dur", dur + "s");
      track.classList.add("run");
    });
  }

  buildTicker();

  // ----------------------------
  // 時刻表示の整形（ISO→HH:MM）
  // ----------------------------
  const times = document.querySelectorAll(".time[data-iso]");
  times.forEach(el=>{
    const iso = el.getAttribute("data-iso");
    if (!iso) return;
    try{
      const dt = new Date(iso);
      const hh = String(dt.getHours()).padStart(2,"0");
      const mm = String(dt.getMinutes()).padStart(2,"0");
      el.textContent = `${hh}:${mm}`;
    }catch(e){}
  });
})();