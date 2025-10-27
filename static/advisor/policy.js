/* policy.js v8 — 完全動作版（クリック修復＋おまかせ時のみAIバナー） */
const $  = (s)=>document.querySelector(s);
const $$ = (s)=>document.querySelectorAll(s);

function abs(path){ return new URL(path, window.location.origin).toString(); }

/* === Toast === */
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

/* === API === */
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

/* === UIチップ === */
function setPressed(container, value){
  container.querySelectorAll('.chip').forEach(btn=>{
    const on = btn.dataset.val === value;
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    btn.classList.toggle('active', on);
  });
}
function getPressed(container){
  const el = container.querySelector('.chip[aria-pressed="true"]');
  return el ? el.dataset.val : null;
}
function wireChips(container){
  container.querySelectorAll('.chip').forEach(btn=>{
    btn.addEventListener('click', ()=>{
      setPressed(container, btn.dataset.val);
    }, {passive:true});
  });
}

/* === ラベル === */
function riskLabel(code){
  return {attack:'攻め', normal:'普通', defense:'守り', auto:'おまかせ'}[code] || '-';
}
function styleLabel(code){
  return {short:'短期', mid:'中期', long:'長期', auto:'おまかせ'}[code] || '-';
}

/* === バナー表示 === */
function updateBanner(data){
  const aiBanner = $('#aiBanner');
  const risk = data.current?.risk_mode ?? 'normal';
  const hold = data.current?.hold_style ?? 'mid';
  const resolvedRisk = data.resolved?.risk ?? risk;
  const resolvedHold = data.resolved?.hold ?? hold;

  const runningText = `${riskLabel(resolvedRisk)} × ${styleLabel(resolvedHold)}モード`;
  const isAuto = (risk === 'auto' || hold === 'auto');

  if (isAuto){
    aiBanner.hidden = false;
    $('#runningMode').textContent = runningText;
  } else {
    aiBanner.hidden = true;
  }

  $('#runningModeAlt')?.textContent = runningText;
}

/* === 初期化 === */
(async function init(){
  try{
    wireChips($('#riskChips'));
    wireChips($('#styleChips'));

    const js = await getJSON('/advisor/api/policy/');
    setPressed($('#riskChips'),  js.current.risk_mode);
    setPressed($('#styleChips'), js.current.hold_style);
    updateBanner(js);

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