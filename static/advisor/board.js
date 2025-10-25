// static/advisor/board.js
const $ = (sel)=>document.querySelector(sel);

console.log("[board.js] v2025-10-25-4 loaded"); // 読み替わり確認ログ

// ---- トーストの安全な下マージンを計算（端末の下インセット＋固定オフセット）----
function computeToastBottomPx() {
  let insetBottom = 0;
  if (window.visualViewport) {
    const diff = window.innerHeight - window.visualViewport.height; // 下側の食い込み
    insetBottom = Math.max(0, Math.round(diff));
  }
  const px = insetBottom + 140; // ← 下タブを確実に避ける固定オフセット（必要なら調整）
  return px;
}

// ---- ユーティリティ：絶対URLを作る（相対パス問題を潰す）----
function abs(path){
  return new URL(path, window.location.origin).toString();
}

// ---- ユーティリティ：POST(JSON)（CSRF未使用の最小構成）----
async function postJSON(url, body){
  const res = await fetch(abs(url), {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify(body)
  });
  if(!res.ok){
    const txt = await res.text().catch(()=> "");
    throw new Error(`HTTP ${res.status} ${txt}`);
  }
  return await res.json();
}

(async function init(){
  // --- ボードデータ取得 ---
  const res = await fetch(abs("/advisor/api/board/"));
  const data = await res.json();

  // --- ヘッダー ---
  const d = new Date(data.meta.generated_at);
  const w = ["日","月","火","水","木","金","土"][d.getDay()];
  $("#dateLabel").textContent = `${d.getFullYear()}年${String(d.getMonth()+1).padStart(2,"0")}月${String(d.getDate()).padStart(2,"0")}日（${w}）`;
  const trendP = data.meta.regime.trend_prob;
  const trendText = trendP>=0.7? "相場：強め上向き" : trendP>=0.55? "相場：やや上向き" : trendP>=0.45? "相場：横ばい" : "相場：弱め";
  $("#trendBadge").textContent = `${trendText}（日経${data.meta.regime.nikkei} / TOPIX${data.meta.regime.topix}）`;
  $("#adherence").textContent = Math.round(data.meta.adherence_week*100) + "%";

  // --- テーマTOP3 ---
  const strip = $("#themeStrip");
  data.theme.top3.forEach(t=>{
    const dotClass = t.score>=0.7? 'dot-strong' : t.score>=0.5? 'dot-mid' : 'dot-weak';
    const span = document.createElement('span');
    span.className='theme-chip';
    span.innerHTML = `<i class="theme-dot ${dotClass}"></i>${t.label} ${Math.round(t.score*100)}点`;
    strip.appendChild(span);
  });

  // --- おすすめカード描画 ---
  const cards = $("#cards");
  const makeCard = (item, idx)=>{
    const themeScore = Math.round((item.theme?.score??0)*100);
    const themeLabel = item.theme?.label || "テーマ";
    const actionTone = /売|撤退/.test(item.action)? 'bad' : /様子見/.test(item.action)? 'warn' : 'good';
    const card = document.createElement('article');
    card.className='card'; card.dataset.idx = idx;
    card.innerHTML = `
      <span class="badge">#${idx+1}</span>
      <div class="title">${item.name} <span class="code">(${item.ticker})</span></div>
      <div class="segment">${item.segment}</div>
      <div class="action ${actionTone}">行動：${item.action}</div>
      <ul class="reasons">${item.reasons.map(r=>`<li>・${r}</li>`).join("")}</ul>
      <div class="targets">
        <div class="target">🎯 ${item.targets.tp}</div>
        <div class="target">🛑 ${item.targets.sl}</div>
      </div>
      <div class="ai-meter">
        <div class="meter-bar"><i style="width:${Math.max(8, Math.round((item.ai?.win_prob??0)*100))}%"></i></div>
        <div>AI信頼度：${"★★★★★☆☆☆☆☆".slice(5-Math.round((item.ai?.win_prob??0)*5),10-Math.round((item.ai?.win_prob??0)*5))}</div>
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

  // --- 並び替え（AI×テーマで再ソート） ---
  let sorted = false;
  $("#reorderBtn").addEventListener("click", (e)=>{
    sorted = !sorted;
    e.currentTarget.setAttribute("aria-pressed", String(sorted));
    e.currentTarget.textContent = sorted ? "🔀 優先度順に並び中" : "🔀 並び替え";
    const calc = it => (it.ai?.win_prob??0)*0.7 + (it.theme?.score??0)*0.3;
    const list = [...data.highlights].slice(0,5);
    list.sort((a,b)=> sorted ? calc(b)-calc(a) : 0);
    cards.innerHTML=''; list.forEach((it,i)=>cards.appendChild(makeCard(it,i)));
  });

  // --- ボタン（→ サーバ記録 & 2時間後リマインド） ---
  document.addEventListener("click", async (ev)=>{
    const btn = ev.target.closest("button.btn"); if(!btn) return;
    const card = btn.closest(".card"); const idx = Number(card?.dataset?.idx??0);
    const item = data.highlights[idx]; const act = btn.dataset.act;

    try{
      if(act === "save_order"){
        // ← 理由・テーマ・AI・TP/SL も同梱して保存
        await postJSON("/advisor/api/action/", {
          action: "save_order",
          ticker: item.ticker,
          policy_id: item.policy_id || "",
          note: "",
          name: item.name,
          reasons: item.reasons || [],
          theme: item.theme || {},          // {label, score}
          ai: item.ai || {},                // {win_prob}
          targets: item.targets || {},      // {tp, sl}
        });
        showToast(`${item.name}：記録しました`);
      }else if(act === "reject"){
        await postJSON("/advisor/api/action/", {
          action: "reject",
          ticker: item.ticker,
          policy_id: item.policy_id || "",
          note: ""
        });
        showToast(`${item.name}：記録しました`);
      }else if(act === "remind"){
        await postJSON("/advisor/api/remind/", {
          ticker: item.ticker, after_minutes: 120
        });
        showToast(`${item.name}：2時間後にお知らせします`);
      }
    }catch(e){
      console.error(e);
      showToast("通信に失敗しました");
    }
  });

  // ---- 修正版トースト：確実に下タブの上へ表示、フェード付き ----
  function showToast(msg){
    const t = document.createElement('div');
    // 競合を避けるため、位置系プロパティを明示
    t.style.position = 'fixed';
    t.style.top = 'auto';
    t.style.left = '50%';
    t.style.transform = 'translateX(-50%)';
    t.style.bottom = computeToastBottomPx() + 'px'; // ← 毎回計算して反映
    t.style.background = 'rgba(0,0,0,0.8)';
    t.style.color = '#fff';
    t.style.padding = '10px 16px';
    t.style.borderRadius = '14px';
    t.style.boxShadow = '0 6px 20px rgba(0,0,0,.4)';
    t.style.zIndex = '9999';
    t.style.opacity = '0';
    t.style.pointerEvents = 'none';
    t.style.transition = 'opacity 0.3s ease';

    t.textContent = msg;
    document.body.appendChild(t);

    // フェードイン
    requestAnimationFrame(()=> t.style.opacity = '1');

    // 回転/キーボード出現などで可変時も追従
    const onViewport = ()=> { t.style.bottom = computeToastBottomPx() + 'px'; };
    if (window.visualViewport) {
      window.visualViewport.addEventListener('resize', onViewport);
    }

    // 自動消去
    setTimeout(()=>{
      t.style.opacity = '0';
      setTimeout(()=>{
        if (window.visualViewport) {
          window.visualViewport.removeEventListener('resize', onViewport);
        }
        t.remove();
      }, 300);
    }, 2000);
  }
})();