document.addEventListener("DOMContentLoaded", ()=>{
  const form = document.getElementById("divForm");

  const brokerHidden = document.getElementById("broker");
  const acctHidden   = document.getElementById("account_type");

  const chipsWraps = document.querySelectorAll(".chips");
  chipsWraps.forEach(wrap=>{
    wrap.addEventListener("click", (e)=>{
      const btn = e.target.closest(".chip");
      if (!btn) return;
      wrap.querySelectorAll(".chip").forEach(c=>c.classList.remove("active"));
      btn.classList.add("active");
      const target = wrap.dataset.target;
      const hidden = document.getElementById(target);
      if (hidden) hidden.value = btn.textContent.trim();
      refreshKPI();
    });
  });

  // 金額入力
  const amount = document.getElementById("amount");
  const tax    = document.getElementById("tax");
  const fee    = document.getElementById("fee");

  const kNet   = document.getElementById("kNet");
  const kTax   = document.getElementById("kTax");
  const kFee   = document.getElementById("kFee");
  const kProfit= document.getElementById("kProfit");

  function toNum(s){
    if (s==null) return 0;
    const v = parseFloat(String(s).replace(/[^\-0-9.]/g,''));
    return isNaN(v) ? 0 : v;
  }
  function yen(n){ return Math.round(n).toLocaleString('ja-JP'); }
  function fmtSet(el, v){ if (el) el.textContent = yen(v); }

  function formatFieldComma(el){
    const pos = el.selectionStart;
    const raw = String(el.value).replace(/[^\-0-9]/g,'');
    if (raw === "") { el.value = ""; return; }
    const num = parseInt(raw,10);
    if (isNaN(num)) return;
    el.value = num.toLocaleString('ja-JP');
    // キャレット位置は簡易対応（末尾へ）
    el.selectionStart = el.selectionEnd = el.value.length;
  }

  [amount, tax, fee].forEach(el=>{
    if (!el) return;
    el.addEventListener("input", ()=>{
      // 数値フォーマット（,）
      formatFieldComma(el);
      refreshKPI();
    });
    el.addEventListener("blur", ()=> formatFieldComma(el));
  });

  function refreshKPI(){
    const vAmount = toNum(amount?.value);
    const vTax    = toNum(tax?.value);
    const vFee    = toNum(fee?.value);

    // 実現損益に計上するのは (受取額 - 手数料)
    const profit  = vAmount - vFee;

    fmtSet(kNet, vAmount);
    fmtSet(kTax, vTax);
    fmtSet(kFee, vFee);
    fmtSet(kProfit, profit);
  }
  refreshKPI();

  // デフォルトの受取日は今日
  const date = document.getElementById("date");
  if (date && !date.value){
    const now = new Date();
    const yyyy = now.getFullYear();
    const mm = String(now.getMonth()+1).padStart(2,'0');
    const dd = String(now.getDate()).padStart(2,'0');
    date.value = `${yyyy}-${mm}-${dd}`;
  }

  // 送信時の簡易バリデーション
  form?.addEventListener("submit", (e)=>{
    const stock = document.getElementById("stockName");
    const amt   = amount;

    let ok = true;
    if (!stock?.value.trim()) { ok = false; stock.classList.add("invalid"); }
    if (!amt?.value.trim() || toNum(amt.value) <= 0) { ok = false; amt.classList.add("invalid"); }

    if (!ok){
      e.preventDefault();
      alert("必須項目を確認してください。\n・銘柄名\n・受取額（手取り）");
      return;
    }
  });
});
