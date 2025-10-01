// dividends_dashboard.js – 月次/ドーナツ/進捗バー/達成トースト/ドリルダウン + フィルタ即時反映
(function(){
  const $  = (s, r=document)=> r.querySelector(s);
  const $$ = (s, r=document)=> Array.from(r.querySelectorAll(s));
  const URLS   = window.DIVD_URLS   || {};
  const LABELS = window.DIVD_LABELS || {broker:{}, account:{}};

  const fmt =(n)=> Number(n||0).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
  const q   =(v)=> encodeURIComponent(v||"");

  /* ------------ Toast ------------ */
  const toast = $("#dashToast");
  const showToast=(msg)=>{
    if(!toast) return;
    toast.textContent = msg;
    toast.style.opacity="1";
    toast.style.transform="translate(-50%,0)";
    setTimeout(()=>{ toast.style.opacity="0"; toast.style.transform="translate(-50%,24px)"; }, 1400);
  };

  const drill=(params)=>{
    const u = new URL(URLS.list, location.origin);
    Object.entries(params).forEach(([k,v])=>{ if(v!==undefined && v!==null && v!=="") u.searchParams.set(k, v); });
    return u.toString();
  };

  /* ------------ 月次（税引後+税額） ------------ */
  function drawMonthly(list){
    const wrap = $("#monthly_svg"); if(!wrap) return;
    const W=360,H=160,pad=18,bw=18,gap=12;
    const max = Math.max(1, ...list.map(x=> (x.net + x.tax)));
    const sy = v => H - pad - (v/max)*(H - pad*2);
    const svg = document.createElementNS("http://www.w3.org/2000/svg","svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("width","100%"); svg.setAttribute("height","100%");
    svg.innerHTML = `<path d="M${pad},${H-pad}H${W-pad}" stroke="rgba(255,255,255,.25)" fill="none"/>`;
    let x = pad;

    const tip = $("#chartTip");
    const wrapRect = ()=> wrap.getBoundingClientRect ? wrap.getBoundingClientRect() : {left:0,top:0};
    const showTip=(cx,cy,m,net,tax)=>{
      if(!tip) return;
      const r = wrapRect();
      tip.textContent = `${m}月  税引後 ${fmt(net)} / 税額 ${fmt(tax)}`;
      tip.style.left = (cx - r.left) + "px";
      tip.style.top  = (cy - r.top - 8) + "px";
      tip.style.display = "block";
    };
    const hideTip=()=>{ if(tip) tip.style.display="none"; };

    list.forEach(d=>{
      const hNet = (H - pad) - sy(d.net);
      const r1 = document.createElementNS("http://www.w3.org/2000/svg","rect");
      r1.setAttribute("x", x); r1.setAttribute("y", sy(d.net));
      r1.setAttribute("width", bw); r1.setAttribute("height", hNet);
      r1.setAttribute("rx", 3); r1.setAttribute("fill", "#6ea8ff");
      r1.dataset.m=d.m; r1.dataset.net=d.net; r1.dataset.tax=d.tax;
      svg.appendChild(r1);

      const hTax = (H - pad) - sy(d.net + d.tax) - hNet;
      const r2 = document.createElementNS("http://www.w3.org/2000/svg","rect");
      r2.setAttribute("x", x); r2.setAttribute("y", sy(d.net + d.tax));
      r2.setAttribute("width", bw); r2.setAttribute("height", hTax);
      r2.setAttribute("rx", 3); r2.setAttribute("fill", "#a0aec0");
      r2.dataset.m=d.m; r2.dataset.net=d.net; r2.dataset.tax=d.tax;
      svg.appendChild(r2);

      const t = document.createElementNS("http://www.w3.org/2000/svg","text");
      t.setAttribute("x", x + bw/2); t.setAttribute("y", H-4);
      t.setAttribute("text-anchor","middle"); t.setAttribute("font-size","9");
      t.setAttribute("fill","rgba(255,255,255,.75)"); t.textContent = d.m;
      svg.appendChild(t);

      x += bw + gap;
    });

    svg.addEventListener("mousemove",(e)=>{
      const el = e.target;
      if (el.tagName==="rect" && el.dataset.m){ showTip(e.clientX,e.clientY,el.dataset.m,+el.dataset.net,+el.dataset.tax); }
      else { hideTip(); }
    });
    svg.addEventListener("mouseleave", hideTip);
    svg.addEventListener("click",(e)=>{
      const el = e.target;
      if (el.tagName==="rect" && el.dataset.m){
        const year=$("#flt_year").value, broker=$("#flt_broker").value, account=$("#flt_account").value;
        location.href = drill({year, month: el.dataset.m, broker, account});
      }
    });
    svg.addEventListener("touchstart",(e)=>{
      const el = e.target; if(!(el && el.tagName==="rect" && el.dataset.m)) return;
      const t = e.touches[0]; showTip(t.clientX,t.clientY,el.dataset.m,+el.dataset.net,+el.dataset.tax);
    }, {passive:true});
    svg.addEventListener("touchend", ()=> hideTip(), {passive:true});

    wrap.replaceChildren(svg);
  }

  /* ------------ ドーナツ（右凡例） ------------ */
  function drawDonut(svgId, legendId, rows, opts){
    const svg = document.getElementById(svgId);
    const legend = document.getElementById(legendId);
    if (!svg || !legend){ return; }
    svg.innerHTML = ""; legend.innerHTML = "";

    if (!rows || !rows.length){
      legend.innerHTML = `<div class="muted">データなし</div>`;
      return;
    }
    rows = rows.slice().sort((a,b)=> (b.net||0)-(a.net||0));
    const total = rows.reduce((s,r)=> s + Number(r.net||0), 0) || 1;

    const cx=60, cy=60, r=38, sw=14;
    const C = 2 * Math.PI * r;
    let acc = 0;

    // 背景リング
    const bg = document.createElementNS("http://www.w3.org/2000/svg","circle");
    bg.setAttribute("cx",cx);bg.setAttribute("cy",cy);bg.setAttribute("r",r);
    bg.setAttribute("fill","none");bg.setAttribute("stroke","rgba(255,255,255,.10)");
    bg.setAttribute("stroke-width",sw);
    svg.appendChild(bg);

    rows.forEach((row,i)=>{
      const val = Number(row.net||0);
      const ratio = Math.max(0, val/total);
      const segLen = C * ratio;
      const circle = document.createElementNS("http://www.w3.org/2000/svg","circle");
      circle.setAttribute("cx",cx); circle.setAttribute("cy",cy); circle.setAttribute("r",r);
      circle.setAttribute("fill","none"); circle.setAttribute("stroke-width",sw);
      const hue = Math.round((i*57)%360);
      const color = `hsl(${hue} 70% 65%)`;
      circle.setAttribute("stroke", color);
      circle.setAttribute("stroke-dasharray", `${segLen} ${C-segLen}`);
      circle.setAttribute("transform", `rotate(-90 ${cx} ${cy})`);
      circle.setAttribute("stroke-dashoffset", String(-acc*C));
      svg.appendChild(circle);
      acc += ratio;

      // 凡例
      const raw = row[opts.key];
      const shown = (opts.labels||{})[raw] || raw || "—";
      const pct = (val/total*100)||0;
      const a = document.createElement("a");
      a.className = "row";
      a.href = drill({ year: $("#flt_year").value, [opts.key]: raw });
      a.innerHTML = `
        <span class="l"><i class="swatch" style="background:${color}"></i>${shown}</span>
        <span class="r">${fmt(val)}&nbsp;&nbsp;<span class="muted">${pct.toFixed(1)}%</span></span>
      `;
      legend.appendChild(a);
    });

    // 中央値
    const t = document.createElementNS("http://www.w3.org/2000/svg","text");
    t.setAttribute("x", cx); t.setAttribute("y", cy+4);
    t.setAttribute("text-anchor","middle"); t.setAttribute("font-size","11");
    t.setAttribute("fill","rgba(255,255,255,.85)"); t.textContent = fmt(total);
    svg.appendChild(t);
  }

  /* ------------ 目標UI ------------ */
  let prevAchieved=false;
  function setGoalUI(goal){
    const amount = Number(goal?.amount||0);
    const pct    = Math.max(0, Math.min(100, Number(goal?.progress_pct||0)));
    const remain = Number(goal?.remaining||0);
    $("#goal_amount_view").textContent   = fmt(amount);
    $("#goal_amount_input").value        = amount ? amount.toFixed(2) : "";
    $("#goal_progress_view").textContent = pct.toFixed(2) + "%";
    $("#goal_remaining_view").textContent= fmt(remain);
    $("#goal_bar_inner").style.width     = pct + "%";
    const card = $("#goal_card");
    const achieved = pct >= 100;
    card.classList.toggle("achieved", achieved);
    if (achieved && !prevAchieved){ showToast("🎉 目標を達成しました！"); try{navigator.vibrate?.(20);}catch{} }
    prevAchieved = achieved;
  }

  /* ------------ 取得＆反映 ------------ */
  async function fetchAndRender(){
    const year = $("#flt_year").value, broker=$("#flt_broker").value, account=$("#flt_account").value;
    const url = `${URLS.json}?year=${q(year)}&broker=${q(broker)}&account=${q(account)}`;
    const data = await fetch(url, {credentials:"same-origin"}).then(r=>r.json());

    // KPI
    $("#kpi_count").textContent = (data.kpi?.count ?? 0);
    $("#kpi_gross").textContent = fmt(data.kpi?.gross ?? 0);
    $("#kpi_tax").textContent   = fmt(data.kpi?.tax ?? 0);
    $("#kpi_net").textContent   = fmt(data.kpi?.net ?? 0);
    $("#kpi_yield").textContent = (Number(data.kpi?.yield_pct||0)).toFixed(2);

    // 目標
    setGoalUI(data.goal || {});

    // 月次
    drawMonthly((data.monthly||[]).map(x=>({m:x.m, net:+x.net, tax:+x.tax})));

    // ドーナツ（右凡例・日本語ラベル）
    drawDonut("donut_broker","legend_broker", data.by_broker||[],  {key:"broker",  labels:LABELS.broker});
    drawDonut("donut_account","legend_account", data.by_account||[], {key:"account", labels:LABELS.account});

    // Top銘柄
    const top = data.top_symbols||[];
    const box = $("#tbl_top");
    if (box){
      box.innerHTML = top.length
        ? top.map(r=>`<div class="row"><span class="l">${r.label}</span><span class="r">${fmt(r.net)}</span></div>`).join("")
        : `<div class="muted">データなし</div>`;
    }
  }

  /* ------------ 操作系 ------------ */
  $("#flt_form")?.addEventListener("submit",(e)=>{ e.preventDefault(); fetchAndRender(); });
  ["#flt_year","#flt_broker","#flt_account"].forEach(sel=> $(sel)?.addEventListener("change", fetchAndRender));

  $("#goal_save_btn")?.addEventListener("click", async ()=>{
    const year = $("#flt_year").value;
    const amount = $("#goal_amount_input").value || "0";
    try{
      const resp = await fetch(URLS.save_goal, {
        method:"POST",
        headers:{ "Content-Type":"application/x-www-form-urlencoded", "X-Requested-With":"fetch" },
        body:`year=${q(year)}&amount=${q(amount)}`
      });
      if (!resp.ok) throw new Error();
      showToast("保存しました");
      fetchAndRender();
    }catch{ showToast("保存に失敗しました"); }
  });

  // 初期ロード（失敗時はサーバ描画のまま）
  fetchAndRender().catch(()=>{
    try{
      const el = document.getElementById("js-monthly");
      if (!el) return;
      const list = JSON.parse(el.textContent||"[]").map(x=>({m:x.m, net:+x.net, tax:+x.tax}));
      drawMonthly(list);
    }catch(_){}
  });
})();