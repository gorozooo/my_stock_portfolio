const $ = (sel)=>document.querySelector(sel);

console.log("[board.js] v2025-10-28 r17 (LIVE/DEMO pill)");

function computeToastBottomPx() {
  let insetBottom = 0;
  if (window.visualViewport) {
    const diff = window.innerHeight - window.visualViewport.height;
    insetBottom = Math.max(0, Math.round(diff));
  }
  return insetBottom + 140; // 下タブ回避
}

function abs(path){ return new URL(path, window.location.origin).toString(); }

async function postJSON(url, body){
  const res = await fetch(abs(url), {
    method:"POST",
    headers:{ "Content-Type":"application/json" },
    body: JSON.stringify(body),
    credentials: "same-origin",
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
  if(code === "up") return {icon:"↗️", label:"上向き"};
  if(code === "down") return {icon:"↘️", label:"下向き"};
  return {icon:"➡️", label:"横ばい"};
}

function stars(prob01){
  const s = Math.round((prob01 ?? 0)*5);
  const f = "★★★★★".slice(0, Math.max(0, Math.min(5, s)));
  const e = "☆☆☆☆☆".slice(0, 5 - Math.max(0, Math.min(5, s)));
  return f + e;
}

(function init(){
  (async ()=>{
    // --- 取得 ---
    const res = await fetch(abs("/advisor/api/board/"), { credentials: "same-origin" });
    if (res.status === 401) {
      const next = encodeURIComponent(location.pathname + location.search + location.hash);
      location.href = `/accounts/login/?next=${next}`;
      return;
    }
    const data = await res.json();

    // --- ステータスピル（LIVE/DEMO） ---
    const pill = $("#statusPill");
    if (pill){
      const isLive = !!(data && data.meta && data.meta.live);
      const ver = (data && data.meta && data.meta.model_version) ? String(data.meta.model_version) : "";
      pill.textContent = isLive ? "LIVE" : "DEMO";
      pill.classList.remove("live", "demo");
      pill.classList.add(isLive ? "live" : "demo");
      // ちょい情報追加（モデルバージョン）
      if (ver) {
        const v = document.createElement("i");
        v.textContent = ` ${ver}`;
        pill.appendChild(v);
      }
    }

    // --- ヘッダー（日付/相場/テーマ/再現率） ---
    const d = new Date(data.meta.generated_at);
    const w = ["日","月","火","水","木","金","土"][d.getDay()];
    const dateLabel = $("#dateLabel");
    if (dateLabel) dateLabel.textContent = `${d.getFullYear()}年${String(d.getMonth()+1).padStart(2,"0")}月${String(d.getDate()).padStart(2,"0")}日（${w}）`;

    const trendP = data.meta.regime.trend_prob;
    const trendText = trendP>=0.7? "相場：強め上向き" : trendP>=0.55? "相場：やや上向き" : trendP>=0.45? "相場：横ばい" : "相場：弱め";
    const trendBadge = $("#trendBadge");
    if (trendBadge) trendBadge.textContent = `${trendText}（日経${data.meta.regime.nikkei} / TOPIX${data.meta.regime.topix}）`;
    const adherence = $("#adherence");
    if (adherence) adherence.textContent = Math.round(data.meta.adherence_week*100) + "%";

    const strip = $("#themeStrip");
    if (strip) {
      strip.innerHTML = "";
      if (data.meta.scenario){
        const s1 = document.createElement("span");
        s1.className = "scenario-chip";
        s1.textContent = data.meta.scenario;
        strip.appendChild(s1);
      }
      (data.theme.top3 || []).forEach(t=>{
        const dotClass = t.score>=0.7? 'dot-strong' : t.score>=0.5? 'dot-mid' : 'dot-weak';
        const span = document.createElement('span');
        span.className='theme-chip';
        span.innerHTML = `<i class="theme-dot ${dotClass}"></i>${t.label} ${Math.round(t.score*100)}点`;
        strip.appendChild(span);
      });
    }

    // --- カード描画 ---
    const cards = $("#cards");
    if (!cards) return;
    cards.innerHTML = "";

    const makeCard = (item, idx)=>{
      const themeScore = Math.round((item.theme?.score??0)*100);
      const themeLabel = item.theme?.label || "テーマ";
      const actionTone = /売|撤退|縮小/.test(item.action)? 'bad' : /様子見/.test(item.action)? 'warn' : 'good';

      const wk = weeklyIconLabel(item.weekly_trend);
      const overall = item.overall_score ?? Math.round(((item.ai?.win_prob??0)*0.7 + (item.theme?.score??0)*0.3)*100);
      const aiProb = Number(item?.ai?.win_prob ?? 0);
      const aiStars = stars(aiProb);

      const tpPct = Math.round((item.targets?.tp_pct ?? 0) * 100);
      const slPct = Math.round((item.targets?.sl_pct ?? 0) * 100);
      const tpPrice = item.targets?.tp_price;
      const slPrice = item.targets?.sl_price;
      const entry = item.entry_price_hint;
      const sizeHint = item.sizing?.position_size_hint;
      const needCash = item.sizing?.need_cash;

      const tpProb = Math.round((item.ai?.tp_prob ?? 0) * 100);
      const slProb = Math.round((item.ai?.sl_prob ?? 0) * 100);

      const card = document.createElement('article');
      card.className='card';
      card.dataset.idx = idx;

      card.innerHTML = `
        <span class="badge">#${idx+1}</span>

        <div class="title">${item.name} <span class="code">(${item.ticker})</span></div>
        <div class="segment">${item.segment}・週足：${wk.icon} ${wk.label}</div>

        <div class="overall">
          <span class="overall-score">総合評価 <b>${overall}</b> 点</span>
          <span class="ai-trust">AI信頼度：${aiStars}</span>
        </div>

        <div class="action ${actionTone}">行動：${item.action}</div>

        <ul class="reasons">${(item.reasons||[]).map(r=>`<li>・${r}</li>`).join("")}</ul>

        <div class="targets">
          <div class="target">🎯 目標 ${tpPct}% → <b>${tpPrice?.toLocaleString?.() ?? "-"}</b>円</div>
          <div class="target">🛑 損切 ${slPct}% → <b>${slPrice?.toLocaleString?.() ?? "-"}</b>円</div>
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
    };

    data.highlights.slice(0,5).forEach((it,i)=>cards.appendChild(makeCard(it,i)));

    // 並び替え（総合評価で再ソート）
    let sorted = false;
    const reorderBtn = $("#reorderBtn");
    if (reorderBtn){
      reorderBtn.addEventListener("click", (e)=>{
        sorted = !sorted;
        e.currentTarget.setAttribute("aria-pressed", String(sorted));
        e.currentTarget.textContent = sorted ? "🔀 優先度順に並び中" : "🔀 並び替え";
        const calc = it => (it.overall_score ?? ((it.ai?.win_prob??0)*0.7 + (it.theme?.score??0)*0.3)*100);
        const list = [...data.highlights].slice(0,5);
        list.sort((a,b)=> sorted ? calc(b)-calc(a) : 0);
        const cards = $("#cards");
        cards.innerHTML=''; list.forEach((it,i)=>cards.appendChild(makeCard(it,i)));
      });
    }

    // クリック → 記録/リマインド
    document.addEventListener("click", async (ev)=>{
      const btn = ev.target.closest("button.btn"); if(!btn) return;
      const card = btn.closest(".card"); const idx = Number(card?.dataset?.idx ?? 0);
      const item = data.highlights[idx]; const act = btn.dataset.act;

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
          await postJSON("/advisor/api/action/", payload);
          showToast(`${item.name}：記録しました`);
        }else if(act === "remind"){
          await postJSON("/advisor/api/remind/", { ticker: item.ticker, after_minutes: 120 });
          showToast(`${item.name}：2時間後にお知らせします`);
        }
      }catch(e){
        console.error(e);
        const msg = (e && e.message) ? e.message : "通信に失敗しました";
        showToast(`通信エラー: ${msg}`);
      }
    });

    // トースト
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
  })().catch(console.error);
})();