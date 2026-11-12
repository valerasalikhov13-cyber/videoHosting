// VidSec — фронтенд на чистом JavaScript.
// Весь пользовательский текст выводится через textContent, чтобы исключить XSS.

const app = document.getElementById('app');
const nav = document.getElementById('nav');

// ---------- Сессия ----------
function token() { try { return localStorage.getItem('token'); } catch (e) { return null; } }
function setToken(t) { try { t ? localStorage.setItem('token', t) : localStorage.removeItem('token'); } catch (e) {} }
function me() {
  const t = token();
  if (!t) return null;
  try {
    const p = JSON.parse(atob(t.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
    if (p.exp * 1000 < Date.now()) { setToken(null); return null; }
    return { id: Number(p.sub), name: p.name };
  } catch (e) { setToken(null); return null; }
}

// ---------- HTTP ----------
async function api(method, url, body) {
  const headers = {};
  if (token()) headers['Authorization'] = 'Bearer ' + token();
  if (body && !(body instanceof FormData)) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(body); }
  const r = await fetch(url, { method, headers, body });
  if (r.status === 401 && token()) { setToken(null); renderNav(); }
  if (r.status === 204) return null;
  const text = await r.text();
  let data; try { data = JSON.parse(text); } catch (e) { data = text; }
  if (!r.ok) {
    let msg = data && data.detail ? data.detail : ('HTTP ' + r.status);
    if (Array.isArray(msg)) msg = msg.map(d => d.msg).join('; ');
    throw new Error(msg);
  }
  return data;
}

// ---------- DOM-помощник ----------
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') node.className = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) node.setAttribute(k, v);
  }
  for (const c of children.flat()) if (c != null) node.append(c instanceof Node ? c : String(c));
  return node;
}
function fmtDate(s) { return new Date(s).toLocaleString('ru-RU', { dateStyle: 'medium', timeStyle: 'short' }); }
const STATUS = { processing: 'обрабатывается', ready: 'готово', failed: 'ошибка обработки' };

// ---------- Навигация ----------
function renderNav() {
  const u = me();
  nav.replaceChildren(...[
    el('a', { href: '#/' }, 'Главная'),
    u ? el('a', { href: '#/upload' }, 'Загрузить видео') : null,
    u ? el('a', { href: '#/channel/' + u.id }, 'Мой канал') : null,
    el('a', { href: '#/status' }, 'Состояние системы'),
    el('span', { class: 'spacer' }),
    u ? el('span', { class: 'user' }, u.name) : null,
    u ? el('a', { href: '#/', onclick: () => { setToken(null); renderNav(); } }, 'Выйти')
      : el('a', { href: '#/login' }, 'Войти'),
    u ? null : el('a', { href: '#/register' }, 'Регистрация'),
  ].filter(Boolean));
}

function videoCard(v, extra) {
  const thumb = v.has_thumbnail
    ? el('img', { src: `/api/videos/${v.id}/thumbnail`, alt: '' })
    : STATUS[v.status] || '';
  return el('a', { class: 'card', href: '#/video/' + v.id },
    el('div', { class: 'thumb' }, thumb),
    el('div', { class: 'body' },
      el('div', { class: 'title' }, v.title),
      el('div', { class: 'muted small' }, v.channel_name),
      el('div', { class: 'muted small' },
        `${v.views} просм.`, v.reputation != null ? ` · репутация ${v.reputation}` : '', extra || ''),
      v.status !== 'ready' ? el('span', { class: 'badge ' + v.status }, STATUS[v.status]) : null,
    ));
}

// ---------- Страницы ----------
async function pageHome() {
  const [rec, fresh] = await Promise.all([api('GET', '/api/recommendations'), api('GET', '/api/videos')]);
  app.replaceChildren(
    el('h1', {}, 'Рекомендации'),
    el('p', { class: 'muted small' },
      `Сортировка по формуле ${rec.formula}. Список из кеша Redis: ${rec.cache === 'hit' ? 'да (hit)' : 'нет, посчитан в БД (miss)'}.`),
    rec.items.length ? el('div', { class: 'grid' }, rec.items.map(v => videoCard(v, ` · score ${v.score}`)))
      : el('p', { class: 'muted' }, 'Пока нет видео.'),
    el('h2', {}, 'Новые видео'),
    fresh.length ? el('div', { class: 'grid' }, fresh.map(v => videoCard(v)))
      : el('p', { class: 'muted' }, 'Пока нет видео.'),
  );
}

