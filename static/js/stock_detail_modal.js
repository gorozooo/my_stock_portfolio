/* 詳細モーダル（段階導入：まずは「概要」だけ） */
(function(){
  const mountId = "detail-modal-mount";

  function yen(n){ try { return "¥" + Math.round(Number(n || 0)).toLocaleString(); } catch(e){ return "¥0"; } }
  function num(n){ try { return Number(n || 0).toLocaleString(); } catch(e){ return "0"; } }

  function ensureMount(){
    let m = document.getElementById(mountId);
    if(!m){
      m = document.createElement("div");
      m.id = mountId;
      document.body.appendChild(m);
    }
    return m;
  }

  async function openDetail(stockId){
    if(!stockId){ console.warn("stockIdが不明"); return; }
    const mount = ensureMount();

    try{
      // 1) HTML断片を取得して挿入
      const htmlRes = await fetch(`/stocks/${stockId}/detail_fragment/`, {credentials:"same-origin"});
      if(!htmlRes.ok){ throw new Error("モーダルの読み込みに失敗しました"); }
      const html = await htmlRes.text();
      mount.innerHTML = html;

      const modal = mount.querySelector("#detail-modal");
      if(!modal){ throw new Error("モーダルが生成できませんでした"); }

      // 閉じる操作
      modal.querySelectorAll("[data-dm-close]").forEach(el=>{
        el.addEventListener("click", ()=> closeDetail());
      });
      document.addEventListener("keydown", escCloseOnce);

      // タブ切替（今は概要以外はdisabled）
      modal.querySelectorAll(".detail-tab").forEach(btn=>{
        btn.addEventListener("click", ()=>{
          if(btn.disabled) return;
          const name = btn.getAttribute("data-tab");
          modal.querySelectorAll(".detail-tab").forEach(b=>b.classList.toggle("is-active", b===btn));
          modal.querySelectorAll(".detail-panel").forEach(p=>p.classList.toggle("is-active", p.getAttribute("data-panel")===name));
        });
      });

      // 2) 概要JSONを読み込み
      const ovWrap = modal.querySelector('[data-panel="overview"]');
      const res = await fetch(`/stocks/${stockId}/overview.json`, {credentials:"same-origin"});
      if(!res.ok){ throw new Error("概要データの取得に失敗しました"); }
      const d = await res.json();

      const plClass = (Number(d.profit_loss||0) >= 0) ? "pos" : "neg";
      ovWrap.innerHTML = `
        <div class="overview-grid">
          <div class="ov-item"><div class="ov-k">証券会社</div><div class="ov-v">${d.broker||"—"}</div></div>
          <div class="ov-item"><div class="ov-k">口座区分</div><div class="ov-v">${d.account_type||"—"}</div></div>
          <div class="ov-item"><div class="ov-k">保有株数</div><div class="ov-v">${num(d.shares)} 株</div></div>
          <div class="ov-item"><div class="ov-k">ポジション</div><div class="ov-v">${d.position||"—"}</div></div>
          <div class="ov-item"><div class="ov-k">取得単価</div><div class="ov-v">${yen(d.unit_price)}</div></div>
          <div class="ov-item"><div class="ov-k">現在株価</div><div class="ov-v">${yen(d.current_price)}</div></div>
          <div class="ov-item"><div class="ov-k">取得額</div><div class="ov-v">${yen(d.total_cost)}</div></div>
          <div class="ov-item"><div class="ov-k">評価額</div><div class="ov-v">${yen(d.market_value)}</div></div>
          <div class="ov-item"><div class="ov-k">評価損益</div><div class="ov-v ${plClass}">${yen(d.profit_loss)}</div></div>
          <div class="ov-item"><div class="ov-k">購入日</div><div class="ov-v">${d.purchase_date||"—"}</div></div>
          <div class="ov-item" style="grid-column: 1 / -1;">
            <div class="ov-k">メモ</div>
            <div class="ov-v" style="white-space:pre-wrap;">${(d.note||"").trim() || "—"}</div>
          </div>
        </div>
      `;

    }catch(err){
      console.error(err);
      alert("詳細の読み込みでエラーが発生しました。時間をおいて再度お試しください。");
      closeDetail();
    }
  }

  function escCloseOnce(e){
    if(e.key === "Escape"){ closeDetail(); }
  }
  function closeDetail(){
    const m = document.getElementById(mountId);
    if(m){ m.innerHTML = ""; }
    document.removeEventListener("keydown", escCloseOnce);
  }

  // 一覧のカードに紐づけ（.stock-card の data-id を使う）
  document.addEventListener("DOMContentLoaded", ()=>{
    document.body.addEventListener("click", (e)=>{
      const card = e.target.closest(".stock-card");
      if(!card) return;
      // スワイプ時は誤タップ防止：.swiped だったらカード本体クリックで開かない
      if(card.classList.contains("swiped")) return;

      const id = card.dataset.id;
      if(!id || id === "0"){ console.warn("card dataset.id が不正"); return; }

      openDetail(id);
    });
  });
})();