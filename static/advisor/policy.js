const $  = (s)=>document.querySelector(s);
const $$ = (s)=>document.querySelectorAll(s);

function abs(path){ return new URL(path, window.location.origin).toString(); }

// ---- Toast（スマホ下タブ回避）
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
  setTimeout(()=>{ t.style.opacity='0'; setTimeout(()=>{ if(window.visualViewport) window.visualViewport.removeEventListener('resize', onV); t.remove(); }, 250); }, 1800);
}

// ---- API
async function getJSON(url){
  const r = await fetch(abs(url), {headers:{'Cache-Control':'no-store'}});
  if(!r.ok) throw new Error(`HTTP ${r.status} ${await r.text().catch(()=> '')}`);
  return await r.json();
}
async function postJSON(url, body){
  const r = await fetch(abs(url), {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body||{})});
  if(!r.ok) throw new Error(`HTTP ${r.status} ${await r.text().catch(()=> '')}`);
  return await r.json();
}

// ---- UI
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

// ---- 初期化
(async function init(){
  try{
    wireChips($('#riskChips'));
    wireChips($('#styleChips'));

    const js = await getJSON('/advisor/api/policy/');
    // 現在値を反映
    setPressed($('#riskChips'),  js.current.risk_mode);
    setPressed($('#styleChips'), js.current.hold_style);

    // バナー＆運用中表示
    const aiBanner = $('#aiBanner');
    const running  = `${js.resolved.labels.risk} × ${js.resolved.labels.style}モード`;
    $('#runningMode').textContent = running;
    if (js.banner){ aiBanner.hidden = false; } else { aiBanner.hidden = true; }

    // 保存
    $('#saveBtn').addEventListener('click', async ()=>{
      try{
        const risk = getPressed($('#riskChips'))  || 'normal';
        const hold = getPressed($('#styleChips')) || 'mid';
        const res = await postJSON('/advisor/api/policy/', { risk_mode: risk, hold_style: hold });
        const running2 = `${res.resolved.labels.risk} × ${res.resolved.labels.style}モード`;
        $('#runningMode').textContent = running2;
        // 自動選択バナー表示/非表示
        const autoOn = (risk==='auto' || hold==='auto');
        $('#aiBanner').hidden = !autoOn;
        toast('保存しました');
      }catch(e){
        console.error(e); toast('通信に失敗しました');
      }
    });

    // リセット（既定：普通 × 中期）
    $('#resetBtn').addEventListener('click', async ()=>{
      try{
        setPressed($('#riskChips'), 'normal');
        setPressed($('#styleChips'), 'mid');
        const res = await postJSON('/advisor/api/policy/', { risk_mode: 'normal', hold_style: 'mid' });
        const running2 = `${res.resolved.labels.risk} × ${res.resolved.labels.style}モード`;
        $('#runningMode').textContent = running2;
        $('#aiBanner').hidden = true;
        toast('リセットしました');
      }catch(e){
        console.error(e); toast('通信に失敗しました');
      }
    });

  }catch(e){
    console.error(e);
    toast('読み込みに失敗しました');
  }
})();