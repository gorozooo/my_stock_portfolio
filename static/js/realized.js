document.addEventListener('DOMContentLoaded', function () {
  // ===== 要素取得 =====
  var table       = document.getElementById('realizedTable');
  var tbody       = table ? table.querySelector('tbody') : null;
  var yearFilter  = document.getElementById('yearFilter');
  var monthFilter = document.getElementById('monthFilter');
  var chips       = Array.prototype.slice.call(document.querySelectorAll('.quick-chips .chip'));
  var emptyState  = document.getElementById('emptyState');

  // KPI（必須3つ + あれば更新する分）
  var sumCountEl      = document.getElementById('sumCount');
  var winRateEl       = document.getElementById('winRate');
  var netProfitEl     = document.getElementById('netProfit');
  var totalProfitEl   = document.getElementById('totalProfit');
  var totalLossEl     = document.getElementById('totalLoss');
  var avgNetEl        = document.getElementById('avgNet');
  var avgProfitOnlyEl = document.getElementById('avgProfitOnly');
  var avgLossOnlyEl   = document.getElementById('avgLossOnly');

  if (!table || !tbody || !sumCountEl || !winRateEl || !netProfitEl) {
    // 必須要素が無い場合は何もしない（スマホでも落ちない）
    return;
  }

  // ===== ユーティリティ =====
  function toNumber(text) {
    if (text == null) return 0;
    var s = String(text).replace(/[^\-0-9.]/g, ''); // +, カンマ, ％ など除去
    var v = parseFloat(s);
    return isNaN(v) ? 0 : v;
  }
  function fmtInt(n) {
    // 端数は四捨五入の整数表示（桁区切り）
    return Math.round(n).toLocaleString();
  }
  function dataRowsAll() {
    // データ行のみ（グループ行は data-date を持たない）
    return Array.prototype.slice.call(tbody.querySelectorAll('tr[data-date]'));
  }
  function visibleRows() {
    return dataRowsAll().filter(function (r) {
      return r.style.display !== 'none';
    });
  }

  // ===== KPI 更新 =====
  function updateKPI(rows) {
    try {
      var vals = rows.map(function (r) {
        // 損益額は5列目（0開始で index 4）
        var cell = r.children[4];
        return toNumber(cell ? cell.textContent : '');
      });
      var pos = vals.filter(function (v) { return v > 0; });
      var neg = vals.filter(function (v) { return v < 0; });

      var count = rows.length;
      var wins  = pos.length;
      var net   = vals.reduce(function (a, b) { return a + b; }, 0);
      var posSum= pos.reduce(function (a, b) { return a + b; }, 0);
      var negSum= neg.reduce(function (a, b) { return a + b; }, 0);
      var avgNet= count ? net / count : 0;
      var avgPos= pos.length ? posSum / pos.length : 0;
      var avgNeg= neg.length ? negSum / neg.length : 0;

      // 1行目（必須3項目）
      sumCountEl.textContent  = String(count);
      winRateEl.textContent   = count ? (Math.round((wins / count) * 100) + '%') : '0%';
      netProfitEl.textContent = fmtInt(net);
      netProfitEl.classList.remove('profit','loss');
      if (net > 0) netProfitEl.classList.add('profit');
      if (net < 0) netProfitEl.classList.add('loss');

      // 2行目
      if (totalProfitEl) totalProfitEl.textContent = fmtInt(posSum);
      if (totalLossEl)   totalLossEl.textContent   = fmtInt(negSum);

      // 3行目
      if (avgNetEl) {
        avgNetEl.textContent = fmtInt(avgNet);
        avgNetEl.classList.remove('profit','loss');
        if (avgNet > 0) avgNetEl.classList.add('profit');
        if (avgNet < 0) avgNetEl.classList.add('loss');
      }
      if (avgProfitOnlyEl) avgProfitOnlyEl.textContent = fmtInt(avgPos);
      if (avgLossOnlyEl)   avgLossOnlyEl.textContent   = fmtInt(avgNeg);
    } catch (e) {
      // どこかで失敗してもKPIが消えたままにならないように最低限の値を維持
      sumCountEl.textContent  = sumCountEl.textContent || '0';
      winRateEl.textContent   = winRateEl.textContent  || '0%';
      netProfitEl.textContent = netProfitEl.textContent|| '0';
    }
  }

  // ===== フィルタ =====
  function applyFilter() {
    var y = yearFilter ? yearFilter.value : '';
    var m = monthFilter ? monthFilter.value : '';

    var rows = dataRowsAll();
    for (var i = 0; i < rows.length; i++) {
      var row  = rows[i];
      var date = row.getAttribute('data-date') || '';
      var parts = date.split('-'); // ["YYYY","MM","DD"]
      var yy = parts[0] || '';
      var mm = parts[1] || '';
      var show = true;
      if (y && yy !== y)   show = false;
      if (m && mm !== m)   show = false;
      row.style.display = show ? '' : 'none';
    }

    if (emptyState) {
      var anyVisible = visibleRows().length > 0;
      emptyState.style.display = anyVisible ? 'none' : '';
    }

    updateKPI(visibleRows());
  }

  // ===== クイックフィルタ =====
  function pad2(n) { return n < 10 ? '0' + n : '' + n; }
  function clearActiveChips() {
    for (var i=0; i<chips.length; i++) chips[i].classList.remove('active');
  }

  for (var i = 0; i < chips.length; i++) {
    chips[i].addEventListener('click', function () {
      var now = new Date();
      var y = now.getFullYear();
      var m = now.getMonth() + 1;
      var mm = pad2(m);
      var key = this.getAttribute('data-range');

      if (key === 'this-month') {
        if (yearFilter)  yearFilter.value  = String(y);
        if (monthFilter) monthFilter.value = mm;
      } else if (key === 'last-month') {
        var d = new Date(y, m - 2, 1);
        if (yearFilter)  yearFilter.value  = String(d.getFullYear());
        if (monthFilter) monthFilter.value = pad2(d.getMonth() + 1);
      } else if (key === 'this-year') {
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

  // プルダウン変更
  if (yearFilter)  yearFilter.addEventListener('change', function () { clearActiveChips(); applyFilter(); });
  if (monthFilter) monthFilter.addEventListener('change', function () { clearActiveChips(); applyFilter(); });

  // ===== モーダル（行タップ / クリック） =====
  var modal        = document.getElementById('stockModal');
  var closeBtn     = modal ? modal.querySelector('.close') : null;
  var modalName    = document.getElementById('modalName');
  var modalPrice   = document.getElementById('modalPrice');
  var modalSector  = document.getElementById('modalSector');
  var modalPurchase= document.getElementById('modalPurchase');
  var modalQuantity= document.getElementById('modalQuantity');
  var modalProfit  = document.getElementById('modalProfit');
  var modalRate    = document.getElementById('modalRate');

  function openModalForRow(row) {
    if (!modal) return;
    if (modalName)     modalName.textContent     = row.getAttribute('data-name')     || '';
    if (modalPrice)    modalPrice.textContent    = row.getAttribute('data-price')    || '';
    if (modalSector)   modalSector.textContent   = row.getAttribute('data-sector')   || '';
    if (modalPurchase) modalPurchase.textContent = row.getAttribute('data-purchase') || '';
    if (modalQuantity) modalQuantity.textContent = row.getAttribute('data-quantity') || '';
    if (modalProfit)   modalProfit.textContent   = row.getAttribute('data-profit')   || '';
    if (modalRate)     modalRate.textContent     = row.getAttribute('data-rate')     || '';
    modal.classList.add('show');
  }
  function attachRowHandlers() {
    var rows = dataRowsAll();
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
  }
  if (modal && closeBtn) {
    closeBtn.addEventListener('click', function(){ modal.classList.remove('show'); });
    window.addEventListener('click', function(e){ if (e.target === modal) modal.classList.remove('show'); });
  }

  // ===== 初期処理 =====
  attachRowHandlers();          // 行のクリック/タップ
  updateKPI(dataRowsAll());     // 初回は全データでKPIを確定（ここが一番大事）
  applyFilter();                // フィルタ状態を反映（プルダンが空なら全件のまま）
});