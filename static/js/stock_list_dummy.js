document.addEventListener("DOMContentLoaded", () => {

  const dummyStocks = [
    {name:"トヨタ", code:"7203", shares:100, cost:2500, price:2700},
    {name:"ソニー", code:"6758", shares:50, cost:12000, price:11500},
    {name:"任天堂", code:"7974", shares:30, cost:50000, price:52000},
    {name:"キーエンス", code:"6861", shares:20, cost:60000, price:62000},
    {name:"ファーストリテイリング", code:"9983", shares:10, cost:75000, price:76000}
  ];

  const container = document.getElementById("stock-cards-container");

  dummyStocks.forEach(stock => {
    stock.profit_amount = stock.price - stock.cost;
    stock.profit_rate = ((stock.price - stock.cost)/stock.cost*100).toFixed(2);
    stock.chart_history = [stock.cost, stock.cost*1.05, stock.cost*0.95, stock.price, stock.price*1.02];

    const wrapper = document.createElement("div");
    wrapper.className = "stock-card-wrapper";

    const card = document.createElement("div");
    card.className = "stock-card";
    card.dataset.name = stock.name;
    card.dataset.code = stock.code;
    card.dataset.shares = stock.shares + "株";
    card.dataset.cost = stock.cost.toLocaleString() + "円";
    card.dataset.price = stock.price.toLocaleString() + "円";
    card.dataset.profit = stock.profit_amount.toLocaleString() + "円 (" + stock.profit_rate + "%)";
    card.dataset.chart = JSON.stringify(stock.chart_history);

    card.innerHTML = `
      <div class="stock-header">
        <span class="stock-name">${stock.name}</span>
        <span class="stock-code">${stock.code}</span>
      </div>
      <div class="stock-body">
        <div class="stock-row"><span>株数</span><span>${stock.shares}株</span></div>
        <div class="stock-row"><span>取得単価</span><span>${stock.cost.toLocaleString()}円</span></div>
        <div class="stock-row"><span>現在株価</span><span>${stock.price.toLocaleString()}円</span></div>
        <div class="stock-row gain ${stock.profit_amount>=0?'positive':'negative'}">
          <span>損益</span>
          <span>${stock.profit_amount.toLocaleString()}円 (${stock.profit_rate}%)</span>
        </div>
      </div>
    `;

    const sellBtn = document.createElement("button");
    sellBtn.className = "sell-btn";
    sellBtn.textContent = "売却";
    sellBtn.style.pointerEvents = "auto";

    wrapper.appendChild(card);
    wrapper.appendChild(sellBtn);
    container.appendChild(wrapper);

    // ===== スワイプ検知 =====
    let startX=0, currentX=0, swiped=false;
    const swipeThreshold=80;

    card.addEventListener("touchstart", e=>{ startX = e.touches[0].clientX; });
    card.addEventListener("touchmove", e=>{
      currentX = e.touches[0].clientX - startX;
      if(currentX < 0 && currentX > -swipeThreshold){
        card.style.transform = `translateX(${currentX}px)`;
        sellBtn.style.right = `${-swipeThreshold - currentX}px`;
      }
    });
    card.addEventListener("touchend", ()=>{
      if(currentX <= -swipeThreshold/2){
        card.style.transform = `translateX(-${swipeThreshold}px)`;
        sellBtn.style.right = "0px";
        wrapper.classList.add("show-sell");
        swiped = true;
      }else{
        card.style.transform = "translateX(0px)";
        sellBtn.style.right = `-${swipeThreshold}px`;
        wrapper.classList.remove("show-sell");
        swiped = false;
      }
      currentX=0;
    });

    // ===== 売却ボタン押下 =====
    sellBtn.addEventListener("click", ()=>{
      openConfirmModal(`✅ ${stock.name} を本当に売却しますか？`, ()=>{
        showToast(`${stock.name} を売却しました ✅`);
        wrapper.remove();
      });
    });

    // ===== カードタップで詳細モーダル =====
    card.addEventListener("click", ()=>{
      if(swiped) return;
      openStockModal(card);
    });

  });

  /* ===== 株カード詳細モーダル ===== */
  const modal = document.getElementById("stock-modal");
  const closeBtn = modal.querySelector(".close");
  const sellModalBtn = document.getElementById("sell-btn");
  const modalName = document.getElementById("modal-name");
  const modalCode = document.getElementById("modal-code");
  const modalShares = document.getElementById("modal-shares");
  const modalCost = document.getElementById("modal-cost");
  const modalPrice = document.getElementById("modal-price");
  const modalProfit = document.getElementById("modal-profit");
  const chartCanvas = document.getElementById("modal-chart");
  let chartInstance = null;

  function openStockModal(card){
    modalName.textContent = card.dataset.name;
    modalCode.textContent = card.dataset.code;
    modalShares.textContent = card.dataset.shares;
    modalCost.textContent = card.dataset.cost;
    modalPrice.textContent = card.dataset.price;
    modalProfit.textContent = card.dataset.profit;

    const chartData = JSON.parse(card.dataset.chart);
    if(chartInstance) chartInstance.destroy();
    chartInstance = new Chart(chartCanvas, {
      type:'line',
      data:{
        labels:chartData.map((_,i)=>i+1),
        datasets:[{
          label:'株価推移',
          data:chartData,
          borderColor:'#3b82f6',
          backgroundColor:'rgba(59,130,246,0.2)',
          tension:0.3
        }]
      },
      options:{responsive:true, plugins:{legend:{display:false}}, scales:{y:{beginAtZero:false}}}
    });

    modal.style.display = "block";
    modal.style.top = `${window.scrollY + 60}px`;
    modal.querySelector(".modal-content").style.marginTop="0";
  }

  closeBtn.addEventListener("click", ()=>{ modal.style.display="none"; });
  window.addEventListener("click", e=>{ if(e.target==modal) modal.style.display="none"; });
  modal.addEventListener("touchstart", e=>{ if(e.target==modal) modal.style.display="none"; });

  sellModalBtn.addEventListener("click", ()=>{
    openConfirmModal(`✅ ${modalName.textContent} を本当に売却しますか？`, ()=>{
      const wrapperToRemove = Array.from(document.querySelectorAll(".stock-card-wrapper"))
        .find(w=>w.querySelector(".stock-card").dataset.name===modalName.textContent);
      if(wrapperToRemove){
        showToast(`${modalName.textContent} を売却しました ✅`);
        wrapperToRemove.remove();
      }
      modal.style.display="none";
    });
  });

  /* ===== 共通確認モーダル ===== */
  const confirmModal = document.getElementById("confirm-modal");
  const confirmMessage = document.getElementById("confirm-message");
  const btnCancel = document.getElementById("confirm-cancel");
  const btnOk = document.getElementById("confirm-ok");
  let confirmCallback = null;

  function openConfirmModal(message, callback){
    confirmMessage.textContent = message;
    confirmModal.style.display = "block";
    confirmCallback = callback;
  }

  btnCancel.addEventListener("click", ()=>{ confirmModal.style.display="none"; confirmCallback=null; });
  btnOk.addEventListener("click", ()=>{
    if(confirmCallback) confirmCallback();
    confirmModal.style.display="none";
    confirmCallback=null;
  });
  window.addEventListener("click", e=>{
    if(e.target==confirmModal){ confirmModal.style.display="none"; confirmCallback=null; }
  });

  /* ===== トースト通知 ===== */
  function showToast(message){
    const toast = document.createElement("div");
    toast.className = "toast";
    toast.textContent = message;
    document.body.appendChild(toast); // ← body に追加
    requestAnimationFrame(()=>{ toast.classList.add("show"); });
    setTimeout(()=>{
      toast.classList.remove("show");
      setTimeout(()=>toast.remove(),300);
    },1800);
  }

});
