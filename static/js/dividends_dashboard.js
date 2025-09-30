// dividends_dashboard.js – ダッシュボード非同期更新 + 進捗バー + ドーナツ + 達成トースト + ドリルダウン
(function(){
  const $  = (s, r=document)=> r.querySelector(s);
  const URLS = (window.DIVD_URLS||{});

  const fmt = n => Number(n||0).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
  const q   = v => encodeURIComponent(v||"");

  // Toast
  const toast = (function(){
    let el = $("#dashToast");
    return (msg)=>{
      if(!el) return;
      el.textContent = msg;
      el.style.opacity = "1";
      el.style.transform = "translate(-50%,0)";
      setTimeout(()=>{ el.style.opacity="0"; el.style.transform="translate(-50%,24px)"; }, 1400);
    };
  })();

  // ドーナツ（Canvas だけでシンプル描画）
  function drawDonut(canvasId, legendId, rows){
    const cv = document.getElementById(canvasId);
    const lg = document.getElementById(legendId);
    if (!cv || !lg){ return; }
    const ctx = cv.getContext("2d");
    ctx.clearRect(0,0,cv.width,cv.height);

    const total = rows.reduce((s,r)=> s + Number(r.net||0), 0) || 1;
    const cx = cv.width/2, cy = cv.height/2, r = Math.min(cx,cy)-4, inner = r*0.62;

    // 色は固定配列（環境を選ばない中間色）
    const COLORS = ["#6ea8ff","#9f7aea","#60c3a3","#f6c164","#f57ba0","#9ec27b","#c49bd8","#7bc0f7","#d18f6b","#b8b9ff"];

    let ang = -Math.PI/2;
    rows.forEach((row, i)=>{
      const val = Number(row.net||0);
      if (val <= 0) return;
      const ratio = val / total;
      const end = ang + Math.PI*2*ratio;
      ctx.beginPath();
      ctx.moveTo(cx,cy);
      ctx.arc(cx,cy,r, ang, end);
      ctx.closePath();
      ctx.fillStyle = COLORS[i % COLORS.length];
      ctx.globalAlpha = 0.95;
      ctx.fill();
      ang = end;
    });

    // 穴
    ctx.globalCompositeOperation = "destination-out";
    ctx.beginPath(); ctx.arc(cx,cy,inner,0,Math.PI*2); ctx.fill();
    ctx.globalCompositeOperation = "source-over";

    // 真ん中に合計
    ctx.fillStyle = "rgba(255,255,255,.9)";
    ctx.font = "600 14px system-ui, -apple-system, Segoe UI, Roboto";
    ctx.textAlign = "center";
    ctx.fillText(fmt(total), cx, cy+5);

    // 凡例
    lg.innerHTML = rows.map((r,i)=>{
      const color = COLORS[i%COLORS.length];
      const label = r.broker || r.account || r.label || "—";
      return `<div class="item"><div class="key"><span class="dot" style="background:${color}"></span>${label}</div><div>${fmt(r.net||0)}</div></div>`;
    }).join("") || '<div class="muted">—</div>';
  }

  function drill(params){
    const u = new URL(URLS.list || "/dividends/", location.origin);
    Object.entries(params).forEach(([k,v])=>{ if(v!==undefined && v!==null && v!=="") u.searchParams.set(k, v); });
    return u.toString();
  }

  // 月次ミニ棒（積み上げ）
  function drawMonthly(list){
    const wrap = $("#monthly_svg"); if(!wrap) return;
    const W=360,H=160,pad=18,bw=18,gap=12;
    const max = Math.max(1, ...list.map(x=> (x.net + x.tax)));
    const sy = v => H - pad - (v/max)*(H - pad*2);
    const svg = document.createElementNS("http://www.w3.org/2000/svg","svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("width", "100%"); svg.setAttribute("height", "100%");
    svg.innerHTML = `<path d="M${pad},${H-pad}H${W-pad}" stroke="rgba(255,255,255,.25)" fill="none"/>`;
    let x = pad;

    const tip  = document.getElementById("chartTip");
    const wrapRect = () => (wrap.getBoundingClientRect ? wrap.getBoundingClientRect() : {left:0,top:0});

    function showTip(cx,cy, m, net, tax){
      if (!tip) return;
      const r = wrapRect();
      tip.textContent = `${m}月  税引後 ${fmt(net)} / 税額 ${fmt(tax)}`;
      tip.style.left = (cx - r.left) + "px";
      tip.style.top  = (cy - r.top - 8) + "px";
      tip.style.display = "block";
    }
    function hideTip(){ if (tip) tip.style.display="none"; }

    list.forEach((d)=>{
      const hNet = (H - pad) - sy(d.net);
      const r1 = document.createElementNS("http://www.w3.org/2000/svg","rect");
      r1.setAttribute("x", x); r1.setAttribute("y", sy(d.net));
      r1.setAttribute("width", bw); r1.setAttribute("height", hNet);
      r1.setAttribute("rx", 3); r1.setAttribute("fill", "#6ea8ff");
      r1.dataset.m = d.m; r1.dataset.net = d.net; r1.dataset.tax = d.tax;
      svg.appendChild(r1);

      const hTax = (H - pad) - sy(d.net + d.tax) - hNet;
      const r2 = document.createElementNS("http://www.w3.org/2000/svg","rect");
      r2.setAttribute("x", x); r2.setAttribute("y", sy(d.net + d.tax));
      r2.setAttribute("width", bw); r2.setAttribute("height", hTax);
      r2.setAttribute("rx", 3); r2.setAttribute("fill", "#a0aec0");
      r2.dataset.m = d.m; r2.dataset.net = d.net; r2.dataset.tax = d.tax;
      svg.appendChild(r2);

      const t = document.createElementNS("http://www.w3.org/2000/svg","text");
      t.setAttribute("x", x + bw/2); t.setAttribute("y", H-4);
      t.setAttribute("text-anchor","middle"); t.setAttribute("font-size","9");
      t.setAttribute("fill","rgba(255,255,255,.75)"); t.textContent = d.m;
      svg.appendChild(t);

      x += bw + gap;
    });

    svg.addEventListener("mousemove", (e)=>{
      const el = e.target;
      if (el.tagName === "rect" && el.dataset.m){
        showTip(e.clientX, e.clientY, el.dataset.m, +el.dataset.net, +el.dataset.tax);
      }else{ hideTip(); }
    });
    svg.addEventListener("mouseleave", ()=>{ const tip = document.getElementById("chartTip"); if(tip) tip.style.display="none"; });
    svg.addEventListener("click", (e)=>{
      const el = e.target;
      if (el.tagName === "rect" && el.dataset.m){
        const year = $("#flt_year")?.value, broker=$("#flt_broker")?.value, account=$("#flt_account")?.value;
        location.href = drill({year, month: el.dataset.m, broker, account});
      }
    });
    wrap.replaceChildren(svg);
  }

  // 目標UI
  let prevAchieved = false;
  function setGoalUI(goal){
    const amount = Number(goal?.amount || 0);
    const pct    = Math.max(0, Math.min(100, Number(goal?.progress_pct || 0)));
    const remain = Number(goal?.remaining || 0);

    $("#goal_amount_view").textContent   = fmt(amount);
    const inp = $("#goal_amount_input"); if (inp) inp.value = amount ? amount.toFixed(2) : "";
    $("#goal_progress_view").textContent = pct.toFixed(2) + "%";
    $("#goal_remaining_view").textContent= fmt(remain);
    $("#goal_bar_inner").style.width     = pct + "%";

    const card = $("#goal_card");
    const achieved = pct >= 100;
    card?.classList.toggle("achieved", achieved);

    if (achieved && !prevAchieved){
      toast("🎉 目標を達成しました！");
      try{ navigator.vibrate && navigator.vibrate(20); }catch(_){}
    }
    prevAchieved = achieved;
  }

  // 取得＆反映
  async function fetchAndRender(){
    const year = $("#flt_year")?.value || "", broker=$("#flt_broker")?.value || "", account=$("#flt_account")?.value || "";
    const url = `${URLS.json}?year=${q(year)}&broker=${q(broker)}&account=${q(account)}`;
    const data = await fetch(url, {credentials:"same-origin"}).then(r=>r.json());

    $("#kpi_count").textContent = (data.kpi?.count ?? 0);
    $("#kpi_gross").textContent = fmt(data.kpi?.gross ?? 0);
    $("#kpi_tax").textContent   = fmt(data.kpi?.tax ?? 0);
    $("#kpi_net").textContent   = fmt(data.kpi?.net ?? 0);
    $("#kpi_yield").textContent = (Number(data.kpi?.yield_pct||0)).toFixed(2);

    setGoalUI(data.goal || {});

    const monthly = (data.monthly||[]).map(x=>({m:x.m, net:+x.net, tax:+x.tax}));
    drawMonthly(monthly);

    drawDonut("donut_broker",  "legend_broker",  data.by_broker  || []);
    drawDonut("donut_account", "legend_account", data.by_account || []);

    // Topも差し替え
    const topBox = document.getElementById("tbl_top");
    if (topBox){
      const rows = (data.top_symbols||[]).map(r=>`<div class="row"><span class="l">${r.label}</span><span class="r">${fmt(r.net)}</span></div>`).join("");
      topBox.innerHTML = rows || '<div class="muted">データなし</div>';
    }
  }

  // 反映・セレクト変更でAjax更新
  document.getElementById("flt_form")?.addEventListener("submit",(e)=>{ e.preventDefault(); fetchAndRender(); });
  ["#flt_year","#flt_broker","#flt_account"].forEach(sel=>{
    document.querySelector(sel)?.addEventListener("change", ()=> fetchAndRender());
  });

  // 目標保存
  document.getElementById("goal_save_btn")?.addEventListener("click", async ()=>{
    const year = document.getElementById("flt_year")?.value || "";
    const amount = document.getElementById("goal_amount_input")?.value || "0";
    try{
      const resp = await fetch(URLS.save_goal, {
        method:"POST",
        headers:{ "Content-Type":"application/x-www-form-urlencoded", "X-Requested-With":"fetch" },
        body:`year=${q(year)}&amount=${q(amount)}`
      });
      if (!resp.ok) throw new Error("save failed");
      toast("保存しました");
      fetchAndRender();
    }catch(_){ toast("保存に失敗しました"); }
  });

  // 初期描画
  fetchAndRender().catch(()=> {
    // 月次だけフォールバック描画
    try{
      const el = document.getElementById("js-monthly");
      if (!el) return;
      const list = JSON.parse(el.textContent||"[]").map(x=>({m:x.m, net:+x.net, tax:+x.tax}));
      drawMonthly(list);
    }catch(_){}
  });
})();