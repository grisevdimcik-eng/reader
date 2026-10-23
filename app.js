(function(){
const $=id=>document.getElementById(id), root=document.documentElement;
const defaults={theme:'auto',font:'serif',size:19,lh:1.7,width:'mid'};
const widths={narrow:'32rem',mid:'38rem',wide:'48rem'};
let settings=JSON.parse(localStorage.getItem('reader.settings')||'null')||defaults;
let currentUrl='', currentData=null, currentLinks={prev:null,next:null};
function apply(){root.dataset.theme=settings.theme;root.style.setProperty('--read-size',settings.size+'px');root.style.setProperty('--lh',settings.lh);root.style.setProperty('--width',widths[settings.width]);root.style.setProperty('--read-font',settings.font==='sans'?'system-ui,-apple-system,"Segoe UI",sans-serif':'Georgia,"Times New Roman",serif');$('size').value=settings.size;$('lh').value=settings.lh;$('sizeV').textContent=settings.size+' px';$('lhV').textContent=Number(settings.lh).toFixed(2);document.querySelectorAll('.seg').forEach(seg=>seg.querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',b.dataset.v===settings[seg.dataset.key])));localStorage.setItem('reader.settings',JSON.stringify(settings))} apply();
document.querySelectorAll('.seg button').forEach(b=>b.onclick=()=>{settings[b.parentElement.dataset.key]=b.dataset.v;apply()});$('size').oninput=e=>{settings.size=+e.target.value;apply()};$('lh').oninput=e=>{settings.lh=+e.target.value;apply()};$('gear').onclick=()=>{$('panel').hidden=!$('panel').hidden};
function show(which){$('home').hidden=which!=='home';$('loading').hidden=which!=='loading';$('article').hidden=which!=='article';$('bar').hidden=which==='home'}
function error(msg){$('err').textContent=msg;$('err').hidden=false;show('home')}
function normalize(url){if(!/^https?:\/\//i.test(url))url='https://'+url;return new URL(url).href}
function absolute(u,base){try{return new URL(u,base).href}catch{return null}}
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function mdInline(s){let x=esc(s);x=x.replace(/!\[([^\]]*)\]\((https?:\/\/[^\s)]+)\)/g,'<img alt="$1" src="$2">');x=x.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,'<a href="$2" target="_blank" rel="noopener">$1</a>');x=x.replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');x=x.replace(/__([^_]+)__/g,'<strong>$1</strong>');x=x.replace(/\*([^*]+)\*/g,'<em>$1</em>');x=x.replace(/_([^_]+)_/g,'<em>$1</em>');return x}
function markdownToHtml(md){
 let lines=md.replace(/\r/g,'').split('\n'), out=[], para=[];
 const flush=()=>{if(para.length){out.push('<p>'+mdInline(para.join(' '))+'</p>');para=[]}};
 let inList=false;
 for(let line of lines){
  if(/^\s*$/.test(line)){flush();if(inList){out.push('</ul>');inList=false}continue}
  let m=line.match(/^#{1,4}\s+(.+)$/);if(m){flush();if(inList){out.push('</ul>');inList=false}let n=line.match(/^#+/)[0].length;out.push('<h'+n+'>'+mdInline(m[1])+'</h'+n+'>');continue}
  m=line.match(/^[-*]\s+(.+)$/);if(m){flush();if(!inList){out.push('<ul>');inList=true}out.push('<li>'+mdInline(m[1])+'</li>');continue}
  if(/^>\s?/.test(line)){flush();if(inList){out.push('</ul>');inList=false}out.push('<blockquote>'+mdInline(line.replace(/^>\s?/,''))+'</blockquote>');continue}
  if(/^---+$/.test(line.trim())){flush();if(inList){out.push('</ul>');inList=false}out.push('<hr>');continue}
  if(inList){out.push('<li>'+mdInline(line.trim())+'</li>')}else para.push(line.trim());
 }
 flush();if(inList)out.push('</ul>');return out.join('\n')||'<p>'+esc(md)+'</p>';
}
function parseJina(text,url){
 let title=new URL(url).hostname, content=text;
 const fm=text.match(/^---\n([\s\S]*?)\n---\n([\s\S]*)$/);
 if(fm){const t=fm[1].match(/^title:\s*["']?(.*?)["']?\s*$/m);if(t)title=t[1].replace(/^['"]|['"]$/g,'');content=fm[2]}
 content=content.replace(/^\s*Title:\s*.*\n/i,'').trim();
 return {title,html:markdownToHtml(content),text:content,url,host:new URL(url).hostname};
}
function lovereadInfo(url){try{const u=new URL(url);if(!/loveread\.ec$/i.test(u.hostname))return null;const id=u.searchParams.get('id');if(!id)return null;let p=parseInt(u.searchParams.get('p')||'1',10);if(!Number.isFinite(p)||p<1)p=1;return {id,p}}catch{return null}}
function lovereadPage(id,p){return 'https://loveread.ec/read_book.php?id='+encodeURIComponent(id)+'&p='+p}
async function fetchJina(url){
 const target='https://r.jina.ai/'+url;
 const r=await fetch(target,{headers:{'Accept':'text/plain'}});
 if(!r.ok)throw new Error('Сервис чтения ответил ошибкой '+r.status+'.');
 const text=await r.text();
 if(!text.trim())throw new Error('Сервис не вернул текст страницы.');
 return text;
}
async function fetchPage(url){
 const lr=lovereadInfo(url);
 if(lr){
   const text=await fetchJina(lovereadPage(lr.id,lr.p));
   const data=parseJina(text,url);
   return {doc:null,data,next:lovereadPage(lr.id,lr.p+1),prev:lr.p>1?lovereadPage(lr.id,lr.p-1):null};
 }
 const r=await fetch(url,{redirect:'follow'});if(!r.ok)throw new Error('Сайт ответил ошибкой '+r.status+'.');const html=await r.text();const doc=new DOMParser().parseFromString(html,'text/html');return {doc,data:extract(doc,url),next:findNav(doc,url,'next'),prev:findNav(doc,url,'prev')}
}
function cleanDoc(doc,base){doc.querySelectorAll('script,style,noscript,iframe,object,embed,form,nav,footer,header,aside,[role="navigation"],[role="banner"],[role="complementary"]').forEach(x=>x.remove());doc.querySelectorAll('a').forEach(a=>{const h=absolute(a.getAttribute('href'),base);if(h)a.href=h});doc.querySelectorAll('img').forEach(i=>{const s=absolute(i.getAttribute('src'),base);if(s)i.src=s;else i.remove()})}
function score(el){const text=(el.innerText||'').trim();if(text.length<400)return -1;let p=el.querySelectorAll('p').length;let links=el.querySelectorAll('a').length;return text.length+p*180-links*20}
function extract(doc,url){cleanDoc(doc,url);let candidates=[...doc.querySelectorAll('article,main,[role="main"],.article,.post,.entry-content,.post-content,.chapter,.story')];let best=candidates.sort((a,b)=>score(b)-score(a))[0];if(!best||score(best)<0)best=doc.body;let title=(doc.querySelector('h1')||doc.querySelector('title'))?.textContent?.trim()||new URL(url).hostname;let clone=best.cloneNode(true);let text=clone.innerText.trim();if(text.length<200)throw new Error('Не удалось найти основной текст на этой странице.');return {title,html:clone.innerHTML,text,url,host:new URL(url).hostname}}
function findNav(doc,url,dir){const selectors=dir==='next'?['a[rel="next"]','.next a','.pagination .next a','.pager .next a','a.next','a[aria-label*="next" i]','a[aria-label*="след" i]']:['a[rel="prev"]','.prev a','.pagination .prev a','.pager .prev a','a.prev','a[aria-label*="prev" i]','a[aria-label*="пред" i]'];for(const s of selectors){const a=doc.querySelector(s);const h=a&&absolute(a.getAttribute('href'),url);if(h)return h}const words=dir==='next'?['next','следующая','следующая страница','дальше','вперёд','вперед','»','>']:['prev','предыдущая','назад','«','<'];for(const a of doc.querySelectorAll('a[href]')){const t=(a.innerText||a.getAttribute('aria-label')||'').trim().toLowerCase();if(words.some(w=>t===w||t.includes(w)&&t.length<40)){const h=absolute(a.getAttribute('href'),url);if(h)return h}}return null}
function saveRecent(d){let a=JSON.parse(localStorage.getItem('reader.recent')||'[]').filter(x=>x.url!==d.url);a.unshift({url:d.url,title:d.title,host:d.host});localStorage.setItem('reader.recent',JSON.stringify(a.slice(0,10)));renderRecent()}
function renderRecent(){const a=JSON.parse(localStorage.getItem('reader.recent')||'[]');$('recent').hidden=!a.length;$('recentList').innerHTML='';a.forEach(x=>{const li=document.createElement('li'),q=document.createElement('a');q.href='?u='+encodeURIComponent(x.url);q.innerHTML='<span class="t"></span><span class="m"></span>';q.querySelector('.t').textContent=x.title;q.querySelector('.m').textContent=x.host;li.append(q);$('recentList').append(li)})}renderRecent();
function render(d,nav){currentData=d;currentLinks={next:nav.next,prev:nav.prev};document.title=d.title;$('title').textContent=d.title;$('host').textContent=d.host;$('meta').innerHTML='<span>'+d.host+'</span>';$('body').innerHTML=d.html;$('next').hidden=!nav.next;$('prev').hidden=!nav.prev;$('pager').hidden=!nav.next&&!nav.prev;$('end').innerHTML='<span>Конец страницы.</span> <a href="'+d.url+'" target="_blank" rel="noopener">Открыть оригинал</a>';show('article');saveRecent(d);window.scrollTo(0,0);updateProgress()}
async function open(url,push=true){try{url=normalize(url);currentUrl=url;if(push)history.pushState(null,'','?u='+encodeURIComponent(url));$('err').hidden=true;$('host').textContent=new URL(url).hostname;show('loading');const x=await fetchPage(url);render(x.data,{next:x.next,prev:x.prev})}catch(e){error((e&&e.message)||'Не удалось загрузить страницу. Этот сайт может запрещать запросы из браузера (CORS).')}}
$('form').onsubmit=e=>{e.preventDefault();if($('url').value.trim())open($('url').value.trim())};$('back').onclick=()=>{history.pushState(null,'',location.pathname);currentUrl='';show('home');$('url').focus()};$('next').onclick=()=>currentLinks.next&&open(currentLinks.next);$('prev').onclick=()=>currentLinks.prev&&open(currentLinks.prev);window.onpopstate=()=>{const u=new URLSearchParams(location.search).get('u');if(u)open(u,false);else show('home')};
function updateProgress(){const h=document.documentElement.scrollHeight-innerHeight,p=h>0?Math.min(1,Math.max(0,scrollY/h)):0;$('progress').style.transform='scaleX('+p+')';if(currentUrl)localStorage.setItem('reader.pos.'+currentUrl,String(p))}addEventListener('scroll',updateProgress,{passive:true});
const initial=new URLSearchParams(location.search).get('u');if(initial)open(initial,false);else show('home');if('serviceWorker' in navigator)navigator.serviceWorker.register('sw.js').catch(()=>{});
})();
