/* policy.js v2025-10-27 r4
   - 手動保存後も「運用中：◯×◯モード」を常時表示
   - バナーが無い場合は #runningModeAlt にも反映（どちらか片方があればOK）
   - ラベルはローカルで安全に生成
*/
const $  = (s)=>document.querySelector(s);
const $$ = (s)=>document.querySelectorAll(s);

function abs(path){ return new URL(path, window.location.origin).toString(); }

/* ---- Toast（スマホ下タブ回避） ---- */
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

/* ---- API ---- */
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

/* ---- ラベル生成（ローカル） ---- */
function labelForRisk(code){
  return ({aggressive:'攻め', normal:'普通', defensive:'守り', auto:'おまかせ'})[code] || '普通';
}
function labelForStyle(code){
  return ({short:'短期', mid:'中期', long:'長期', auto:'おまかせ'})[code] || '中期';
}

/* ---- UI処理 ---- */
function setPressed(container, value){
  if(!container) return;
  container.querySelectorAll('.chip').forEach(btn=>{
    const on = btn.dataset.val === value;
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
}
function getPressed(container){
  if(!container) return null;
  const el = container.querySelector('.chip[aria-pressed="true"]');
  return el ? el.dataset.val : null;
}
function wireChips(container){
  if(!container) return;
  container.addEventListener('click', (e)=>{
    const btn = e.target.closest('.chip'); if(!btn) return;
    setPressed(container, btn.dataset.val);
  });
}

/* ---- バナー & 運用中表示 ---- */
function setRunningText(riskCode, styleCode, labels){
  // labels（サーバー提供）が無い時はローカル生成
  const riskLabel  = labels?.risk  || labelForRisk(riskCode);
  const styleLabel = labels?.style || labelForStyle(styleCode);
  const txt = `${riskLabel} × ${styleLabel}モード`;

  const inBanner = $('#runningMode');
  const altPlace = $('#runningModeAlt'); // バナー外に用意しておくと確実
  if (inBanner) inBanner.textContent = txt;
  if (altPlace) altPlace.textContent = txt;
}

function updateBanner(bannerText, currentCodes, resolvedLabels){
  const aiBanner = $('#aiBanner');

  // 運用中テキストは常時更新（バナーの有無に依存しない）
  setRunningText(currentCodes?.risk_mode, currentCodes?.hold_style, resolvedLabels);

  // バナー自体の表示/非表示
  const show = !!bannerText;
  if (aiBanner){
    aiBanner.toggleAttribute('hidden', !show);
    aiBanner.style.display = show ? '' : 'none';
    // バナー内に説明テキストがある場合は差し替えたいならここで
    if (show) {
      // 任意：#aiBanner .banner-text があれば置換
      const btxt = aiBanner.querySelector('.banner-text');
      if (btxt) btxt.textContent = bannerText;
    }
  }
}

/* ---- 初期化 ---- */
(async function init(){
  try{
    wireChips($('#riskChips'));
    wireChips($('#styleChips'));

    const js = await getJSON('/advisor/api/policy/');
    setPressed($('#riskChips'),  js.current?.risk_mode);
    setPressed($('#styleChips'), js.current?.hold_style);
    updateBanner(js.banner, js.current, js.resolved?.labels);

    // 保存（手動モード：バナーOFFでも「運用中：◯×◯」は表示される）
    $('#saveBtn')?.addEventListener('click', async ()=>{
      try{
        const risk = getPressed($('#riskChips'))  || 'normal';
        const hold = getPressed($('#styleChips')) || 'mid';
        const res  = await postJSON('/advisor/api/policy/', { risk_mode: risk, hold_style: hold });

        setPressed($('#riskChips'),  res.current?.risk_mode);
        setPressed($('#styleChips'), res.current?.hold_style);

        // サーバーが手動時に banner を返さない想定 → 空を渡して確実に非表示
        const bannerText = res.banner || '';
        updateBanner(bannerText, res.current, res.resolved?.labels);

        toast('保存しました');
      }catch(e){ console.error(e); toast('通信に失敗しました'); }
    });

    // 既定：普通 × 中期へ
    $('#resetBtn')?.addEventListener('click', async ()=>{
      try{
        const res = await postJSON('/advisor/api/policy/', { risk_mode: 'normal', hold_style: 'mid' });
        setPressed($('#riskChips'),  res.current?.risk_mode);
        setPressed($('#styleChips'), res.current?.hold_style);
        updateBanner(res.banner || '', res.current, res.resolved?.labels);
        toast('リセットしました');
      }catch(e){ console.error(e); toast('通信に失敗しました'); }
    });

  }catch(e){
    console.error(e);
    toast('読み込みに失敗しました');
  }
})();