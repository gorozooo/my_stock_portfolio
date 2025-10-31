// v2025-10-29 r21 — force-refresh, cache-bust, robust, data-attr endpoints

const $  = (sel) => document.querySelector(sel);

console.log("[board.js] v2025-10-29 r21 (force-refresh, cache-bust, robust, data-attr)");

function abs(path){ return new URL(path, window.location.origin).toString(); }

function computeToastBottomPx() {
  let insetBottom = 0;
  if (window.visualViewport) {
    const diff = window.innerHeight - window.visualViewport.height;
    insetBottom = Math.max(0, Math.round(diff));
  }
  return insetBottom + 140; // 下タブ回避
}

async function postJSON(url, body){
  const res = await fetch(abs(url), {
    method:"POST",
    headers:{ "Content-Type":"application/json" },
    body: JSON.stringify(body),
    credentials: "same-origin",
    cache: "no-store",
  });
  if (res.status === 401) {
    const next = encodeURIComponent(location.pathname + location.search + location.hash);
    location.href = `/accounts/login/?next=${next}`;
    throw new Error("auth_required");
  }
  if(!res.ok){
    const txt = await res.text().catch(()=> "");
    throw new Error(`HTTP ${res.status} ${txt}`);
  }
  return await res.json();
}

function weeklyIconLabel(code){
  const c = String(code || "flat").toLowerCase();
  if(c === "up")   return {icon:"↗️", label:"上向き"};
  if(c === "down") return {icon:"↘️", label:"下向き"};
  return {icon:"➡️", label:"横ばい"};
}

function stars(prob01){
  const s = Math.round((Number(prob01) || 0)*5);
  const f = "★★★★★".slice(0, Math.max(0, Math.min(5, s)));
  const e = "☆☆☆☆☆".slice(0, 5 - Math.max(0, Math.min(5, s)));
  return f + e;
}

function setStatusPill(data){
  const pill = $("#statusPill");
  if (!pill) return;
  const live = !!(data?.meta?.live);
  pill.textContent = live ? "LIVE" : "DEMO";
  pill.classList.remove("live","demo");
  pill.classList.add(live ? "live":"demo");
}

function setHeader(data){
  const meta = data?.meta || {};
  const theme = data?.theme || {};
  const d = new Date(meta.generated_at || Date.now());
  const w = ["日","月","火","水","木","金","土"][d.getDay()];
  const dateLabel = $("#dateLabel");
  if (dateLabel) dateLabel.textContent =
    `${d.getFullYear()}年${String(d.getMonth()+1).padStart(2,"0")}月${String(d.getDate()).padStart(2,"0")}日（${w}）`;

  const regime = meta.regime || {trend_prob:0.5, nikkei:"→", topix:"→"};
  const trendP = Number(regime.trend_prob) || 0.5;
  const trendText =
    trendP>=0.7 ? "相場：強め上向き" :
    trendP>=0.55? "相場：やや上向き" :
    trendP>=0.45? "相場：横ばい" :
                  "相場：弱め";
  const trendBadge = $("#trendBadge");
  if (trendBadge) trendBadge.textContent = `${trendText}（日経${regime.nikkei ?? "→"} / TOPIX${regime.topix ?? "→"}）`;

  const adherence = $("#adherence");
  if (adherence) adherence.textContent = Math.round((Number(meta.adherence_week)||0)*100) + "%";

  const strip = $("#themeStrip");
  if (strip) {
    strip.innerHTML = "";
    if (meta.scenario){
      const s1 = document.createElement("span");
      s1.className = "scenario-chip";
      s1.textContent = meta.scenario;
      strip.appendChild(s1);
    }
    (theme.top3 || []).forEach(t=>{
      const sc = Number(t?.score) || 0;
      const dotClass = sc>=0.7? 'dot-strong' : sc>=0.5? 'dot-mid' : 'dot-weak';
      const span = document.createElement('span');
      span.className='theme-chip';
      span.innerHTML = `<i class="theme-dot ${dotClass}"></i>${t.label ?? "テーマ"} ${Math.round(sc*100)}点`;
      strip.appendChild(span);
    });
  }
}

