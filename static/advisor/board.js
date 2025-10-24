const $ = (sel)=>document.querySelector(sel);

// ---- 追加：トーストの安全な下マージンを計算（端末の下インセット＋固定オフセット）----
function computeToastBottomPx() {
  // iOS/Safari では visualViewport で安全領域差分が取れることが多い
  let insetBottom = 0;
  if (window.visualViewport) {
    // 画面全体の高さとの差分 ≒ 下側の安全領域（ノッチ/ホームバー）やUIの食い込み
    const diff = window.innerHeight - window.visualViewport.height;
    insetBottom = Math.max(0, Math.round(diff));
  }
  // 下タブに被らないよう固定で +96px（必要ならここを調整）
  return insetBottom + 120;
}

(async function init(){
  const res = await fetch("/advisor/api/board/");
  const data = await res.json();

  // ヘッダー
  const d = new Date(data.meta.generated_at);
  const w = ["日","月","火","水","木","金","土"][d.getDay()];
  $("#dateLabel").textContent = `${d.getFullYear()}年${String(d.getMonth()+1).padStart(2,"0")}月${String(d.getDate()).padStart(2,"0")}日（${w}）`;
  const trendP = data.meta.regime.trend_prob;
  const trendText = trendP>=0.7? "相場：強め上向き" : trendP>=0.55? "相場：やや上向き" : trendP>=0.45? "相場：横ばい" : "相場：弱め";
  $("#trendBadge").textContent = `${trendText}（日経${data.meta.regime.nikkei} / TOPIX${data.meta.regime.topix}）`;
  $("#adherence").textContent = Math.round(data.meta.adherence_week*100) + "%";

  // テーマTOP3
  const strip = $("#themeStrip");
  data.theme.top3.forEach(t=>{
    const dotClass = t.score>=0.7? 'dot-strong' : t.score>=0.5? 'dot-mid' : 'dot-weak';
    const span = document.createElement('span');
    span.className='theme-chip';
    span.innerHTML = `<i class="theme-dot ${dotClass}"></i>${t.label} ${Math.round(t.score*100)}点`;
    strip.appendChild(span);
  });

  // カード
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
      <div class="ai-meter"><div class="meter-bar"><i style="width:${Math.max(8, Math.round((item.ai?.win_prob??0)*100))}%"></i></div>
      <div>AI信頼度：${"★★★★★☆☆☆☆☆".slice(5-Math.round((item.ai?.win_prob??0)*5),10-Math.round((item.ai?.win_prob??0)*5))}</div></div>
      <div class="theme-tag">🏷️ ${themeLabel} ${themeScore}点</div>
      <div class="buttons" role="group" aria-label="アクション">
        <button class="btn primary" data-act="save_order">📝 メモする</button>
        <button class="btn" data-act="remind">⏰ 2時間後に見る</button>
        <button class="btn danger" data-act="reject">❌ 見送り</button>
      </div>`;
    return card;
  };
  data.highlights.slice(0,5).forEach((it,i)=>cards.appendChild(makeCard(it,i)));

  // 並び替え（簡易：AI×テーマで再ソート）
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

  // ボタン（モック：トースト表示／本番はAPIにPOST）
  document.addEventListener("click", (ev)=>{
    const btn = ev.target.closest("button.btn"); if(!btn) return;
    const card = btn.closest(".card"); const idx = Number(card?.dataset?.idx??0);
    const name = data.highlights[idx]?.name ?? "銘柄";
    const act = btn.dataset.act;
    const note = act==='save_order'?'（メモに保存）':act==='remind'?'（2時間後に1回お知らせ）':'（今回は見送り）';
    showToast(`${name}：${btn.textContent} ${note}`);
  });

  // ---- 修正版トースト：確実に下タブの上へ表示、フェード付き ----
  function showToast(msg){
    const t = document.createElement('div');
    // まず確実に top/bottom をリセット（他CSSの inset 競合を避ける）
    t.style.position = 'fixed';
    t.style.top = 'auto';
    t.style.left = '50%';
    t.style.transform = 'translateX(-50%)';
    t.style.bottom = computeToastBottomPx() + 'px';   // ← ここで毎回計算
    t.style.background = 'rgba(0,0,0,0.8)';
    t.style.color = '#fff';
    t.style.padding = '10px 16px';
    t.style.borderRadius = '14px';
    t.style.boxShadow = '0 6px 20px rgba(0,0,0,.4)';
    t.style.zIndex = '9999';
    t.style.opacity = '0';
    t.style.pointerEvents = 'none'; // タブの操作を邪魔しない
    t.style.transition = 'opacity 0.3s ease';

    t.textContent = msg;
    document.body.appendChild(t);

    // レイアウト確定後にフェードイン
    requestAnimationFrame(()=> t.style.opacity = '1');

    // 端末の回転やキーボード表示で可変した場合にも追従
    const onViewport = ()=>{
      t.style.bottom = computeToastBottomPx() + 'px';
    };
    window.visualViewport && window.visualViewport.addEventListener('resize', onViewport);

    setTimeout(()=>{
      t.style.opacity = '0';
      setTimeout(()=>{
        window.visualViewport && window.visualViewport.removeEventListener('resize', onViewport);
        t.remove();
      }, 300);
    }, 2000);
  }
})();