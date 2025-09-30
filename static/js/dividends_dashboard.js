// dividends_dashboard.js – ダッシュボード非同期更新 + 進捗バー + ドーナツ + ドリルダウン
(function(){
  const $  = (s, r=document)=> r.querySelector(s);
  const $$ = (s, r=document)=> Array.from(r.querySelectorAll(s));
  const URLS = (window.DIVD_URLS||{});

  const COLORS = [
    "#6ea8ff","#9f7aea","#60d394","#f6bd60","#f28482",
    "#82caff","#c084fc","#7ad3a1","#ffd27f","#ff9aa2"
  ];

  const toast = $("#dashToast");
  function showToast(msg){
    if(!toast) return;
    toast.textContent = msg;
    toast.style.opacity = "1";
    toast.style.transform = "translate(-50%,0)";
    setTimeout(()=>{ toast.style.opacity="0"; toast.style.transform="translate(-50%,24px)"; }, 1400);
  }

  function fmt(n){ return Number(n||0).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2}); }
  function q(v){ return encodeURIComponent(v||""); }
  function drill(params){
    const u = new URL(URLS.list, location.origin);
    Object.entries(params).forEach(([k,v])=>{ if(v!==undefined && v!==null && v!=="") u.searchParams.set(k, v); });
    return u.toString();
  }

  /* ---------- 月次（棒） ---------- */
  function drawMonthly(list){
    const wrap = $("#monthly_svg"); if(!wrap) return;
    const W=360,H=160,pad=18,bw=18,gap=12;
    const max = Math.max(1, ...list.map(x=> (x.net + x.tax)));
    const sy = v => H - pad - (v/max)*(H - pad*2);
    const svg = document.createElementNS("http://www.w3.org/2000/svg","svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("width", "100%"); svg.setAttribute("height", "100%");
    // 軸線
    const axis = document.createElementNS("http://www.w3.org/2000/svg","path");
    axis.setAttribute("d", `M${pad},${H-pad}H${W-pad}`);
    axis.setAttribute("stroke","rgba(255,255,255,.25)"); axis.setAttribute("fill","none");
    svg.appendChild(axis);

    let x = pad;
    const tip  = $("#chartTip");
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
      // net
      const hNet = (H - pad) - sy(d.net);
      const r1 = document.createElementNS("http://www.w3.org/2000/svg","rect");
      r1.setAttribute("x", x); r1.setAttribute("y", sy(d.net));
      r1.setAttribute("width", bw); r1.setAttribute("height", hNet);
      r1.setAttribute("rx", 3); r1.setAttribute("fill", "#6ea8ff");
      r1.dataset.m = d.m; r1.dataset.net = d.net; r1.dataset.tax = d.tax;
      svg.appendChild(r1);

      // tax
      const hTax = (H - pad) - sy(d.net + d.tax) - hNet;
      const r2 = document.createElementNS("http://www.w3.org/2000/svg","rect");
      r2.setAttribute("x", x); r2.setAttribute("y", sy(d.net + d.tax));
      r2.setAttribute("width", bw); r2.setAttribute("height", hTax);
      r2.setAttribute("rx", 3); r2.setAttribute("fill", "#a0aec0");
      r2.dataset.m = d.m; r2.dataset.net = d.net; r2.dataset.tax = d.tax;
      svg.appendChild(r2);

      // label
      const t = document.createElementNS("http://www.w3.org/2000/svg","text");
      t.setAttribute("x", x + bw/2); t.setAttribute("y", H-4);
      t.setAttribute("text-anchor","middle"); t.setAttribute("font-size","9");
      t.setAttribute("fill","rgba(255,255,255,.75)"); t.textContent = d.m;
      svg.appendChild(t);

      x += bw + gap;
    });

    svg.addEventListener("mousemove", (e)=>{
      const el = e.target;
      if (el.tagName === "rect" && el.dataset.m){ showTip(e.clientX, e.clientY, el.dataset.m, +el.dataset.net, +el.dataset.tax); }
      else { hideTip(); }
    });
    svg.addEventListener("mouseleave", hideTip);
    svg.addEventListener("click", (e)=>{
      const el = e.target;
      if (el.tagName === "rect" && el.dataset.m){
        const year = $("#flt_year").value, broker=$("#flt_broker").value, account=$("#flt_account").value;
        location.href = drill({year, month: el.dataset.m, broker, account});
      }
    });
    svg.addEventListener("touchstart", (e)=>{
      const el = e.target; if(!(el && el.tagName==="rect" && el.dataset.m)) return;
      const t = e.touches[0]; showTip(t.clientX, t.clientY, el.dataset.m, +el.dataset.net, +el.dataset.tax);
    }, {passive:true});
    svg.addEventListener("touchend", ()=>{ hideTip(); }, {passive:true});

    wrap.replaceChildren(svg);
  }

  /* ---------- ドーナツ（SVG） ---------- */
  const NS = "http://www.w3.org/2000/svg";
  function polar(cx, cy, r, ang){ return [cx + r*Math.cos(ang), cy + r*Math.sin(ang)]; }

  function drawDonut(svgSel, legendSel, rows, labelKey, drillKey){
    const svg = $(svgSel), legend = $(legendSel);
    if (!svg || !legend) return;

    svg.replaceChildren(); // まず空っぽに

    // データなし
    const empty = (!rows || !rows.length || rows.every(x => Number(x.net||0) <= 0));
    if (empty){
      const ring = document.createElementNS(NS,"circle");
      ring.setAttribute("cx","60"); ring.setAttribute("cy","60"); ring.setAttribute("r","46");
      ring.setAttribute("fill","none"); ring.setAttribute("stroke","rgba(255,255,255,.12)"); ring.setAttribute("stroke-width","12");
      svg.appendChild(ring);
      const txt = document.createElementNS(NS,"text");
      txt.setAttribute("x","60"); txt.setAttribute("y","64"); txt.setAttribute("text-anchor","middle");
      txt.setAttribute("fill","rgba(255,255,255,.65)"); txt.setAttribute("font-size","10"); txt.textContent = "データなし";
      svg.appendChild(txt);
      legend.innerHTML = `<div class="muted">データなし</div>`;
      return;
    }

    const total = rows.reduce((s,x)=> s + Number(x.net||0), 0);
    const cx=60, cy=60, r=46, w=12;

    // 背景リング
    const bg = document.createElementNS(NS,"circle");
    bg.setAttribute("cx", cx); bg.setAttribute("cy", cy); bg.setAttribute("r", r);
    bg.setAttribute("fill","none"); bg.setAttribute("stroke","rgba(255,255,255,.12)"); bg.setAttribute("stroke-width", String(w));
    svg.appendChild(bg);

    let start = -Math.PI/2;
    rows.forEach((row, i)=>{
      const v = Number(row.net||0);
      const frac = v/Math.max(1,total);
      const ang = frac * Math.PI * 2;
      const end = start + ang;

      const minAng = Math.PI/180 * 2;
      const drawEnd = end - start < minAng ? start + minAng : end;

      const [sx,sy] = polar(cx,cy,r, start);
      const [ex,ey] = polar(cx,cy,r, drawEnd);
      const large = drawEnd - start > Math.PI ? 1 : 0;

      const path = document.createElementNS(NS,"path");
      path.setAttribute("d", `M ${sx} ${sy} A ${r} ${r} 0 ${large} 1 ${ex} ${ey}`);
      path.setAttribute("fill","none");
      path.setAttribute("stroke", COLORS[i % COLORS.length]);
      path.setAttribute("stroke-width", String(w));
      path.style.cursor = "pointer";
      path.addEventListener("click", ()=>{
        const year = $("#flt_year").value;
        const params = {year};
        params[drillKey] = row[labelKey] || row[drillKey];
        location.href = drill(params);
      });
      svg.appendChild(path);

      start = end;
    });

    // 中央テキスト（合計）
    const t1 = document.createElementNS(NS,"text");
    t1.setAttribute("x","60"); t1.setAttribute("y","57"); t1.setAttribute("text-anchor","middle");
    t1.setAttribute("fill","#cfd6ee"); t1.setAttribute("font-size","10"); t1.textContent = "合計";
    svg.appendChild(t1);
    const t2 = document.createElementNS(NS,"text");
    t2.setAttribute("x","60"); t2.setAttribute("y","72"); t2.setAttribute("text-anchor","middle");
    t2.setAttribute("fill","#fff"); t2.setAttribute("font-size","12"); t2.setAttribute("font-weight","700");
    t2.textContent = fmt(total);
    svg.appendChild(t2);

    // 凡例
    legend.innerHTML = rows.map((row,i)=>{
      const name = row[labelKey] || row[drillKey] || "—";
      const pct  = total > 0 ? ((Number(row.net||0)/total)*100).toFixed(1) : "0.0";
      return `<div class="row" data-idx="${i}">
        <div class="l"><i class="swatch" style="background:${COLORS[i%COLORS.length]}"></i><span>${name}</span></div>
        <div class="r"><span>${fmt(row.net||0)}</span><span class="muted" style="margin-left:8px">${pct}%</span></div>
      </div>`;
    }).join("");
    $$("#"+legend.id+" .row").forEach((el, idx)=>{
      el.style.cursor = "pointer";
      el.addEventListener("click", ()=>{
        const key = rows[idx][labelKey] || rows[idx][drillKey];
        const year = $("#flt_year").value;
        const params = {year};
        params[drillKey] = key;
        location.href = drill(params);
      });
    });
  }

  /* ---------- ランキング行 ---------- */
  function renderRows(containerSel, rows, key, drillKey){
    const box = $(containerSel); if (!box) return;
    if (!rows || !rows.length){ box.innerHTML = '<div class="muted">データなし</div>'; return; }
    box.innerHTML = rows.map(r=>{
      const v = r[key] ?? r[drillKey] ?? "—";
      return `<div class="row"><span class="l">${v}</span><span class="r">${fmt(r.net)}</span></div>`;
    }).join("");
  }

  /* ---------- 目標UI ---------- */
  let prevAchieved = false;
  function setGoalUI(goal){
    const amount = Number(goal?.amount || 0);
    const pct    = Math.max(0, Math.min(100, Number(goal?.progress_pct || 0)));
    const remain = Number(goal?.remaining || 0);

    $("#goal_amount_view").textContent   = fmt(amount);
    $("#goal_amount_input").value        = amount ? amount.toFixed(2) : "";
    $("#goal_progress_view").textContent = pct.toFixed(2) + "%";
    $("#goal_remaining_view").textContent= fmt(remain);
    $("#goal_bar_inner").style.width     = pct + "%";

    const card = $("#goal_card");
    const achieved = pct >= 100;
    card.classList.toggle("achieved", achieved);
    if (achieved && !prevAchieved){ showToast("🎉 目標を達成しました！"); try{ navigator.vibrate(20); }catch(_){ } }
    prevAchieved = achieved;
  }

  /* ---------- データ取得＆反映 ---------- */
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
    const monthly = (data.monthly||[]).map(x=>({m:x.m, net:+x.net, tax:+x.tax}));
    drawMonthly(monthly);

    // ドーナツ（ブローカー / 口座）
    drawDonut("#donut_broker",  "#legend_broker",  data.by_broker||[],  "broker",  "broker");
    drawDonut("#donut_account", "#legend_account", data.by_account||[], "account", "account");

    // ランキング
    renderRows("#tbl_top", data.top_symbols||[], "label", null);
  }

  // 反映ボタン & セレクト変更で即反映
  $("#flt_form")?.addEventListener("submit",(e)=>{ e.preventDefault(); fetchAndRender(); });
  ["#flt_year","#flt_broker","#flt_account"].forEach(sel=>{
    $(sel)?.addEventListener("change", ()=> fetchAndRender());
  });

  // 目標保存（Ajax）
  $("#goal_save_btn")?.addEventListener("click", async ()=>{
    const year = $("#flt_year").value;
    const amount = $("#goal_amount_input").value || "0";
    try{
      const resp = await fetch(URLS.save_goal, {
        method:"POST",
        headers:{ "Content-Type":"application/x-www-form-urlencoded", "X-Requested-With":"fetch" },
        body:`year=${q(year)}&amount=${q(amount)}`
      });
      if (!resp.ok) throw new Error("save failed");
      showToast("保存しました");
      fetchAndRender();
    }catch(_){ showToast("保存に失敗しました"); }
  });

  // 初期ロード（失敗時はサーバー描画 fallback）
  fetchAndRender().catch(()=> {
    try{
      const el = document.getElementById("js-monthly");
      if (!el) return;
      const list = JSON.parse(el.textContent||"[]").map(x=>({m:x.m, net:+x.net, tax:+x.tax}));
      drawMonthly(list);
    }catch(_){}
  });
})();