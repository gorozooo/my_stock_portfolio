/* policy.js v7 — おまかせ時のみAIバナー表示＋手動時は通常モード表示 */
const $  = (s)=>document.querySelector(s);
const $$ = (s)=>document.querySelectorAll(s);

function abs(path){ return new URL(path, window.location.origin).toString(); }

/* ========== トースト（下タブ回避） ========== */
function computeToastBottomPx(){
  let insetBottom = 0;
  if (window.visualViewport){
    const diff = window.innerHeight - window.visualViewport.height;
    insetBottom = Math.max(0, Math.round(diff));
  }
  return insetBottom + 140;
}
function toast(msg){
  const t = document.createElement('div');
  t.className = 'toast';
  t.textContent = msg;
  t.style.bottom = computeToastBottomPx()+'px';
  document.body.appendChild(t);
  requestAnimationFrame(()=> t.style.opacity = '1');
  const onV = ()=> t.style.bottom = computeToastBottomPx()+'px';
  if (window.visualViewport) window.visualViewport.addEventListener('resize', onV);
  setTimeout(()=>{ 
    t.style.opacity='0'; 
    setTimeout(()=>{ 
      if(window.visualViewport) window.visualViewport.removeEventListener('resize', onV); 
      t.remove(); 
    }, 250); 
  }, 1800);
}

/* ========== API ========== */
async function getJSON(url){
  const r = await fetch(abs(url), {headers:{'Cache-Control':'no-store'}});
  if(!r.ok) throw new Error(`HTTP ${r.status} ${await r.text().catch(()=> '')}`);
  return await r.json();
}
async function postJSON(url, body){
  const r = await fetch(abs(url), {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(body||{})
  });
  if(!r.ok) throw new Error(`HTTP ${r.status} ${await r.text().catch(()=> '')}`);
  return await r.json();
}

/* ========== UIチップ操作 ========== */
function setPressed(container, value){
  container.querySelectorAll('.chip').forEach(btn=>{
    const on = btn.dataset.val === value;
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
}
function getPressed(container){
  const el = container.querySelector('.chip[aria-pressed="true"]');
  return el ? el.dataset.val : null;
}
function wireChips(container){
  container.addEventListener('click', (e)=>{
    const btn = e.target.closest('.chip'); if(!btn) return;
    setPressed(container, btn.dataset.val);
  });
}

/* ========== ラベル変換 ========== */
function riskLabel(code){
  switch(code){
    case 'attack': return '攻め';
    case 'normal': return '普通';
    case 'defense': return '守り';
    case 'auto': return 'おまかせ';
    default: return '-';
  }
}
function styleLabel(code){
  switch(code){
    case 'short': return '短期';
    case 'mid': return '中期';
    case 'long': return '長期';
    case 'auto': return 'おまかせ';
    default: return '-';
  }
}

/* ========== バナー表示処理 ========== */
function updateBanner(data){
  const aiBanner = $('#aiBanner');
  const running = $('#runningMode');
  const runningLabel = $('#runningLabel');

  const risk = data.current?.risk_mode ?? 'normal';
  const hold = data.current?.hold_style ?? 'mid';

  const resolvedRisk = data.resolved?.risk ?? risk;
  const resolvedHold = data.resolved?.hold ?? hold;

  const runningText = `${riskLabel(resolvedRisk)} × ${styleLabel(resolvedHold)}モード`;

  // おまかせを含む場合のみAIバナー表示
  const isAuto = (risk === 'auto' || hold === 'auto');

  if (isAuto){
    aiBanner.hidden = false;
    running.textContent = runningText;
    runningLabel.textContent = '運用中：';
  } else {
    aiBanner.hidden = true;
  }

  // 常時表示する運用中ラベル更新
  $('#runningModeAlt')?.textContent = runningText;
}

/* ========== 初期化 ========== */
(async function init(){
  try{
    wireChips($('#riskChips'));
    wireChips($('#styleChips'));

    const js = await getJSON('/advisor/api/policy/');
    setPressed($('#riskChips'),  js.current.risk_mode);
    setPressed($('#styleChips'), js.current.hold_style);
    updateBanner(js);

    // 保存
    $('#saveBtn').addEventListener('click', async ()=>{
      try{
        const risk = getPressed($('#riskChips'))  || 'normal';
        const hold = getPressed($('#styleChips')) || 'mid';
        const res = await postJSON('/advisor/api/policy/', { risk_mode: risk, hold_style: hold });
        setPressed($('#riskChips'),  res.current.risk_mode);
        setPressed($('#styleChips'), res.current.hold_style);
        updateBanner(res);
        toast('保存しました');
      }catch(e){ console.error(e); toast('通信に失敗しました'); }
    });

    // リセット
    $('#resetBtn').addEventListener('click', async ()=>{
      try{
        const res = await postJSON('/advisor/api/policy/', { risk_mode: 'normal', hold_style: 'mid' });
        setPressed($('#riskChips'),  res.current.risk_mode);
        setPressed($('#styleChips'), res.current.hold_style);
        updateBanner(res);
        toast('リセットしました');
      }catch(e){ console.error(e); toast('通信に失敗しました'); }
    });

  }catch(e){
    console.error(e);
    toast('読み込みに失敗しました');
  }
})();