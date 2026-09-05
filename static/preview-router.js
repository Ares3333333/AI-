(()=>{
 const pages=window.PREVIEW_DATA.pages,articles=window.PREVIEW_DATA.articles;
 const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 function render(){
  const raw=location.hash.startsWith('#/')?location.hash.slice(1):'/';
  const u=new URL(raw,'https://preview.invalid');let path=u.pathname;if(!path.endsWith('/'))path+='/';
  const data=pages[path]||pages['/404/'];document.querySelector('main').innerHTML=data.html;document.title=data.title;document.body.dataset.static='true';
  document.querySelectorAll('.mobile-menu').forEach(m=>m.open=false);
  const domain=document.querySelector('#scan-domain');if(domain&&u.searchParams.has('domain'))domain.value=u.searchParams.get('domain').slice(0,253);
  const q=u.searchParams.get('q'),input=document.querySelector('#journal-q');
  if(q&&input){
   input.value=q;const match=path.match(/^\/journal\/topic\/([^/]+)/),cluster=match&&match[1];
   const found=articles.filter(a=>(!cluster||a.cluster===cluster)&&(a.title+' '+a.description+' '+a.search_text).toLocaleLowerCase('ru').includes(q.toLocaleLowerCase('ru')));
   const list=document.querySelector('.journal-grid')||document.querySelector('.empty-state');
   if(list){list.className='journal-grid';list.innerHTML=found.length?found.map(a=>`<article class="article-card"><div class="article-card-top"><span>${esc(a.cluster_name)}</span><span>${a.minutes} мин</span></div><h3><a href="${a.url}">${esc(a.title)}</a></h3><p>${esc(a.description)}</p><a class="card-more" href="${a.url}">Читать разбор <span>↗</span></a></article>`).join(''):'<div class="empty-state"><h2>Ничего не найдено.</h2><p>Попробуйте другой запрос.</p></div>';}
   const label=document.querySelector('.journal-results');if(label)label.textContent=`Поиск: ${q} · ${found.length} материалов`;document.querySelector('.pagination')?.remove();
  }
  window.scrollTo(0,0);document.dispatchEvent(new Event('page:load'));
 }
 document.addEventListener('click',e=>{const a=e.target.closest('a[href]');if(!a||e.ctrlKey||e.metaKey||e.shiftKey||e.altKey)return;const href=a.getAttribute('href');if(href.startsWith('/')&&!href.startsWith('//')){e.preventDefault();if(location.hash==='#'+href)render();else location.hash=href;}});
 document.addEventListener('submit',e=>{const f=e.target;if(f.matches('.domain-form,.journal-search')){e.preventDefault();location.hash=(f.getAttribute('action')||'/journal/')+'?'+new URLSearchParams(new FormData(f)).toString();}});
 window.addEventListener('hashchange',render);render();
})();
