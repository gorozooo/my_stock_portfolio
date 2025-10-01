(function(){
  const $=s=>document.querySelector(s);
  const fmtYen = n => (Math.round(n||0)).toLocaleString() + "円";

  async function fetchMonth(){
    const y = +$('#year').value, m = +$('#month').value;
    const b = $('#broker').value, a = $('#account').value;
    const u = `/dividends/calendar.json?year=${y}&month=${m}&broker=${encodeURIComponent(b)}&account=${encodeURIComponent(a)}`;
    const res = await fetch(u, {credentials:"same-origin"});
    return res.json();
  }

  function buildGrid(data){
    const box = $('#calGrid'); box.innerHTML='';
    const y = data.year, m = data.month;
    const first = new Date(y, m-1, 1).getDay();
    const last  = new Date(y, m, 0).getDate();

    // 前の空白
    for(let i=0;i<first;i++){ const c=document.createElement('div'); c.className='cell'; box.appendChild(c); }

    // 本体
    data.days.forEach(d=>{
      const c = document.createElement('div'); c.className='cell';
      c.innerHTML = `<div>${d.d}</div>`;
      if((d.net||0)>0){
        const b = document.createElement('div'); b.className='badge'; b.textContent = fmtYen(d.net);
        c.appendChild(b);
        c.addEventListener('click', ()=> openModal(y,m,d.d,d.items));
      }
      box.appendChild(c);
    });

    // 末尾の詰め
    const cells = first + last;
    const pad = (Math.ceil(cells/7)*7 - cells);
    for(let i=0;i<pad;i++){ const c=document.createElement('div'); c.className='cell'; box.appendChild(c); }
  }

  function openModal(y,m,d,items){
    $('#modalTitle').textContent = `${y}年${m}月${d}日`;
    const body = $('#modalBody');
    body.innerHTML = items.map(it=>
      `<div class="row"><span>${it.label}</span><span>${fmtYen(it.net)}</span></div>`
    ).join('') || '<div class="row">データなし</div>';
    $('#modalMask').style.display = 'flex';
  }

  $('#modalMask').addEventListener('click', (e)=>{
    if (e.target.id === 'modalMask') e.currentTarget.style.display='none';
  });

  ['#year','#month','#broker','#account'].forEach(sel=>{
    $(sel).addEventListener('change', ()=> fetchMonth().then(buildGrid));
  });

  fetchMonth().then(buildGrid);
})();