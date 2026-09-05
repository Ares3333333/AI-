(() => {
'use strict';
let accessToken='',pollTimer=null,briefUrl='';
const $=s=>document.querySelector(s);
const staticMode=()=>document.body.dataset.static==='true';
function message(selector,text,error=false){const e=$(selector);if(e){e.textContent=text;e.classList.toggle('error',error);}}
async function api(path,method='GET',body){
 if(staticMode())throw new Error('Это переносимый предпросмотр. Для операции нужен сервер. Данные никуда не отправлены.');
 const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),15000);
 try{
  const response=await fetch(path,{method,credentials:'same-origin',cache:'no-store',signal:controller.signal,headers:{'Content-Type':'application/json','X-CSRF-Token':$('meta[name="csrf-token"]')?.content||''},body:method==='GET'?undefined:JSON.stringify(body||{})});
  let data;try{data=await response.json();}catch{throw new Error('Сервер не вернул ожидаемый ответ.');}
  if(!response.ok)throw new Error(data.message||'Операция не выполнена.');return data;
 }catch(e){if(e.name==='AbortError')throw new Error('Время ожидания сервера истекло. Результат не подтверждён.');throw e;}
 finally{clearTimeout(timer);}
}
function brief(form){
 const values=Object.fromEntries(new FormData(form));const labels={role:'Роль',task:'Задача',budget:'Бюджет',timing:'Срок'};
 const text='БРИФ НА ИИ-СИСТЕМУ\nСформирован локально. Не отправлен исполнителю.\n\n'+Object.entries(labels).map(([k,v])=>v+': '+(values[k]||'')).join('\n\n');
 const output=$('#brief-output');output.hidden=false;output.querySelector('textarea').value=text;
 if(briefUrl)URL.revokeObjectURL(briefUrl);
 briefUrl=URL.createObjectURL(new Blob([text],{type:'text/plain;charset=utf-8'}));
 const link=output.querySelector('a');link.href=briefUrl;link.download='business-brief.txt';link.click();
 message('#brief-message','Бриф сформирован. Браузеру предложено скачать файл; текст также доступен ниже. Ничего не отправлялось, встреча не назначена.');
}
function cleanDomain(value){
 const raw=(value||'').trim().replace(/^https?:\/\//i,'').replace(/^www\./i,'').split(/[\/?#]/)[0];
 return raw.replace(/[^a-z0-9а-яё.-]/gi,'').slice(0,46);
}
function initializeDomainPreview(){
 const input=document.querySelector('[data-domain-input]');
 const preview=document.querySelector('[data-domain-preview]');
 const value=document.querySelector('[data-domain-value]');
 const state=document.querySelector('[data-domain-state]');
 if(!input||!preview||!value||!state)return;
 const update=()=>{
  const domain=cleanDomain(input.value);
  preview.classList.toggle('domain-preview-active',Boolean(domain));
  value.textContent=domain||'Ваш сайт.';
  state.textContent=domain?'Готов к ограниченной проверке':'Введите домен слева';
 };
 input.addEventListener('input',update,{passive:true});
 input.addEventListener('focus',()=>preview.classList.add('domain-preview-focus'));
 input.addEventListener('blur',()=>preview.classList.remove('domain-preview-focus'));
 update();
}
document.addEventListener('submit',async e=>{
 const form=e.target;if(!(form instanceof HTMLFormElement))return;
 if(form.id==='brief-form'){e.preventDefault();brief(form);return;}
 if(!['scan-form','save-form'].includes(form.id))return;e.preventDefault();
 const target=form.id==='scan-form'?'#scan-message':'#save-message';const button=form.querySelector('button[type="submit"]');
 if(form.id==='scan-form'&&form.dataset.available!=='true'){message(target,'Сканирование отключено в этом предпросмотре. Задание не создавалось, анализ не запускался. Серверная реализация находится в исходниках.',true);return;}
 button.disabled=true;
 try{
  if(form.id==='scan-form'){
   message(target,'Передаём задание серверу. Результат появится после фактической проверки.');
   const key=form.dataset.idempotency||crypto.randomUUID();form.dataset.idempotency=key;
   const result=await api('/api/scans','POST',{domain:form.elements.domain.value,idempotency_key:key,plan:'free'});
   location.assign('/report/'+encodeURIComponent(result.id)+'/');
  }else{await api('/api/access/request','POST',{email:form.elements.email.value,report_id:form.dataset.reportId});message(target,'Письмо передано почтовому серверу. Проверьте входящие и папку спама.');}
 }catch(error){message(target,error.message,true);}finally{button.disabled=false;}
});
document.addEventListener('click',async e=>{
 const remove=e.target.closest('[data-delete-report]');
 if(remove){if(!confirm('Удалить этот отчёт и его наблюдения без возможности восстановления?'))return;try{await api('/api/reports/'+encodeURIComponent(remove.dataset.deleteReport),'DELETE');location.assign('/account/');}catch(error){message('#delete-message',error.message,true);}}
 const select=e.target.closest('[data-select-brief]');if(select){$('#brief-output textarea').focus();$('#brief-output textarea').select();}
 const verify=e.target.closest('#verify-access');
 if(verify){if(!accessToken){message('#access-message','Одноразовая ссылка отсутствует. Откройте ссылку из письма.',true);return;}verify.disabled=true;
  try{await api('/api/access/verify','POST',{token:accessToken});accessToken='';location.assign('/account/');}catch(error){message('#access-message',error.message,true);}finally{verify.disabled=false;}}
});
function initialize(){
 if(pollTimer)clearTimeout(pollTimer);
 initializeDomainPreview();
 if(location.hash.startsWith('#token=')){accessToken=new URLSearchParams(location.hash.slice(1)).get('token')||'';history.replaceState(null,'',location.pathname);message('#access-message','Ссылка получена. Нажмите кнопку для подтверждения входа.');}
 const progress=$('#report-progress');
 if(progress&&['queued','running'].includes(progress.dataset.status)&&!staticMode()){
  let attempts=0;
  const poll=async()=>{if(++attempts>55||!document.contains(progress)){message('#report-progress','Проверка состояния остановлена. Обновите страницу.');return;}
   try{const job=await api('/api/scans/'+encodeURIComponent(progress.dataset.jobId));progress.textContent=job.status_label+' · получено HTML-страниц: '+job.pages_processed;if(!['queued','running'].includes(job.status)){location.reload();return;}pollTimer=setTimeout(poll,2000);}catch(error){message('#report-progress',error.message,true);}};
  pollTimer=setTimeout(poll,1000);
 }
}
document.addEventListener('page:load',initialize);initialize();
})();
