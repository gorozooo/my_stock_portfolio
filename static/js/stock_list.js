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
    const profitAmount = (stock.price - stock.cost) * stock.shares;
    const profitRate = ((stock.price - stock.cost) / stock.cost * 100).toFixed(2);
    const valuation = stock.price * stock.shares;

    stock.chart_history = [
      stock.cost,
      stock.cost * 1.05,
      stock.cost * 0.95,
      stock.price,
      stock.price * 1.02
    ];

    const wrapper = document.createElement("div");
    wrapper.className = "stock-card-wrapper";

    const card = document.createElement("div");
    card.className = "stock-card";
    card.dataset.name = stock.name;
    card.dataset.code = stock.code;
    card.dataset.shares = stock.shares + "株";
    card.dataset.cost = stock.cost.toLocaleString() + "円";
    card.dataset.price = stock.price.toLocaleString() + "円";
    card.dataset.profit = profitAmount.toLocaleString() + "円 (" + profitRate + "%)";
    card.dataset.chart = JSON.stringify(stock.chart_history);

    card.innerHTML = `
      <div class="stock-header">
        <span class="stock-name">${stock.name}</span>
        <span class="stock-price">${stock.price.toLocaleString()}円</span>
      </div>
      <div class="stock-info">
        <p><span class="label">株数</span><br>${stock.shares}株</p>
        <p><span class="label">取得単価</span><br>${stock.cost.toLocaleString()}円</p>
        <p><span class="label">評価額</span><br>${valuation.toLocaleString()}円</p>
        <p class="gain ${profitAmount >= 0 ? "positive" : "negative"}">
          <span class="label">損益</span><br>${profitAmount.toLocaleString()}円 (${profitRate}%)
        </p>
      </div>
    `;

    // 売却ボタン（スワイプで出す用）
    const sellBtn = document.createElement("button");
    sellBtn.className = "sell-swipe-button";
    sellBtn.textContent = "売却";

    wrapper.appendChild(card);
    wrapper.appendChild(sellBtn);
    container.appendChild(wrapper);
  });

  // モーダル
  const modal = document.getElementById("stock-modal");
  const closeBtn = modal.querySelector(".close");
  const sellBtnModal = document.getElementById("sell-btn");
  const modalName = document.getElementById("modal-name");
  const modalCode = document.getElementById("modal-code");
  const modalShares = document.getElementById("modal-shares");
  const modalCost = document.getElementById("modal-cost");
  const modalPrice = document.getElementById("modal-price");
  const modalProfit = document.getElementById("modal-profit");
  const chartCanvas = document.getElementById("modal-chart");
  let chartInstance = null;

  function openModal(e) {
    const card = e.currentTarget;
    modalName.textContent = card.dataset.name;
    modalCode.textContent = card.dataset.code;
    modalShares.textContent = card.dataset.shares;
    modalCost.textContent = card.dataset.cost;
    modalPrice.textContent = card.dataset.price;
    modalProfit.textContent = card.dataset.profit;

    const chartData = JSON.parse(card.dataset.chart);
    if(chartInstance) chartInstance.destroy();
    chartInstance = new Chart(chartCanvas, {
      type: 'line',
      data: {
        labels: chartData.map((_,i)=>i+1),
        datasets:[{
          label:'株価推移',
          data:chartData,
          borderColor:'#3b82f6',
          backgroundColor:'rgba(59,130,246,0.2)',
          tension:0.3
        }]
      },
      options:{
        responsive:true,
        plugins:{legend:{display:false}},
        scales:{y:{beginAtZero:false}}
      }
    });

    modal.style.display = "block";
  }

  // カードクリックでモーダル
  document.querySelectorAll(".stock-card").forEach(card => {
    card.addEventListener("click", openModal);
  });

  // モーダル閉じる
  closeBtn.addEventListener("click", ()=>{ modal.style.display="none"; });
  window.addEventListener("click", e=>{ if(e.target==modal) modal.style.display="none"; });
  modal.addEventListener("touchstart", e=>{ if(e.target==modal) modal.style.display="none"; });

  // モーダル売却（ダミー処理）
  sellBtnModal.addEventListener("click", ()=>{
    alert(`✅ ${modalName.textContent} を売却しました（ダミー処理）`);
    modal.style.display="none";
    const cardToRemove = Array.from(document.querySelectorAll(".stock-card"))
      .find(c=>c.dataset.name===modalName.textContent);
    if(cardToRemove) cardToRemove.parentElement.remove();
  });

});