let pollTimer = null;
async function pageVideo(id) {
  const u = me();
  const v = await api('GET', `/api/videos/${id}`);
  const isOwner = u && u.id === v.channel_id;

  if (v.status === 'processing') {
    pollTimer = setTimeout(() => route(), 3000);
  }
  let views = v.views;
  if (v.status === 'ready') {
    try { views = (await api('POST', `/api/videos/${id}/view`)).views; } catch (e) {}
  }

  const repEl = el('span', { class: 'rep' }, v.reputation);
  const chRepEl = el('span', {}, v.channel_reputation);
  const likeBtn = el('button', {}, '👍 Нравится');
  const dislikeBtn = el('button', {}, '👎 Не нравится');
  const voteMsg = el('span', { class: 'error small' });
  let myVote = v.my_vote;
  function paintVotes() {
    likeBtn.className = myVote === 1 ? 'on-like' : '';
    dislikeBtn.className = myVote === -1 ? 'on-dislike' : '';
  }
  async function sendVote(value) {
    if (!me()) { location.hash = '#/login'; return; }
    try {
      const r = await api('POST', `/api/videos/${id}/vote`, { value: myVote === value ? 0 : value });
      myVote = r.my_vote; repEl.textContent = r.reputation; chRepEl.textContent = r.channel_reputation;
      voteMsg.textContent = ''; paintVotes();
    } catch (e) { voteMsg.textContent = e.message; }
  }
  likeBtn.onclick = () => sendVote(1);
  dislikeBtn.onclick = () => sendVote(-1);
  paintVotes();

  const media = v.status === 'ready'
    ? el('video', { src: `/api/videos/${id}/stream`, controls: '', preload: 'metadata' })
    : el('div', { class: 'panel' }, el('span', { class: 'badge ' + v.status }, STATUS[v.status]),
        v.status === 'processing' ? ' Видео обрабатывается в video-worker, страница обновится сама.' : ' Не удалось обработать файл.');

  const ownerBox = isOwner ? ownerControls(v) : null;

  app.replaceChildren(
    media,
    el('h1', { style: 'margin-top:16px' }, v.title),
    el('div', { class: 'row muted small' },
      el('a', { href: '#/channel/' + v.channel_id }, v.channel_name), ' · репутация канала ', chRepEl,
      ` · ${views} просмотров · ${fmtDate(v.created_at)}`),
    el('div', { class: 'row', style: 'margin:16px 0' }, likeBtn, repEl, dislikeBtn, voteMsg),
    v.description ? el('p', { style: 'white-space:pre-wrap' }, v.description) : null,
    ownerBox,
    el('h2', {}, 'Комментарии'),
    await commentsBlock(id),
  );
}

function ownerControls(v) {
  const box = el('div', { class: 'panel', style: 'margin:16px 0' });
  const title = el('input', { value: v.title, maxlength: 200 });
  const desc = el('textarea', { maxlength: 5000 }); desc.value = v.description;
  const msg = el('div', { class: 'small' });
  box.append(
    el('div', { class: 'muted small', style: 'margin-bottom:8px' }, 'Это ваше видео'),
    el('label', {}, 'Название', title),
    el('label', { style: 'margin-top:8px' }, 'Описание', desc),
    el('div', { class: 'row', style: 'margin-top:12px' },
      el('button', { class: 'primary', onclick: async () => {
        try { await api('PATCH', `/api/videos/${v.id}`, { title: title.value, description: desc.value }); route(); }
        catch (e) { msg.className = 'error small'; msg.textContent = e.message; }
      } }, 'Сохранить'),
      el('button', { class: 'danger', onclick: async () => {
        if (!confirm('Удалить видео без возможности восстановления?')) return;
        try { await api('DELETE', `/api/videos/${v.id}`); location.hash = '#/channel/' + v.channel_id; }
        catch (e) { msg.className = 'error small'; msg.textContent = e.message; }
      } }, 'Удалить видео')),
    msg);
  return box;
}

async function commentsBlock(videoId) {
  const u = me();
  const list = el('div');
  const comments = await api('GET', `/api/videos/${videoId}/comments`);
  function add(c) {
    const node = el('div', { class: 'comment' },
      el('div', { class: 'row' },
        el('a', { class: 'who', href: '#/channel/' + c.author_id }, c.author_name),
        el('span', { class: 'muted small' }, fmtDate(c.created_at)),
        u && u.id === c.author_id ? el('button', { class: 'danger small', onclick: async () => {
          await api('DELETE', `/api/comments/${c.id}`); node.remove();
        } }, 'Удалить') : null),
      el('div', { style: 'white-space:pre-wrap; margin-top:4px' }, c.text));
    list.append(node);
  }
  comments.forEach(add);
  if (!comments.length) list.append(el('p', { class: 'muted' }, 'Комментариев пока нет.'));
  if (!u) return el('div', {}, list, el('p', { class: 'muted' }, el('a', { href: '#/login' }, 'Войдите'), ', чтобы оставить комментарий.'));
  const text = el('textarea', { placeholder: 'Ваш комментарий', maxlength: 2000 });
  const msg = el('div', { class: 'error small' });
  const form = el('form', { class: 'panel', style: 'max-width:none; margin-top:12px', onsubmit: async (e) => {
    e.preventDefault();
    try {
      const c = await api('POST', `/api/videos/${videoId}/comments`, { text: text.value });
      if (!list.querySelector('.comment')) list.replaceChildren();
      add(c); text.value = ''; msg.textContent = '';
    } catch (err) { msg.textContent = err.message; }
  } }, text, el('div', {}, el('button', { class: 'primary' }, 'Отправить')), msg);
  return el('div', {}, list, form);
}