function escapeHtml(s){
  return String(s).replace(/[&<>"']/g, m=>({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[m]));
}

function renderBadges(meta){
  if(!meta) return '';
  const out = [];
  if(meta.sector){ out.push(`<span class="badge-mini sector"><i class="dot"></i>${escapeHtml(meta.sector)}</span>`); }
  if(meta.market){ out.push(`<span class="badge-mini market"><i class="dot"></i>${escapeHtml(meta.market)}</span>`); }
  return out.length ? `<div class="badges">${out.join('')}</div>` : '';
}

// 置き換え：makeCard（⏱バッジ＋トレーリング表示）
function makeCard(item, idx){
  const themeScore = Math.round(((item?.theme?.score) ?? 0)*100);
  const themeLabel = item?.theme?.label || "テーマ";
  const actionTone = /売|撤退|縮小/.test(item?.action ?? "") ? 'bad'
                     : /様子見/.test(item?.action ?? "") ? 'warn' : 'good';
  const wk = weeklyIconLabel(item?.weekly_trend);
  const overall = Number(item?.overall_score) || Math.round(
    (((item?.ai?.win_prob??0)*0.7) + ((item?.theme?.score??0)*0.3))*100
  );
  const aiProb = Number(item?.ai?.win_prob ?? 0);
  const aiStars = stars(aiProb);
  const tpPct   = Math.round(((item?.targets?.tp_pct) ?? 0) * 100);
  const slPct   = Math.round(((item?.targets?.sl_pct) ?? 0) * 100);
  const tpPrice = item?.targets?.tp_price;
  const slPrice = item?.targets?.sl_price;
  const entry   = item?.entry_price_hint;
  const sizeHint= item?.sizing?.position_size_hint;
  const needCash= item?.sizing?.need_cash;
  const tpProb  = Math.round(((item?.ai?.tp_prob) ?? 0) * 100);
  const slProb  = Math.round(((item?.ai?.sl_prob) ?? 0) * 100);

  const timeDue = !!(item?.targets?.time_exit_due);                 // ★ 追加：時間切れ
  const trailMult = item?.targets?.trail_atr_mult ?? null;          // ★ 追加：トレーリングATR倍率

  const card = document.createElement('article');
  card.className='card';
  card.dataset.idx = idx;

  const safeName = (item?.name || item?.ticker || "").toString();

  // ★ 右上バッジを2段に（#順位 / ⏱time-out）
  const badge2 = `
    <span class="badge">#${idx+1}</span>
    ${timeDue ? `<span class="badge timeout" title="時間切れルールに達しました">⏱ TIME-OUT</span>` : ``}
  `;

  // ★ トレーリング注記（あれば表示）
  const trailNote = trailMult ? `<div class="target subtle">📈 トレーリング ${trailMult}×ATR（目安）</div>` : ``;

  card.innerHTML = `
    ${badge2}
    <div class="title">${safeName} <span class="code">(${item?.ticker ?? "-"})</span></div>
    <div class="segment">${item?.segment ?? ""}・週足：${wk.icon} ${wk.label}</div>

    <div class="overall">
      <span class="overall-score">総合評価 <b>${overall}</b> 点</span>
      <span class="ai-trust">AI信頼度：${aiStars}</span>
    </div>

    <div class="action ${actionTone}">行動：${item?.action ?? ""}</div>

    <ul class="reasons">${(item?.reasons||[]).map(r=>`<li>・${r}</li>`).join("")}</ul>

    <div class="targets">
      <div class="target">🎯 目標 ${isFinite(tpPct)? tpPct : "?"}% → <b>${tpPrice?.toLocaleString?.() ?? "-"}</b>円</div>
      <div class="target">🛑 損切 ${isFinite(slPct)? slPct : "?"}% → <b>${slPrice?.toLocaleString?.() ?? "-"}</b>円</div>
      ${trailNote}
    </div>

    <div class="entry-size">
      <div>IN目安：<b>${entry?.toLocaleString?.() ?? "-"}</b>円</div>
      ${sizeHint ? `<div>数量目安：<b>${sizeHint}</b> 株（必要資金 ${needCash?.toLocaleString?.() ?? "-"}円）</div>` : ""}
    </div>

    <div class="meter-wrap">
      <div class="meter-bar"><i style="width:${Math.max(8, Math.round(aiProb*100))}%"></i></div>
      <div class="meter-caption">TP到達:${tpProb}% / SL到達:${slProb}%</div>
    </div>

    <div class="theme-tag">🏷️ ${themeLabel} ${themeScore}点</div>

    <div class="buttons" role="group" aria-label="アクション">
      <button class="btn primary" data-act="save_order">📝 メモする</button>
      <button class="btn" data-act="remind">⏰ 2時間後に見る</button>
      <button class="btn danger" data-act="reject">❌ 見送り</button>
    </div>`;
  return card;
}

function renderCards(data){
  const cards = $("#cards"); if (!cards) return;
  cards.innerHTML = "";
  (data?.highlights || []).slice(0,5).forEach((it,i)=>cards.appendChild(makeCard(it,i)));
}

function attachActions(data, endpoints){
  // 並び替え
  let sorted = false;
  const reorderBtn = $("#reorderBtn");
  if (reorderBtn){
    reorderBtn.addEventListener("click", (e)=>{
      sorted = !sorted;
      e.currentTarget.setAttribute("aria-pressed", String(sorted));
      e.currentTarget.textContent = sorted ? "🔀 優先度順に並び中" : "🔀 並び替え";
      const calc = it => (Number(it?.overall_score) ||
        Math.round((((it?.ai?.win_prob??0)*0.7 + (it?.theme?.score??0)*0.3)*100)));
      const list = [...(data?.highlights || [])].slice(0,5);
      list.sort((a,b)=> sorted ? calc(b)-calc(a) : 0);
      const cards = $("#cards");
      cards.innerHTML=''; list.forEach((it,i)=>cards.appendChild(makeCard(it,i)));
    });
  }

  // 保存・通知
  document.addEventListener("click", async (ev)=>{
    const btn = ev.target.closest("button.btn"); if(!btn) return;
    const card = btn.closest(".card"); const idx = Number(card?.dataset?.idx ?? 0);
    const item = (data?.highlights || [])[idx]; if(!item) return;
    const act = btn.dataset.act;

    try{
      if(act === "save_order" || act === "reject"){
        const payload = {
          action: act,
          ticker: item.ticker,
          policy_id: "",
          note: "",
          name: item.name,
          reason_summary: (item.reasons||[]).join(" / "),
          reason_details: item.reasons || [],
          theme_label: item.theme?.label || "",
          theme_score: item.theme?.score ?? null,
          ai_win_prob: item.ai?.win_prob ?? null,
          target_tp: `+${Math.round((item.targets?.tp_pct ?? 0)*100)}% → ${item.targets?.tp_price ?? ""}円`,
          target_sl: `-${Math.round((item.targets?.sl_pct ?? 0)*100)}% → ${item.targets?.sl_price ?? ""}円`,
          overall_score: item.overall_score ?? null,
          weekly_trend: item.weekly_trend || "",
          entry_price_hint: item.entry_price_hint ?? null,
          tp_price: item.targets?.tp_price ?? null,
          sl_price: item.targets?.sl_price ?? null,
          tp_pct: item.targets?.tp_pct ?? null,
          sl_pct: item.targets?.sl_pct ?? null,
          position_size_hint: item.sizing?.position_size_hint ?? null,
        };
        await postJSON(endpoints.action, payload);
        showToast(`${item.name}：記録しました`);
      }else if(act === "remind"){
        await postJSON(endpoints.remind, { ticker: item.ticker, after_minutes: 120 });
        showToast(`${item.name}：2時間後にお知らせします`);
      }
    }catch(e){
      console.error(e);
      const msg = (e && e.message) ? e.message : "通信に失敗しました";
      showToast(`通信エラー: ${msg}`);
    }
  });
}

async function fetchBoard({force=false} = {}, endpoints){
  // cache-bust クエリ & no-store
  const url = `${endpoints.board}?${force ? "force=1&" : ""}_t=${Date.now()}`;
  const res = await fetch(url, { credentials: "same-origin", cache: "no-store" });

  if (res.status === 401) {
    const next = encodeURIComponent(location.pathname + location.search + location.hash);
    location.href = `/accounts/login/?next=${next}`;
    return null;
  }

  if(!res.ok){
    const txt = await res.text().catch(()=> "");
    throw new Error(`HTTP ${res.status} ${txt}`);
  }
  const data = await res.json();
  if(!data || !data.highlights){ throw new Error("invalid board payload"); }
  return data;
}

function showToast(msg){
  const t = document.createElement('div');
  t.style.position='fixed';
  t.style.top='auto';
  t.style.left='50%';
  t.style.transform='translateX(-50%)';
  t.style.bottom = computeToastBottomPx() + 'px';
  t.style.background='rgba(0,0,0,0.8)';
  t.style.color='#fff';
  t.style.padding='10px 16px';
  t.style.borderRadius='14px';
  t.style.boxShadow='0 6px 20px rgba(0,0,0,.4)';
  t.style.zIndex='9999';
  t.style.opacity='0';
  t.style.pointerEvents='none';
  t.style.transition='opacity 0.3s ease';
  t.textContent = msg;
  document.body.appendChild(t);
  requestAnimationFrame(()=> t.style.opacity = '1');
  const onViewport = ()=> { t.style.bottom = computeToastBottomPx() + 'px'; };
  if (window.visualViewport) window.visualViewport.addEventListener('resize', onViewport);
  setTimeout(()=>{
    t.style.opacity = '0';
    setTimeout(()=>{
      if (window.visualViewport) window.visualViewport.removeEventListener('resize', onViewport);
      t.remove();
    }, 300);
  }, 2000);
}

async function boot(force=false){
  const root = $("#advisorRoot");
  const endpoints = {
    board : root?.dataset?.apiBoard  || "/advisor/api/board/",
    action: root?.dataset?.apiAction || "/advisor/api/action/",
    remind: root?.dataset?.apiRemind || "/advisor/api/remind/",
  };
  try{
    const data = await fetchBoard({force}, endpoints);
    setStatusPill(data);
    setHeader(data);
    renderCards(data);
    attachActions(data, endpoints);
  }catch(e){
    console.error(e);
    showToast("ボードの取得に失敗しました");
  }
}

(function init(){
  // 初期ロード
  boot(false);
  // 強制リフレッシュ
  const refreshBtn = $("#refreshBtn");
  if (refreshBtn){
    refreshBtn.addEventListener("click", async ()=>{
      refreshBtn.disabled = true;
      refreshBtn.querySelector(".label")?.replaceChildren(document.createTextNode("更新中…"));
      try{
        await boot(true); // force=1 で再取得
        showToast("最新データに更新しました");
      }catch(e){
        console.error(e);
        showToast("更新に失敗しました");
      }finally{
        refreshBtn.disabled = false;
        refreshBtn.querySelector(".label")?.replaceChildren(document.createTextNode("更新"));
      }
    });
  }
})();