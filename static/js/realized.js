// 高さをCSS変数に反映（top-fixedとボトムタブの実高さ）
(function(){
  function getBottomTab(){
    return document.querySelector('.bottom-tab, #bottom-tab');
  }
  function setHeights(){
    var top = document.querySelector('.top-fixed');
    var topH = top ? top.offsetHeight : 0;
    var bottom = getBottomTab();
    var bottomH = bottom ? bottom.offsetHeight : 0;
    document.documentElement.style.setProperty('--top-h', topH + 'px');
    document.documentElement.style.setProperty('--bottom-h', bottomH + 'px');
  }
  if (document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', setHeights);
  } else {
    setHeights();
  }
  window.addEventListener('resize', setHeights);
  window.addEventListener('orientationchange', setHeights);
  var top = document.querySelector('.top-fixed');
  if (top && 'MutationObserver' in window){
    new MutationObserver(setHeights).observe(top, {childList:true, subtree:true});
  }
  setTimeout(setHeights, 60);
})();

document.addEventListener('DOMContentLoaded', function () {
  var table       = document.getElementById('realizedTable');
  var tbody       = table ? table.querySelector('tbody') : null;
  var yearFilter  = document.getElementById('yearFilter');
  var monthFilter = document.getElementById('monthFilter');
  var chips       = Array.prototype.slice.call(document.querySelectorAll('.quick-chips .chip'));
  var emptyState  = document.getElementById('emptyState');

  // KPI
  var sumCountEl      = document.getElementById('sumCount');
  var winRateEl       = document.getElementById('winRate');
  var netProfitEl     = document.getElementById('netProfit');
  var totalProfitEl   = document.getElementById('totalProfit');
  var totalLossEl     = document.getElementById('totalLoss');
  var avgNetEl        = document.getElementById('avgNet');
  var avgProfitOnlyEl = document.getElementById('avgProfitOnly');
  var avgLossOnlyEl   = document.getElementById('avgLossOnly');

  if (!table || !tbody || !sumCountEl || !winRateEl || !netProfitEl) return;

  // 列インデックス（0始まり）
  var COL_DATE   = 0;
  var COL_NAME   = 1;
  var COL_BROKER = 2;
  var COL_ACCOUNT= 3;
  var COL_TYPE   = 4;
  var COL_QTY    = 5;
  var COL_PROFIT = 6; // ★ 損益額
  var COL_RATE   = 7;

  function toNumber(text) {
    if (text == null) return 0;
    var s = String(text).replace(/[^\-0-9.]/g, '');
    var v = parseFloat(s);
    return isNaN(v) ? 0 : v;
  }
  function fmtInt(n) {
    return Math.round(n).toLocaleString('ja-JP');
  }
  function allDataRows() {
    var rows = tbody.querySelectorAll('tr');
    var out = [];
    for (var i=0; i<rows.length; i++) {
      if (!rows[i].classList.contains('group-row')) out.push(rows[i]);
    }
    return out;
  }
  function visibleRows() {
    var rows = allDataRows();
    var out = [];
    for (var i=0; i<rows.length; i++) {
      if (rows[i].style.display !== 'none') out.push(rows[i]);
    }
    return out;
  }

  function updateKPI(rows) {
    var count = rows.length;
    var vals = [];
    for (var i=0; i<rows.length; i++) {
      var cell = rows[i].children[COL_PROFIT];
      vals.push(toNumber(cell ? cell.textContent : '0'));
    }
    var wins=0, net=0, posSum=0, negSum=0, j;
    for (j=0; j<vals.length; j++){
      var v = vals[j];
      net += v;
      if (v>0){ wins++; posSum += v; }
      else if (v<0){ negSum += v; }
    }

    var avgNet = count ? net / count : 0;
    var posCnt=0, negCnt=0;
    for (j=0; j<vals.length; j++){ if (vals[j]>0) posCnt++; if (vals[j]<0) negCnt++; }
    var avgPos = posCnt ? posSum/posCnt : 0;
    var avgNeg = negCnt ? negSum/negCnt : 0;

    sumCountEl.textContent  = String(count);
    winRateEl.textContent   = count ? (Math.round((wins/count)*100) + '%') : '0%';
    netProfitEl.textContent = fmtInt(net);
    netProfitEl.classList.remove('profit','loss');
    if (net > 0) netProfitEl.classList.add('profit');
    if (net < 0) netProfitEl.classList.add('loss');

    if (totalProfitEl) totalProfitEl.textContent = fmtInt(posSum);
    if (totalLossEl)   totalLossEl.textContent   = fmtInt(negSum);

    if (avgNetEl){
      avgNetEl.textContent = fmtInt(avgNet);
      avgNetEl.classList.remove('profit','loss');
      if (avgNet > 0) avgNetEl.classList.add('profit');
      if (avgNet < 0) avgNetEl.classList.add('loss');
    }
    if (avgProfitOnlyEl) avgProfitOnlyEl.textContent = fmtInt(avgPos);
    if (avgLossOnlyEl)   avgLossOnlyEl.textContent   = fmtInt(avgNeg);
  }

  function applyFilter() {
    var y = yearFilter ? yearFilter.value : '';
    var m = monthFilter ? monthFilter.value : '';
    var rows = allDataRows();
    for (var i=0; i<rows.length; i++) {
      var row  = rows[i];
      var date = row.getAttribute('data-date') || '';
      var yy = date.split('-')[0] || '';
      var mm = date.split('-')[1] || '';
      var show = true;
      if (y && yy !== y)   show = false;
      if (m && mm !== m)   show = false;
      row.style.display = show ? '' : 'none';
    }
    if (emptyState) {
      emptyState.style.display = visibleRows().length ? 'none' : '';
    }
    updateKPI(visibleRows());
  }

  // クイックフィルタ
  function pad2(n){ return n<10 ? '0'+n : ''+n; }
  function clearActiveChips(){ for (var i=0;i<chips.length;i++) chips[i].classList.remove('active'); }
  for (var i=0; i<chips.length; i++) {
    chips[i].addEventListener('click', function(){
      var now = new Date();
      var y = now.getFullYear();
      var m = now.getMonth()+1;
      var mm = pad2(m);
      var key = this.getAttribute('data-range');

      if (key === 'this-month'){
        if (yearFilter)  yearFilter.value  = String(y);
        if (monthFilter) monthFilter.value = mm;
      } else if (key === 'last-month'){
        var d = new Date(y, m-2, 1);
        if (yearFilter)  yearFilter.value  = String(d.getFullYear());
        if (monthFilter) monthFilter.value = pad2(d.getMonth()+1);
      } else if (key === 'this-year'){
        if (yearFilter)  yearFilter.value  = String(y);
        if (monthFilter) monthFilter.value = '';
      } else {
        if (yearFilter)  yearFilter.value  = '';
        if (monthFilter) monthFilter.value = '';
      }
      clearActiveChips();
      this.classList.add('active');
      applyFilter();
    });
  }
  if (yearFilter)  yearFilter.addEventListener('change', function(){ clearActiveChips(); applyFilter(); });
  if (monthFilter) monthFilter.addEventListener('change', function(){ clearActiveChips(); applyFilter(); });

  // モーダル
  var modal         = document.getElementById('stockModal');
  var closeBtn      = modal ? modal.querySelector('.close') : null;
  var modalTitle    = document.getElementById('modalTitle');
  var modalPurchase = document.getElementById('modalPurchase');
  var modalQuantity = document.getElementById('modalQuantity');
  var modalBroker   = document.getElementById('modalBroker');
  var modalAccount  = document.getElementById('modalAccount');
  var modalSell     = document.getElementById('modalSell');
  var modalProfit   = document.getElementById('modalProfit');
  var modalFee      = document.getElementById('modalFee');

  function openModalForRow(row){
    if (!modal) return;
    var name  = row.getAttribute('data-name') || '';
    var code  = row.getAttribute('data-code') || '';
    var title = name + (code ? '（' + code + '）' : '');
    if (modalTitle)    modalTitle.textContent    = title;
    if (modalPurchase) modalPurchase.textContent = row.getAttribute('data-purchase') || '';
    if (modalQuantity) modalQuantity.textContent = row.getAttribute('data-quantity') || '';
    if (modalBroker)   modalBroker.textContent   = row.getAttribute('data-broker') || '';
    if (modalAccount)  modalAccount.textContent  = row.getAttribute('data-account') || '';
    if (modalSell)     modalSell.textContent     = row.getAttribute('data-sell') || '';
    if (modalProfit)   modalProfit.textContent   = row.getAttribute('data-profit') || '';
    if (modalFee)      modalFee.textContent      = row.getAttribute('data-fee') || '';
    modal.classList.add('show');
  }

  // 行タップ/クリック
  (function attachRowHandlers(){
    var rows = allDataRows();
    for (var i=0; i<rows.length; i++) {
      (function(row){
        var sx=0, sy=0, st=0, moved=false;
        var TAP_MAX_MOVE=10, TAP_MAX_TIME=500;
        row.addEventListener('touchstart', function(e){
          var t = e.touches[0]; sx=t.clientX; sy=t.clientY; st=Date.now(); moved=false;
        }, {passive:true});
        row.addEventListener('touchmove', function(e){
          var t = e.touches[0];
          if (Math.abs(t.clientX-sx)>TAP_MAX_MOVE || Math.abs(t.clientY-sy)>TAP_MAX_MOVE) moved=true;
        }, {passive:true});
        row.addEventListener('touchend', function(e){
          var dt = Date.now()-st;
          if (!moved && dt<=TAP_MAX_TIME && row.style.display!=='none'){ e.preventDefault(); openModalForRow(row); }
        });
        row.addEventListener('click', function(){ if (row.style.display!=='none') openModalForRow(row); });
      })(rows[i]);
    }
  })();

  // 初期表示
  updateKPI(allDataRows());
  applyFilter();
});