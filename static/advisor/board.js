// static/advisor/board.js
const $ = (sel)=>document.querySelector(sel);

console.log("[board.js] v2025-10-26-2 loaded (save_order with full payload + 401 handling)");

// ---- トースト下マージン（下タブ/ホームバー回避）----
function computeToastBottomPx() {
  let insetBottom = 0;
  if (window.visualViewport) {
    const diff = window.innerHeight - window.visualViewport.height;
    insetBottom = Math.max(0, Math.round(diff));
  }
  return insetBottom + 140;
}

// ---- 便利関数 ----
function abs(path){ return new URL(path, window.location.origin).toString(); }

async function postJSON(url, body){
  const res = await fetch(abs(url), {
    method:"POST",
    headers:{ "Content-Type":"application/json" },
    body: JSON.stringify(body)
  });
  let payloadText = "";
  try { payloadText = await res.text(); } catch(_){}
  if(!res.ok){
    const err = new Error(`HTTP ${res.status}`);
    err.status = res.status;
    err.body = payloadText;
    throw err;
  }
  return payloadText ? JSON.parse(payloadText) : {};
}

// ---- 表示用：週足の方向（📈/➡️/📉）を推定（デモ用フォールバックあり）----
function guessWeekTrend(item){
  const raw = item?.weekly_trend || item?.ta?.week_trend; // server優先
  if (raw === "up")   return {label:"上向き", icon:"↗️", cls:"wk-up", code:"up"};
  if (raw === "flat") return {label:"横ばい", icon:"➡️", cls:"wk-flat", code:"flat"};
  if (raw === "down") return {label:"下向き", icon:"↘️", cls:"wk-down", code:"down"};

  const a = (item?.action || "");
  if (/買い候補|上向き|上昇|押し目/.test(a)) return {label:"上向き", icon:"↗️", cls:"wk-up", code:"up"};
  if (/様子見|横ばい|レンジ/.test(a))       return {label:"横ばい", icon:"➡️", cls:"wk-flat", code:"flat"};
  if (/売|撤退|下向き|下落/.test(a))         return {label:"下向き", icon:"↘️", cls:"wk-down", code:"down"};

  const p = Number(item?.ai?.win_prob ?? 0);
  if (p >= 0.62) return {label:"上向き", icon:"↗️", cls:"wk-up", code:"up"};
  if (p <= 0.45) return {label:"下向き", icon:"↘️", cls:"wk-down", code:"down"};
  return {label:"横ばい", icon:"➡️", cls:"wk-flat", code:"flat"};
}

// ---- 表示用：総合評価（0-100点）----
function calcOverallScore(item){
  const s1 = Number(item?.overall_score);
  if (!Number.isNaN(s1) && s1 > 0) return Math.round(Math.max(0, Math.min(100, s1)));
  const s2 = Number(item?.scores?.overall || item?.ai?.overall_score);
  if (!Number.isNaN(s2) && s2 > 0) return Math.round(Math.max(0, Math.min(100, s2)));

  const p = Number(item?.ai?.win_prob ?? 0);
  const t = Number(item?.theme?.score ?? 0);
  const m = (/勢い|出来高|戻り|強い/.test(item?.action || "") ? 0.7 : 0.5);
  const score01 = 0.6*p + 0.3*t + 0.1*m;
  return Math.round(Math.max(0, Math.min(100, score01*100)));
}

function starsFromProb(prob01){
  const s = Math.round((prob01 ?? 0)*5);
  const filled = "★★★★★".slice(0, Math.max(0, Math.min(5, s)));
  const empty  = "☆☆☆☆☆".slice(0, 5 - Math.max(0, Math.min(5, s)));
  return filled + empty;
}

// ---- トースト ----
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

