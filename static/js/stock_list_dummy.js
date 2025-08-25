document.addEventListener("DOMContentLoaded", () => {

  // ダミー株データ
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

    container.appendChild(card);
  });

  // モーダル処理
  const modal = document.getElementById("stock-modal");
  const closeBtn = modal.querySelector(".close");
  const sellBtn = document.getElementById("sell-btn");
  const modalName = document.getElementById("modal-name");
  const modalCode = document.getElementById("modal-code");
  const modalShares = document.getElementById("modal-shares");
  const modalCost = document.getElementById("modal-cost");
  const modalPrice = document.getElementById("modal-price");
  const modalProfit = document.getElementById("modal-profit");
  const chartCanvas = document.getElementById("modal-chart");
  let chartInstance = null;

  const cards = document.querySelectorAll(".stock-card");

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
      options:{responsive:true,plugins:{legend:{display:false}},scales:{y:{beginAtZero:false}}}
    });

    modal.style.display = "block";
  }

  // カードクリックのみ（スクロール優先）
  cards.forEach(card => card.addEventListener("click", openModal));

  // モーダル閉じる
  closeBtn.addEventListener("click", ()=>{ modal.style.display="none"; });
  window.addEventListener("click", e=>{ if(e.target==modal) modal.style.display="none"; });

  // ダミー売却
  sellBtn.addEventListener("click", ()=>{
    alert(`✅ ${modalName.textContent} を売却しました（ダミー処理）`);
    modal.style.display="none";
    const cardToRemove = Array.from(document.querySelectorAll(".stock-card"))
      .find(c=>c.dataset.name===modalName.textContent);
    if(cardToRemove) cardToRemove.remove();
  });

});
