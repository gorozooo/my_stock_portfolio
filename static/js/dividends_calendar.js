(function(){
  const $  = (s, r=document)=> r.querySelector(s);
  const $$ = (s, r=document)=> Array.from(r.querySelectorAll(s));
  const URL = window.DIVD_CAL?.json;

  const yen = n => `${Math.round(Number(n||0)).toLocaleString()}円`;

  // カレンダー骨格（必ず描く）
  function buildGrid(year, month){
    const first = new Date(year, month-1, 1);
    const firstDow = first.getDay(); // 0=Sun
    const lastDay = new Date(year, month, 0).getDate();

    const tb = $("#calBody");
    tb.innerHTML = "";
    let d = 1 - firstDow; // 前月分の空白を含めて計算
    for (let r=0; r<6; r++){
      const tr = document.createElement("tr");
      for (let c=0; c<7; c++){
        const td = document.createElement("td");
        const cell = document.createElement("div");
        cell.className = "cell";
        td.appendChild(cell);

        if (d>=1 && d<=lastDay){
          const day = document.createElement("div");
          day.className = "day";
          day.textContent = d;
          cell.appendChild(day);
          cell.dataset.d = d; // バッジ配置＆クリック用
        }
        tr.appendChild(td);
        d++;
      }
      tb.appendChild(tr);
    }
  }

  // バッジを重ねるだけ（グリッドは壊さない）
  function applyBadges(days, y, m){
    // 既存バッジを消す
    $$(".cell .badge").forEach(b=> b.remove());

    (days||[]).forEach(bucket=>{
      if (!bucket.d || !bucket.total) return;
      const cell = document.querySelector(`.cell[data-d="${bucket.d}"]`);
      if (!cell) return;
      const badge = document.createElement("div");
      badge.className = "badge";
      badge.textContent = yen(bucket.total);
      badge.addEventListener("click", (e)=>{
        e.stopPropagation();
        showPop(y, m, bucket);
      });
      cell.appendChild(badge);
      cell.addEventListener("click", ()=> showPop(y, m, bucket)); // セル全体でもOK
    });
  }

  // 明細ポップ
  function showPop(y, m, bucket){
    $("#popTitle").textContent = `${y}/${m}/${bucket.d} の配当`;
    const list = $("#popList");
    list.innerHTML = (bucket.items||[]).map(it=>{
      const left = `${it.ticker || ""} ${it.name || ""}`.trim();
      return `<div class="item"><span>${left}</span><span>${yen(it.net)}</span></div>`;
    }).join("") || `<div class="muted">明細なし</div>`;
    $("#dayPop").style.display = "block";
  }
  $("#popClose").addEventListener("click", ()=> $("#dayPop").style.display="none");
  window.addEventListener("click", (e)=>{ if (!e.target.closest(".pop")) $("#dayPop").style.display="none"; });

  async function fetchJson(y, m, broker, account){
    try{
      const u = new URL(URL, location.origin);
      u.searchParams.set("year", y);
      u.searchParams.set("month", m);
      if (broker)  u.searchParams.set("broker", broker);
      if (account) u.searchParams.set("account", account);
      const res = await fetch(u, {credentials:"same-origin"});
      if (!res.ok) throw new Error("bad status");
      return await res.json();
    }catch(e){
      console.warn("[calendar] fetch failed:", e);
      return { days: [], year: Number(y), month: Number(m) };
    }
  }

  // 読み込み＋反映（常に先にグリッドを描く）
  async function loadAndRender(){
    const y = $("#year").value || new Date().getFullYear();
    const m = $("#month").value || (new Date().getMonth()+1);
    const broker  = $("#broker").value;
    const account = $("#account").value;

    buildGrid(Number(y), Number(m));              // ここで先に描画
    const data = await fetchJson(y, m, broker, account);
    applyBadges(data.days, Number(y), Number(m)); // バッジだけ後から
  }

  // 変更で再読込
  ["#year","#month","#broker","#account"].forEach(sel=>{
    $(sel)?.addEventListener("change", loadAndRender);
  });

  // 初期表示（DOMContentLoaded で即）
  document.addEventListener("DOMContentLoaded", loadAndRender);
})();