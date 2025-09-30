// dividends_dashboard.js – ダッシュボード非同期更新 + 進捗バー + 達成トースト + ドリルダウン
(function(){
  const $  = (s, r=document)=> r.querySelector(s);
  const $$ = (s, r=document)=> Array.from(r.querySelectorAll(s));
  const URLS = (window.DIVD_URLS||{});

  function fmt(n){ return Number(n||0).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2}); }
  function q(v){ return encodeURIComponent(v||""); }

  // Toast
  const toast = $("#dashToast");
  function showToast(msg){
    if(!toast) return;
    toast.textContent = msg;
    toast.style.opacity = "1";
    toast.style.transform = "translate(-50%,0)";
    setTimeout(()=>{ toast.style.opacity="0"; toast.style.transform="translate(-50%,24px)"; }, 1400);
  }

  function drill(params){
    const u = new URL(URLS.list, location.origin);
    Object.entries(params).forEach(([k,v])=>{ if(v!==undefined && v!==null && v!=="") u.searchParams.set(k, v); });
    return u.toString();
  }

  // -------- 月次ミニ棒グラフ（税引後＋税額の積上げ） --------
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
      if (el.tagName === "rect" && el.dataset.m){
        showTip(e.clientX, e.clientY, el.dataset.m, +el.dataset.net, +el.dataset.tax);
      }else{
        hideTip();
      }
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

  // -------- リスト（表）描画 --------
  function renderRows(containerSel, rows, key, drillKey){
    const box = $(containerSel); if (!box) return;
    if (!rows || !rows.length){ box.innerHTML = '<div class="muted">—</div>'; return; }
    const year = $("#flt_year").value;
    box.innerHTML = rows.map(r=>{
      const v = r[key] ?? r[drillKey] ?? "—";
      let href = "#";
      if (drillKey){
        const params = {year};
        params[drillKey] = v;
        href = drill(params);
      }
      return `<a class="row" href="${href}"><span class="l">${v}</span><span class="r">${fmt(r.net)}</span></a>`;
    }).join("");
  }

  // ---- 目標UI反映 & 達成演出 ----
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

    // 初回以外で100%到達したらトースト
    if (achieved && !prevAchieved){
      showToast("🎉 目標を達成しました！");
      if (navigator.vibrate) { try{ navigator.vibrate(20); }catch(_){ } }
    }
    prevAchieved = achieved;
  }

  // -------- 取得＆反映 --------
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

    // 表
    renderRows("#tbl_broker",  data.by_broker,  "broker",  "broker");
    renderRows("#tbl_account", data.by_account, "account", "account");
    renderRows("#tbl_top",     data.top_symbols,"label",   null);
  }

  // 反映ボタン：Ajax置換
  const form = $("#flt_form");
  form?.addEventListener("submit",(e)=>{ e.preventDefault(); fetchAndRender(); });

  // 年/ブローカー/口座 変更で即反映（UX改善）
  ["#flt_year","#flt_broker","#flt_account"].forEach(sel=>{
    const el = $(sel);
    el?.addEventListener("change", ()=> fetchAndRender());
  });

  // 年間目標の保存：Ajax → 再取得
  const saveBtn = $("#goal_save_btn");
  saveBtn?.addEventListener("click", async ()=>{
    const year = $("#flt_year").value;
    const amount = $("#goal_amount_input").value || "0";
    try{
      const resp = await fetch(URLS.save_goal, {
        method:"POST",
        headers:{ "Content-Type":"application/x-www-form-urlencoded", "X-Requested-With":"fetch" },
        body:`year=${q(year)}&amount=${q(amount)}`
      });
      if (!resp.ok){ throw new Error("save failed"); }
      showToast("保存しました");
      fetchAndRender();
    }catch(_){
      showToast("保存に失敗しました");
    }
  });

  // 初期：JSONで最新化（失敗したらサーバ描画のまま）
  fetchAndRender().catch(()=> {
    try{
      const el = document.getElementById("js-monthly");
      if (!el) return;
      const list = JSON.parse(el.textContent||"[]").map(x=>({m:x.m, net:+x.net, tax:+x.tax}));
      drawMonthly(list);
    }catch(_){}
  });
})();