async function pageChannel(id) {
  const ch = await api('GET', `/api/channels/${id}`);
  app.replaceChildren(
    el('h1', {}, 'Канал ' + ch.name),
    el('p', { class: 'muted' }, `Репутация канала: `, el('b', {}, ch.reputation), ` · видео: ${ch.videos.length} · создан ${fmtDate(ch.created_at)}`),
    ch.videos.length ? el('div', { class: 'grid' }, ch.videos.map(v => videoCard(v))) : el('p', { class: 'muted' }, 'На канале пока нет видео.'),
  );
}

function pageUpload() {
  if (!me()) { location.hash = '#/login'; return; }
  const bar = el('div'); const progress = el('div', { class: 'progress', hidden: '' }, bar);
  const msg = el('div', { class: 'small' });
  const form = el('form', { class: 'panel', onsubmit: (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/videos');
    xhr.setRequestHeader('Authorization', 'Bearer ' + token());
    xhr.upload.onprogress = (ev) => { progress.hidden = false; bar.style.width = (100 * ev.loaded / ev.total) + '%'; };
    xhr.onload = () => {
      let data = {}; try { data = JSON.parse(xhr.responseText); } catch (err) {}
      if (xhr.status === 201) location.hash = '#/video/' + data.id;
      else { msg.className = 'error small'; msg.textContent = data.detail ? (Array.isArray(data.detail) ? data.detail.map(d => d.msg).join('; ') : data.detail) : 'HTTP ' + xhr.status; }
    };
    xhr.onerror = () => { msg.className = 'error small'; msg.textContent = 'Ошибка сети'; };
    xhr.send(fd);
  } },
    el('h1', {}, 'Загрузка видео'),
    el('label', {}, 'Название', el('input', { name: 'title', required: '', maxlength: 200 })),
    el('label', {}, 'Описание', el('textarea', { name: 'description', maxlength: 5000 })),
    el('label', {}, 'Видеофайл (до 200 МБ)', el('input', { name: 'file', type: 'file', accept: 'video/*', required: '' })),
    progress, el('div', {}, el('button', { class: 'primary' }, 'Загрузить')), msg);
  app.replaceChildren(form);
}

function pageAuth(mode) {
  const isLogin = mode === 'login';
  const msg = el('div', { class: 'error small' });
  const form = el('form', { class: 'panel', onsubmit: async (e) => {
    e.preventDefault();
    const creds = { username: form.username.value, password: form.password.value };
    try {
      if (!isLogin) await api('POST', '/auth/register', creds);
      const r = await api('POST', '/auth/login', creds);
      setToken(r.access_token); renderNav(); location.hash = '#/';
    } catch (err) { msg.textContent = err.message; }
  } },
    el('h1', {}, isLogin ? 'Вход' : 'Регистрация'),
    el('label', {}, 'Имя пользователя (латиница, цифры, _, от 3 символов)', el('input', { name: 'username', required: '', autocomplete: 'username' })),
    el('label', {}, 'Пароль (от 8 символов)', el('input', { name: 'password', type: 'password', required: '', autocomplete: isLogin ? 'current-password' : 'new-password' })),
    el('div', {}, el('button', { class: 'primary' }, isLogin ? 'Войти' : 'Зарегистрироваться')),
    msg,
    el('p', { class: 'muted small' }, isLogin ? 'Нет аккаунта? ' : 'Уже есть аккаунт? ',
      el('a', { href: isLogin ? '#/register' : '#/login' }, isLogin ? 'Зарегистрироваться' : 'Войти')));
  app.replaceChildren(form);
}

function pageStatus() {
  const out = el('pre', {}, 'Нажмите кнопку, чтобы отправить запрос.');
  async function call(url) {
    out.textContent = 'GET ' + url + ' ...';
    const r = await fetch(url); const body = await r.text();
    let pretty = body; try { pretty = JSON.stringify(JSON.parse(body), null, 2); } catch (e) {}
    out.textContent = `GET ${url}  ->  HTTP ${r.status}\n\n${pretty}`;
  }
  app.replaceChildren(
    el('h1', {}, 'Состояние системы'),
    el('div', { class: 'row', style: 'margin:16px 0' },
      el('button', { onclick: () => call('/api/hello') }, 'api: hello'),
      el('button', { onclick: () => call('/auth/hello') }, 'auth-service: hello'),
      el('button', { onclick: () => call('/api/health') }, 'api: БД, кеш, брокер, хранилище'),
      el('button', { onclick: () => call('/auth/health') }, 'auth-service: БД пользователей')),
    out);
}

// ---------- Роутер ----------
async function route() {
  clearTimeout(pollTimer);
  renderNav();
  const [, page, id] = (location.hash || '#/').split('/');
  try {
    if (page === 'video') await pageVideo(Number(id));
    else if (page === 'channel') await pageChannel(Number(id));
    else if (page === 'upload') pageUpload();
    else if (page === 'login') pageAuth('login');
    else if (page === 'register') pageAuth('register');
    else if (page === 'status') pageStatus();
    else await pageHome();
  } catch (e) {
    app.replaceChildren(el('p', { class: 'error' }, 'Ошибка: ' + e.message));
  }
}
window.addEventListener('hashchange', route);
route();
