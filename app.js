const $ = (id) => document.getElementById(id);
const store = {
  get: (k, d) => { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch {} },
};

// Адрес воркера можно вписать сюда или в настройках в самом приложении
const DEFAULT_WORKER = '';

let state = { bookUrl: '', page: 1, total: null };

function workerUrl() { return (store.get('worker', DEFAULT_WORKER) || '').trim().replace(/\/+$/, ''); }

function parseBook(input) {
  const u = new URL(input.trim());
  const id = u.searchParams.get('id');
  if (!id) throw new Error('В ссылке нет id книги');
  const p = parseInt(u.searchParams.get('p') || '1', 10) || 1;
  return { bookUrl: `${u.origin}/read_book.php?id=${id}`, page: p };
}

async function load(page) {
  const w = workerUrl();
  if (!w) { $('settings').open = true; return setStatus('Сначала впишите адрес Worker в настройках'); }
  setStatus('Загрузка…');
  setNav(false);
  try {
    const target = `${state.bookUrl}&p=${page}`;
    const r = await fetch(`${w}/?url=${encodeURIComponent(target)}`);
    const data = await r.json();
    if (!r.ok || data.error) throw new Error(data.error || 'Ошибка ' + r.status);

    state.page = data.page;
    state.total = data.total;
    $('title').textContent = data.title || '';
    const box = $('text');
    box.innerHTML = '';
    data.paragraphs.forEach((t) => {
      const p = document.createElement('p');
      p.textContent = t;
      box.appendChild(p);
    });
    $('pageinfo').textContent = data.total ? `${data.page} / ${data.total}` : `Стр. ${data.page}`;
    $('prev').disabled = !data.prev;
    $('next').disabled = !data.next;
    store.set('book', JSON.stringify({ bookUrl: state.bookUrl, page: state.page }));
    setStatus('');
    $('reader').hidden = false;
    window.scrollTo(0, 0);
  } catch (e) {
    setStatus('Ошибка: ' + e.message);
  }
  setNav(true);
}

function setStatus(t) { $('status').textContent = t; }
function setNav(on) { if (!on) { $('prev').disabled = true; $('next').disabled = true; } }

function applyFont(size) {
  size = Math.min(30, Math.max(14, size));
  document.documentElement.style.setProperty('--fs', size + 'px');
  store.set('fs', String(size));
}

$('go').addEventListener('click', () => {
  try { Object.assign(state, parseBook($('url').value)); load(state.page); }
  catch (e) { setStatus('Ошибка: ' + e.message); }
});
$('prev').addEventListener('click', () => load(state.page - 1));
$('next').addEventListener('click', () => load(state.page + 1));
$('prev2').addEventListener('click', () => $('prev').click());
$('next2').addEventListener('click', () => $('next').click());
$('fminus').addEventListener('click', () => applyFont(parseInt(store.get('fs', '19'), 10) - 1));
$('fplus').addEventListener('click', () => applyFont(parseInt(store.get('fs', '19'), 10) + 1));
$('theme').addEventListener('click', () => {
  const dark = document.body.classList.toggle('dark');
  store.set('dark', dark ? '1' : '0');
});
$('saveworker').addEventListener('click', () => {
  store.set('worker', $('worker').value.trim());
  setStatus('Адрес сохранён');
});

// Синхронизация кнопок внизу
new MutationObserver(() => {
  $('prev2').disabled = $('prev').disabled;
  $('next2').disabled = $('next').disabled;
}).observe($('prev'), { attributes: true });
new MutationObserver(() => { $('next2').disabled = $('next').disabled; }).observe($('next'), { attributes: true });

// Старт
applyFont(parseInt(store.get('fs', '19'), 10));
if (store.get('dark', '0') === '1') document.body.classList.add('dark');
$('worker').value = workerUrl();
try {
  const saved = JSON.parse(store.get('book', 'null'));
  if (saved) {
    state.bookUrl = saved.bookUrl;
    $('url').value = saved.bookUrl + '&p=' + saved.page;
    if (workerUrl()) load(saved.page);
  }
} catch {}

if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(() => {});