(function init(){
  (async ()=>{
    // --- データ取得 ---
    const res = await fetch(abs("/advisor/api/board/"));
    const data = await res.json();

    // --- ヘッダー（存在すれば） ---
    const d = new Date(data.meta.generated_at);
    const w = ["日","月","火","水","木","金","土"][d.getDay()];
    $("#dateLabel") && ($("#dateLabel").textContent =
      `${d.getFullYear()}年${String(d.getMonth()+1).padStart(2,"0")}月${String(d.getDate()).padStart(2,"0")}日（${w}）`);
    const trendP = data.meta.regime.trend_prob;
    const trendText = trendP>=0.7? "相場：強め上向き" : trendP>=0.55? "相場：やや上向き" : trendP>=0.45? "相場：横ばい" : "相場：弱め";
    $("#trendBadge") && ($("#trendBadge").textContent =
      `${trendText}（日経${data.meta.regime.nikkei} / TOPIX${data.meta.regime.topix}）`);
    $("#adherence") && ($("#adherence").textContent = Math.round(data.meta.adherence_week*100) + "%");

    const strip = $("#themeStrip");
    if (strip) {
      data.theme.top3.forEach(t=>{
        const dotClass = t.score>=0.7? 'dot-strong' : t.score>=0.5? 'dot-mid' : 'dot-weak';
        const span = document.createElement('span');
        span.className='theme-chip';
        span.innerHTML = `<i class="theme-dot ${dotClass}"></i>${t.label} ${Math.round(t.score*100)}点`;
        strip.appendChild(span);
      });
    }

    // --- おすすめカード ---
    const cards = $("#cards");
    if (!cards) return;

    const makeCard = (item, idx)=>{
      const themeScore = Math.round((item.theme?.score??0)*100);
      const themeLabel = item.theme?.label || "テーマ";
      const actionTone = /売|撤退/.test(item.action)? 'bad' : /様子見/.test(item.action)? 'warn' : 'good';

      const wk = guessWeekTrend(item);
      const overall = calcOverallScore(item);
      const aiProb = Number(item?.ai?.win_prob ?? 0);
      const aiStars = starsFromProb(aiProb);

      const card = document.createElement('article');
      card.className='card';
      card.dataset.idx = idx;

      card.innerHTML = `
        <span class="badge">#${idx+1}</span>

        <div class="title">${item.name} <span class="code">(${item.ticker})</span></div>
        <div class="segment">${item.segment}</div>

        <div class="meta-row">
          <span class="chip ${wk.cls}">週足：${wk.icon} ${wk.label}</span>
          <span class="chip theme-chip-compact">#${themeLabel} ${themeScore}点</span>
        </div>

        <div class="action ${actionTone}">行動：${item.action}</div>

        <ul class="reasons">
          ${item.reasons.map(r=>`<li>・${r}</li>`).join("")}
        </ul>

        <div class="targets">
          <div class="target">🎯 ${item.targets?.tp ?? "-"}</div>
          <div class="target">🛑 ${item.targets?.sl ?? "-"}</div>
        </div>

        <div class="overall-block">
          <div class="overall">総合評価：<strong>${overall}点</strong></div>
          <div class="ai-confidence">AI信頼度：${aiStars}</div>
        </div>

        <div class="buttons" role="group" aria-label="アクション">
          <button class="btn primary" data-act="save_order">📝 メモする</button>
          <button class="btn" data-act="remind">⏰ 2時間後に見る</button>
          <button class="btn danger" data-act="reject">❌ 見送り</button>
        </div>`;
      return card;
    };

    data.highlights.slice(0,5).forEach((it,i)=>cards.appendChild(makeCard(it,i)));

    // --- 並び替え（AI×テーマ） ---
    let sorted = false;
    const reorderBtn = $("#reorderBtn");
    if (reorderBtn){
      reorderBtn.addEventListener("click", (e)=>{
        sorted = !sorted;
        e.currentTarget.setAttribute("aria-pressed", String(sorted));
        e.currentTarget.textContent = sorted ? "🔀 優先度順に並び中" : "🔀 並び替え";
        const calc = it => (it.ai?.win_prob??0)*0.7 + (it.theme?.score??0)*0.3;
        const list = [...data.highlights].slice(0,5);
        list.sort((a,b)=> sorted ? calc(b)-calc(a) : 0);
        cards.innerHTML=''; list.forEach((it,i)=>cards.appendChild(makeCard(it,i)));
      });
    }

    // --- クリックアクション → サーバ記録＆リマインド ---
    document.addEventListener("click", async (ev)=>{
      const btn = ev.target.closest("button.btn"); if(!btn) return;
      const card = btn.closest(".card"); const idx = Number(card?.dataset?.idx??0);
      const item = data.highlights[idx]; const act = btn.dataset.act;

      // 保存時に Board の情報を**そのまま**投げる
      const wk = guessWeekTrend(item);
      const payload = {
        action: act,
        ticker: item.ticker,
        policy_id: item.policy_id || "",
        name: item.name || "",
        note: "",
        // 理由（要望通りBoardと同一テキスト/配列）
        reason_summary: (item.reasons || []).join(" / "),
        reason_details: item.reasons || [],
        // テーマ/AI
        theme_label: item.theme?.label || "",
        theme_score: item.theme?.score,
        ai_win_prob: item.ai?.win_prob,
        // ターゲット（テキスト）
        target_tp: item.targets?.tp || "",
        target_sl: item.targets?.sl || "",
        // 追加数値（Board拡張）
        overall_score: item.overall_score ?? calcOverallScore(item),
        weekly_trend: item.weekly_trend || wk.code,           // "up|flat|down"
        entry_price_hint: item.entry_price_hint ?? null,
        tp_price: item.targets?.tp_price ?? null,
        sl_price: item.targets?.sl_price ?? null,
        tp_pct: item.targets?.tp_pct ?? null,
        sl_pct: item.targets?.sl_pct ?? null,
        position_size_hint: item.sizing?.position_size_hint ?? null
      };

      try{
        if(act === "save_order" || act === "reject"){
          await postJSON("/advisor/api/action/", payload);
          showToast(`${item.name}：記録しました`);
        }else if(act === "remind"){
          await postJSON("/advisor/api/remind/", { ticker: item.ticker, after_minutes: 120 });
          showToast(`${item.name}：2時間後にお知らせします`);
        }
      }catch(e){
        console.error("[board.save]", e.status, e.body);

        if (e.status === 401) {
          showToast("ログインが必要です");
          // 必要ならログインに誘導（コメントアウトで保持）
          // setTimeout(()=> location.href = "/accounts/login/?next=" + encodeURIComponent(location.pathname), 600);
        } else {
          showToast("通信に失敗しました");
        }
      }
    });
  })().catch(console.error);
})();