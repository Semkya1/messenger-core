/* Клиент мессенджера + API для клиентских плагинов (window.Messenger). */
(() => {
  const $ = (id) => document.getElementById(id);
  const state = {
    token: localStorage.getItem('token'),
    me: null,
    config: { commands: [], client_scripts: [], client_styles: [], max_upload: 25 * 1024 * 1024 },
    chats: [],
    current: null,       // открытый чат
    messages: [],        // загруженные сообщения открытого чата
    hasMore: false,      // есть ли сообщения старше
    hasNewer: false,     // есть ли сообщения новее (после перехода к старому сообщению)
    loading: false,
    typing: {},          // chat_id -> {text, until}
    replyTo: null,       // сообщение, на которое отвечаем
    editing: null,       // сообщение, которое редактируем
    recording: null,
    es: null,
  };
  const listeners = {};
  const decorators = [];
  const actions = [];
  const chatDecorators = [];
  const PAGE = 50;

  // ================================================================ утилиты
  async function api(method, path, body) {
    const res = await fetch(path, {
      method,
      headers: { 'Content-Type': 'application/json', ...(state.token ? { Authorization: 'Bearer ' + state.token } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401 && state.me) logout(true);
    if (!res.ok) throw new Error(data.error || res.statusText);
    return data;
  }

  async function upload(file) {
    if (file.size > state.config.max_upload) {
      throw new Error(`Файл больше ${Math.round(state.config.max_upload / 1024 / 1024)} МБ`);
    }
    const res = await fetch('/api/upload', {
      method: 'POST',
      headers: {
        Authorization: 'Bearer ' + state.token,
        'Content-Type': file.type || 'application/octet-stream',
        'X-File-Name': encodeURIComponent(file.name || 'file'),
      },
      body: file,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || res.statusText);
    return data;
  }

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === 'class') node.className = v;
      else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
      else if (v !== undefined && v !== null && v !== false) node.setAttribute(k, v);
    }
    for (const c of children.flat()) if (c != null && c !== false) node.append(c instanceof Node ? c : String(c));
    return node;
  }

  const COLORS = ['#e17076', '#7bc862', '#65aadd', '#a695e7', '#ee7aae', '#6ec9cb', '#faa774'];
  function avatar(name, id, opts = {}) {
    const a = el('span', { class: 'avatar' + (opts.size ? ' ' + opts.size : '') + (opts.online ? ' online' : '') });
    if (opts.saved) { a.textContent = '🔖'; a.style.background = 'var(--accent)'; return a; }
    if (opts.url) a.style.backgroundImage = `url("${opts.url}")`;
    else {
      a.textContent = (name || '?').trim()[0]?.toUpperCase() || '?';
      a.style.background = COLORS[Math.abs(id || 0) % COLORS.length];
    }
    return a;
  }
  const chatAvatar = (chat, size) => avatar(chat.title, chat.type === 'private' ? chat.peer?.id : -chat.id, {
    size, url: chat.avatar, saved: chat.is_saved, online: chat.type === 'private' && !chat.is_saved && chat.peer?.online,
  });
  const userAvatar = (u, size) => avatar(u.display_name, u.id, { size, url: u.avatar, online: u.online });

  const sameDay = (a, b) => a.toDateString() === b.toDateString();
  function fmtTime(ts) {
    return new Date(ts * 1000).toLocaleTimeString('ru', { hour: '2-digit', minute: '2-digit' });
  }
  function fmtShort(ts) {
    const d = new Date(ts * 1000);
    return sameDay(d, new Date()) ? fmtTime(ts) : d.toLocaleDateString('ru', { day: 'numeric', month: 'short' });
  }
  function fmtDay(ts) {
    const d = new Date(ts * 1000), now = new Date(), y = new Date(Date.now() - 864e5);
    if (sameDay(d, now)) return 'Сегодня';
    if (sameDay(d, y)) return 'Вчера';
    return d.toLocaleDateString('ru', { day: 'numeric', month: 'long', year: d.getFullYear() === now.getFullYear() ? undefined : 'numeric' });
  }
  function fmtDuration(s) {
    s = Math.max(0, Math.round(s || 0));
    return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
  }
  function fmtSize(b) {
    if (b < 1024) return b + ' Б';
    if (b < 1048576) return (b / 1024).toFixed(1) + ' КБ';
    return (b / 1048576).toFixed(1) + ' МБ';
  }
  function lastSeenText(u) {
    if (!u) return '';
    if (u.is_bot) return 'бот';
    if (u.online) return 'в сети';
    if (!u.last_seen) return 'был(а) давно';
    const d = new Date(u.last_seen * 1000);
    return 'был(а) ' + (sameDay(d, new Date()) ? 'в ' + fmtTime(u.last_seen) : fmtShort(u.last_seen) + ' в ' + fmtTime(u.last_seen));
  }
  function previewText(msg) {
    if (!msg) return '';
    const f = msg.meta?.file;
    const prefix = { voice: '🎤 Голосовое сообщение', image: '🖼 Фото', video: '🎬 Видео', file: '📎 ' + (f?.name || 'Файл') }[msg.type];
    if (prefix) return msg.text ? prefix.split(' ')[0] + ' ' + msg.text : prefix;
    return msg.text;
  }

  // Текст со ссылками (без innerHTML — безопасно)
  function richText(text) {
    const frag = document.createDocumentFragment();
    let last = 0;
    for (const m of text.matchAll(/https?:\/\/[^\s<>"]+/g)) {
      const url = m[0].replace(/[.,!?;:)\]]+$/, '');
      frag.append(text.slice(last, m.index));
      frag.append(el('a', { href: url, target: '_blank', rel: 'noopener noreferrer' }, url));
      last = m.index + url.length;
    }
    frag.append(text.slice(last));
    return frag;
  }

  function toast(text, ms = 2500) {
    const t = $('toast');
    t.textContent = text;
    t.classList.remove('hidden');
    clearTimeout(toast._t);
    toast._t = setTimeout(() => t.classList.add('hidden'), ms);
  }

  function emit(event, data) {
    for (const fn of listeners[event] || []) {
      try { fn(data); } catch (e) { console.error('[plugin]', event, e); }
    }
  }

  // ================================================================ API для плагинов
  window.Messenger = {
    api,                                  // запрос к серверу: api('POST', '/api/...', {...})
    upload,                               // загрузить файл: upload(file) -> {id, url, name, mime, size}
    el,                                   // создать DOM-элемент
    toast,                                // всплывающее уведомление
    get me() { return state.me; },
    get currentChat() { return state.current; },
    get chats() { return state.chats; },
    get messages() { return state.messages; },
    /** События: серверные (message:new, typing, свои события плагинов) и клиентские (ready, chat:open). */
    on(event, fn) { (listeners[event] ||= []).push(fn); },
    /** Изменить отрисовку сообщения: fn(msg, element). */
    addMessageDecorator(fn) { decorators.push(fn); },
    /** Пункт в меню сообщения (правый клик): fn(msg, element). filter(msg) — показывать ли пункт. */
    addMessageAction(label, fn, filter) { actions.push({ label, fn, filter }); },
    /** Изменить отрисовку чата в списке: fn(chat, element). */
    addChatDecorator(fn) { chatDecorators.push(fn); },
    /** Кнопка в шапке открытого чата. */
    addHeaderButton(label, title, fn) {
      const b = el('button', { title, onclick: () => fn(state.current) }, label);
      $('header-buttons').prepend(b);
      return b;
    },
    /** Кнопка в шапке списка чатов. */
    addSidebarButton(label, title, fn) {
      const b = el('button', { title, onclick: fn }, label);
      $('sidebar-buttons').prepend(b);
      return b;
    },
    sendMessage(chatId, text) { return api('POST', `/api/chats/${chatId}/messages`, { text }); },
    openChat: (id) => openChat(id),
    openProfile: (id) => openProfile(id),
    rerender: () => { renderChats(); renderMessages(false); },
    modal: (...a) => modal(...a),
  };

  // ================================================================ авторизация
  let registerMode = false;
  $('auth-toggle').onclick = (e) => {
    e.preventDefault();
    registerMode = !registerMode;
    $('auth-display').classList.toggle('hidden', !registerMode);
    $('auth-submit').textContent = registerMode ? 'Зарегистрироваться' : 'Войти';
    $('auth-toggle').textContent = registerMode ? 'Уже есть аккаунт? Войти' : 'Нет аккаунта? Регистрация';
    $('auth-error').textContent = '';
  };

  $('auth-form').onsubmit = async (e) => {
    e.preventDefault();
    try {
      const body = { username: $('auth-username').value.trim(), password: $('auth-password').value };
      if (registerMode) body.display_name = $('auth-display').value.trim();
      const res = await api('POST', registerMode ? '/api/register' : '/api/login', body);
      state.token = res.token;
      localStorage.setItem('token', res.token);
      await start();
    } catch (err) {
      $('auth-error').textContent = err.message;
    }
  };

  async function logout(silent) {
    if (!silent) { try { await api('POST', '/api/logout'); } catch {} }
    localStorage.removeItem('token');
    location.reload();
  }

  // ================================================================ старт
  async function start() {
    try {
      state.me = await api('GET', '/api/me');
    } catch {
      localStorage.removeItem('token');
      state.token = null;
      $('auth').classList.remove('hidden');
      return;
    }
    $('auth').classList.add('hidden');
    $('main').classList.remove('hidden');
    renderMe();
    state.config = await api('GET', '/api/config');
    await loadPlugins();
    connectEvents();
    await loadChats();
    emit('ready', state.me);
  }

  function renderMe() {
    const a = avatar(state.me.display_name, state.me.id, { url: state.me.avatar });
    a.id = 'me-avatar';
    $('me-avatar').replaceWith(a);
    $('me-name').textContent = state.me.display_name;
  }

  function loadPlugins() {
    for (const css of state.config.client_styles) {
      document.head.append(el('link', { rel: 'stylesheet', href: '/plugins/' + css }));
    }
    return Promise.all(state.config.client_scripts.map((src) => new Promise((resolve) => {
      const s = el('script', { src: '/plugins/' + src });
      s.onload = resolve;
      s.onerror = () => { console.error('Не загрузился плагин', src); resolve(); };
      document.body.append(s);
    })));
  }

  // ================================================================ realtime
  function connectEvents() {
    if (state.es) state.es.close();
    const es = new EventSource('/api/events?token=' + encodeURIComponent(state.token));
    state.es = es;
    es.onmessage = (e) => {
      const { event, data } = JSON.parse(e.data);
      handleEvent(event, data);
      emit(event, data);
    };
    // EventSource сам переподключается; после переподключения обновим данные
    es.onopen = () => { if (state.chats.length) loadChats(); };
  }

  function handleEvent(event, data) {
    const chat = state.chats.find((c) => c.id === data.chat_id);
    switch (event) {
      case 'message:new': {
        if (!chat) { loadChats(); return; }
        chat.last_message = data;
        state.chats.sort((a, b) => (b.last_message?.id || 0) - (a.last_message?.id || 0));
        const mine = data.sender?.id === state.me.id;
        if (data.sender) delete state.typing[data.chat_id];
        const isOpen = state.current?.id === data.chat_id;
        if (isOpen && !state.hasNewer) {
          const box = $('messages');
          const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 120;
          appendMessage(data);
          if (atBottom || mine) scrollToBottom();
          else updateScrollButton(1);
        }
        if (!mine && (!isOpen || document.hidden || state.hasNewer)) {
          chat.unread = (chat.unread || 0) + 1;
          notify(chat, data);
        }
        if (isOpen) markRead();
        renderChats();
        renderHeader();
        break;
      }
      case 'message:update': {
        const i = state.messages.findIndex((m) => m.id === data.id);
        if (i >= 0) { state.messages[i] = data; replaceMessage(data); }
        if (chat?.last_message?.id === data.id) { chat.last_message = data; renderChats(); }
        if (chat?.pinned?.id === data.id) { chat.pinned = data; renderPinned(); }
        break;
      }
      case 'message:deleted': {
        if (state.current?.id === data.chat_id) {
          state.messages = state.messages.filter((m) => m.id !== data.id);
          renderMessages(false);
        }
        loadChats();
        break;
      }
      case 'read': {
        if (!chat) return;
        const key = String(data.user_id);
        chat.read_state[key] = Math.max(chat.read_state[key] || 0, data.message_id);
        if (data.user_id === state.me.id) chat.unread = 0;
        if (state.current?.id === chat.id) updateTicks();
        renderChats();
        break;
      }
      case 'chat:new':
      case 'chat:update':
        loadChats();
        break;
      case 'chat:removed':
        state.chats = state.chats.filter((c) => c.id !== data.chat_id);
        if (state.current?.id === data.chat_id) closeChat();
        renderChats();
        break;
      case 'presence':
        for (const c of state.chats) {
          for (const m of c.members) if (m.id === data.user_id) { m.online = data.online; if (data.last_seen) m.last_seen = data.last_seen; }
          if (c.peer?.id === data.user_id) { c.peer.online = data.online; if (data.last_seen) c.peer.last_seen = data.last_seen; }
        }
        renderChats();
        renderHeader();
        break;
      case 'user:update':
        if (data.id === state.me.id) { state.me = data; renderMe(); }
        loadChats();
        break;
      case 'typing': {
        const text = data.user.display_name + (data.action === 'recording' ? ' записывает голосовое…' : ' печатает…');
        state.typing[data.chat_id] = { text, until: Date.now() + 4000 };
        renderChats();
        renderHeader();
        setTimeout(() => { renderChats(); renderHeader(); }, 4100);
        break;
      }
    }
  }

  // ---------------------------------------------------------------- уведомления
  function notify(chat, msg) {
    if (chat.muted || localStorage.getItem('notify') !== '1') return;
    if (!('Notification' in window) || Notification.permission !== 'granted') return;
    if (!document.hidden && state.current?.id === chat.id) return;
    const n = new Notification(chat.title, {
      body: (chat.type === 'group' && msg.sender ? msg.sender.display_name + ': ' : '') + previewText(msg),
      tag: 'chat-' + chat.id,
      icon: chat.avatar || undefined,
    });
    n.onclick = () => { window.focus(); openChat(chat.id); n.close(); };
  }

  function markRead() {
    const chat = state.current;
    if (!chat || document.hidden || state.hasNewer || !state.messages.length) return;
    const lastId = state.messages[state.messages.length - 1].id;
    const key = String(state.me.id);
    if ((chat.read_state[key] || 0) >= lastId && !chat.unread) return;
    chat.read_state[key] = lastId;
    chat.unread = 0;
    api('POST', `/api/chats/${chat.id}/read`, { message_id: lastId }).catch(() => {});
    renderChats();
  }
  document.addEventListener('visibilitychange', markRead);

  // ================================================================ список чатов
  async function loadChats() {
    state.chats = await api('GET', '/api/chats');
    if (state.current) {
      const fresh = state.chats.find((c) => c.id === state.current.id);
      if (fresh) state.current = fresh;
      else closeChat();
    }
    renderChats();
    renderHeader();
    renderPinned();
    if (state.current) updateTicks();
  }

  function typingText(chatId) {
    const t = state.typing[chatId];
    return t && t.until > Date.now() ? t.text : null;
  }

  function renderChats() {
    const list = $('chat-list');
    list.replaceChildren();
    if (!state.chats.length) {
      list.append(el('div', { class: 'item-sub', style: 'padding:16px' }, 'Чатов пока нет. Найдите человека или бота через поиск (например, dicebot).'));
    }
    for (const chat of state.chats) {
      const lm = chat.last_message;
      const typing = typingText(chat.id);
      let preview = typing;
      if (!preview && lm) {
        const who = lm.sender ? (lm.sender.id === state.me.id && !chat.is_saved ? 'Вы: ' : chat.type === 'group' ? lm.sender.display_name + ': ' : '') : '';
        preview = who + previewText(lm);
      }
      const icon = chat.type === 'group' ? '👥 ' : chat.peer?.is_bot ? '🤖 ' : '';
      const item = el('div', { class: 'item' + (state.current?.id === chat.id ? ' active' : ''), onclick: () => openChat(chat.id) },
        chatAvatar(chat),
        el('div', { class: 'item-body' },
          el('div', { class: 'item-top' },
            el('span', { class: 'item-title' }, icon + chat.title + (chat.muted ? ' 🔕' : '')),
            el('span', { class: 'item-time' }, lm ? fmtShort(lm.created_at) : '')),
          el('div', { class: 'item-top' },
            el('span', { class: 'item-sub' + (typing ? ' accent' : '') }, preview || 'Нет сообщений'),
            chat.unread ? el('span', { class: 'badge' + (chat.muted ? ' muted-badge' : '') }, chat.unread) : null)));
      for (const fn of chatDecorators) { try { fn(chat, item); } catch (e) { console.error(e); } }
      list.append(item);
    }
    const total = state.chats.reduce((s, c) => s + (c.muted ? 0 : c.unread || 0), 0);
    document.title = total ? `(${total}) Мессенджер` : 'Мессенджер';
  }

  // ================================================================ поиск людей
  let searchTimer;
  $('search').oninput = () => {
    clearTimeout(searchTimer);
    const q = $('search').value.trim();
    if (!q) { $('search-results').classList.add('hidden'); return; }
    searchTimer = setTimeout(async () => {
      const users = await api('GET', '/api/users?q=' + encodeURIComponent(q));
      const box = $('search-results');
      box.replaceChildren(...(users.length ? users.map((u) =>
        el('div', { class: 'item', onclick: () => startPrivate(u.id) },
          userAvatar(u),
          el('div', { class: 'item-body' },
            el('div', { class: 'item-title' }, (u.is_bot ? '🤖 ' : '') + u.display_name),
            el('div', { class: 'item-sub' }, '@' + u.username)))) :
        [el('div', { class: 'item-sub', style: 'padding:12px' }, 'Никого не найдено')]));
      box.classList.remove('hidden');
    }, 250);
  };

  async function startPrivate(userId) {
    const chat = await api('POST', '/api/chats', { type: 'private', user_id: userId });
    $('search').value = '';
    $('search-results').classList.add('hidden');
    closeModal();
    await loadChats();
    openChat(chat.id);
  }

  $('open-saved').onclick = async () => {
    const chat = await api('POST', '/api/chats', { type: 'saved' });
    await loadChats();
    openChat(chat.id);
  };

  // ================================================================ модалка
  function modal(title, body, onOk, okText = 'OK') {
    $('modal-title').textContent = title;
    $('modal-body').replaceChildren(body);
    $('modal-ok').textContent = okText;
    $('modal-ok').classList.toggle('hidden', !onOk);
    $('modal-cancel').textContent = onOk ? 'Отмена' : 'Закрыть';
    $('modal').classList.remove('hidden');
    $('modal-cancel').onclick = closeModal;
    $('modal-ok').onclick = async () => {
      try { if ((await onOk()) !== false) closeModal(); } catch (e) { toast(e.message); }
    };
  }
  function closeModal() { $('modal').classList.add('hidden'); }
  $('modal').addEventListener('mousedown', (e) => { if (e.target === $('modal')) closeModal(); });

  function pickFile(accept) {
    return new Promise((resolve) => {
      const input = el('input', { type: 'file', accept });
      input.onchange = () => resolve(input.files[0] || null);
      input.click();
    });
  }

  async function pickUsers(exclude = []) {
    const users = (await api('GET', '/api/users?q=')).filter((u) => !exclude.includes(u.id));
    const box = el('div', {});
    for (const u of users) {
      box.append(el('label', { class: 'check-item' },
        el('input', { type: 'checkbox', value: u.id }),
        userAvatar(u, 'small'),
        (u.is_bot ? '🤖 ' : '') + u.display_name));
    }
    if (!users.length) box.append(el('div', { class: 'muted' }, 'Нет пользователей'));
    return { box, selected: () => [...box.querySelectorAll('input:checked')].map((i) => +i.value) };
  }

  $('new-group').onclick = async () => {
    const title = el('input', { placeholder: 'Название группы' });
    const picker = await pickUsers();
    modal('Новая группа', el('div', { style: 'display:flex;flex-direction:column;gap:10px' },
      title, el('div', { class: 'section-title' }, 'Участники'), picker.box), async () => {
      const chat = await api('POST', '/api/chats', { type: 'group', title: title.value, member_ids: picker.selected() });
      await loadChats();
      openChat(chat.id);
    }, 'Создать');
    title.focus();
  };

  // ================================================================ настройки профиля
  function field(label, input) { return el('label', { class: 'field' }, label, input); }

  function openSettings() {
    const me = state.me;
    let avatarFileId;  // undefined — не менять, null — удалить
    const av = el('div', {}, avatar(me.display_name, me.id, { size: 'big', url: me.avatar }));
    const name = el('input', { value: me.display_name, maxlength: 64 });
    const username = el('input', { value: me.username, maxlength: 32 });
    const bio = el('textarea', { maxlength: 200, placeholder: 'Пара слов о себе' }, me.bio || '');

    const changePhoto = el('button', { class: 'link', onclick: async () => {
      const file = await pickFile('image/png,image/jpeg,image/gif,image/webp');
      if (!file) return;
      try {
        const f = await upload(file);
        avatarFileId = f.id;
        av.replaceChildren(avatar(me.display_name, me.id, { size: 'big', url: f.url }));
      } catch (e) { toast(e.message); }
    } }, 'Изменить фото');
    const removePhoto = el('button', { class: 'link', onclick: () => {
      avatarFileId = null;
      av.replaceChildren(avatar(name.value, me.id, { size: 'big' }));
    } }, 'Удалить фото');

    const theme = el('input', { type: 'checkbox' });
    theme.checked = document.documentElement.dataset.theme === 'dark';
    theme.onchange = () => {
      const t = theme.checked ? 'dark' : 'light';
      document.documentElement.dataset.theme = t;
      localStorage.setItem('theme', t);
    };
    const notifyBox = el('input', { type: 'checkbox' });
    notifyBox.checked = localStorage.getItem('notify') === '1' && window.Notification?.permission === 'granted';
    notifyBox.onchange = async () => {
      if (notifyBox.checked) {
        if (!('Notification' in window)) { toast('Браузер не поддерживает уведомления'); notifyBox.checked = false; return; }
        const perm = await Notification.requestPermission();
        if (perm !== 'granted') { toast('Уведомления запрещены в браузере'); notifyBox.checked = false; return; }
      }
      localStorage.setItem('notify', notifyBox.checked ? '1' : '0');
    };

    const oldPass = el('input', { type: 'password', placeholder: 'Текущий пароль', autocomplete: 'current-password' });
    const newPass = el('input', { type: 'password', placeholder: 'Новый пароль', autocomplete: 'new-password' });
    const changePass = el('button', { class: 'secondary', onclick: async () => {
      try {
        await api('POST', '/api/me/password', { old_password: oldPass.value, new_password: newPass.value });
        oldPass.value = newPass.value = '';
        toast('Пароль изменён. Остальные устройства разлогинены.');
      } catch (e) { toast(e.message); }
    } }, 'Сменить пароль');

    const body = el('div', { style: 'display:flex;flex-direction:column;gap:10px' },
      el('div', { class: 'profile-head' }, av, el('div', {}, changePhoto, ' · ', removePhoto)),
      field('Имя', name),
      field('Логин (@username)', username),
      field('О себе', bio),
      el('div', { class: 'section-title' }, 'Оформление и уведомления'),
      el('label', { class: 'check-item' }, theme, 'Тёмная тема'),
      el('label', { class: 'check-item' }, notifyBox, 'Уведомления о новых сообщениях'),
      el('div', { class: 'section-title' }, 'Безопасность'),
      oldPass, newPass, changePass,
      el('div', { class: 'section-title' }, 'Аккаунт'),
      el('button', { class: 'danger', onclick: () => logout() }, 'Выйти из аккаунта'));

    modal('Настройки', body, async () => {
      const patch = { display_name: name.value, username: username.value, bio: bio.value };
      if (avatarFileId !== undefined) patch.avatar_file_id = avatarFileId;
      state.me = await api('PATCH', '/api/me', patch);
      renderMe();
      toast('Профиль сохранён');
    }, 'Сохранить');
  }
  $('me-button').onclick = openSettings;
  $('open-settings').onclick = openSettings;

  // ================================================================ профили и инфо о чате
  async function openProfile(userId) {
    if (userId === state.me.id) return openSettings();
    const u = await api('GET', `/api/users/${userId}`);
    const body = el('div', {},
      el('div', { class: 'profile-head' },
        avatar(u.display_name, u.id, { size: 'big', url: u.avatar, online: u.online }),
        el('div', { class: 'profile-name' }, (u.is_bot ? '🤖 ' : '') + u.display_name),
        el('div', { class: 'muted' }, lastSeenText(u))),
      el('div', { class: 'profile-row' }, el('span', {}, '@' + u.username), el('span', { class: 'muted' }, 'Имя пользователя')),
      u.bio ? el('div', { class: 'profile-row' }, el('span', {}, u.bio), el('span', { class: 'muted' }, 'О себе')) : null,
      el('button', { onclick: () => startPrivate(u.id), style: 'width:100%;margin-top:8px' }, 'Написать сообщение'));
    modal('Профиль', body, null);
  }

  function muteToggle(chat) {
    const box = el('input', { type: 'checkbox' });
    box.checked = chat.muted;
    box.onchange = async () => {
      await api('POST', `/api/chats/${chat.id}/mute`, { muted: box.checked });
      chat.muted = box.checked;
      renderChats();
    };
    return el('label', { class: 'check-item' }, box, 'Без звука (не уведомлять)');
  }

  function openChatInfo() {
    const chat = state.current;
    if (!chat) return;
    if (chat.type === 'private' && !chat.is_saved) return openProfileWithMute(chat);
    if (chat.is_saved) {
      return modal('Избранное', el('div', { class: 'profile-head' }, chatAvatar(chat, 'big'),
        el('div', { class: 'muted' }, 'Сохраняйте сюда сообщения, заметки и файлы. Их видите только вы.')), null);
    }
    const isOwner = chat.owner_id === state.me.id;
    let avatarFileId;
    const av = el('div', {}, chatAvatar(chat, 'big'));
    const title = el('input', { value: chat.title, maxlength: 64, disabled: !isOwner });
    const members = el('div', {});
    for (const m of chat.members) {
      members.append(el('div', { class: 'member-row', onclick: () => openProfile(m.id) },
        userAvatar(m, 'small'),
        el('div', { class: 'item-body' },
          el('div', {}, m.display_name + (m.id === chat.owner_id ? ' 👑' : '')),
          el('div', { class: 'muted' }, lastSeenText(m))),
        isOwner && m.id !== state.me.id ? el('button', { class: 'icon', title: 'Удалить из группы', onclick: async (e) => {
          e.stopPropagation();
          if (!confirm(`Удалить ${m.display_name} из группы?`)) return;
          try { await api('DELETE', `/api/chats/${chat.id}/members/${m.id}`); closeModal(); } catch (err) { toast(err.message); }
        } }, '✕') : null));
    }
    const body = el('div', { style: 'display:flex;flex-direction:column;gap:10px' },
      el('div', { class: 'profile-head' }, av,
        isOwner ? el('button', { class: 'link', onclick: async () => {
          const file = await pickFile('image/png,image/jpeg,image/gif,image/webp');
          if (!file) return;
          try { const f = await upload(file); avatarFileId = f.id; av.replaceChildren(avatar(chat.title, -chat.id, { size: 'big', url: f.url })); }
          catch (e) { toast(e.message); }
        } }, 'Изменить фото группы') : null),
      field('Название', title),
      muteToggle(chat),
      el('div', { class: 'row-between' },
        el('div', { class: 'section-title' }, `Участники (${chat.members.length})`),
        el('button', { class: 'link', onclick: addMembers }, '＋ Добавить')),
      members,
      el('button', { class: 'danger', onclick: leaveGroup }, 'Выйти из группы'));
    modal('Группа', body, isOwner ? async () => {
      const patch = {};
      if (title.value.trim() !== chat.title) patch.title = title.value;
      if (avatarFileId !== undefined) patch.avatar_file_id = avatarFileId;
      if (Object.keys(patch).length) await api('PATCH', `/api/chats/${chat.id}`, patch);
    } : null, 'Сохранить');
  }

  async function openProfileWithMute(chat) {
    await openProfile(chat.peer.id);
    $('modal-body').append(muteToggle(chat));
  }

  async function addMembers() {
    const chat = state.current;
    const picker = await pickUsers(chat.members.map((m) => m.id));
    modal('Добавить участников', picker.box, async () => {
      for (const id of picker.selected()) await api('POST', `/api/chats/${chat.id}/members`, { user_id: id });
    }, 'Добавить');
  }

  async function leaveGroup() {
    if (!confirm('Выйти из группы?')) return;
    try { await api('POST', `/api/chats/${state.current.id}/leave`); closeModal(); } catch (e) { toast(e.message); }
  }

  $('chat-info').onclick = openChatInfo;

  // ================================================================ открытый чат
  async function openChat(id) {
    const chat = state.chats.find((c) => c.id === id);
    if (!chat) return;
    if (state.current?.id !== id) cancelContext();
    state.current = chat;
    $('empty').classList.add('hidden');
    $('chat-view').classList.remove('hidden');
    $('main').classList.add('chat-open');
    $('chat-search').classList.add('hidden');
    renderChats();
    renderHeader();
    renderPinned();
    await loadLatest();
    $('input').focus();
    emit('chat:open', chat);
  }

  async function loadLatest() {
    const chat = state.current;
    const msgs = await api('GET', `/api/chats/${chat.id}/messages?limit=${PAGE}`);
    if (state.current !== chat) return;
    state.messages = msgs;
    state.hasMore = msgs.length >= PAGE;
    state.hasNewer = false;
    renderMessages(true);
    markRead();
  }

  function closeChat() {
    state.current = null;
    $('chat-view').classList.add('hidden');
    $('empty').classList.remove('hidden');
    $('main').classList.remove('chat-open');
    renderChats();
  }
  $('back').onclick = closeChat;

  function renderHeader() {
    const chat = state.current;
    if (!chat) return;
    const av = chatAvatar(chat);
    av.id = 'chat-avatar';
    $('chat-avatar').replaceWith(av);
    $('chat-title').textContent = chat.title + (chat.muted ? ' 🔕' : '');
    const typing = typingText(chat.id);
    let sub;
    if (typing) sub = typing;
    else if (chat.is_saved) sub = 'ваши заметки';
    else if (chat.type === 'group') sub = `${chat.members.length} участн. · ${chat.members.filter((m) => m.online).length} в сети`;
    else sub = lastSeenText(chat.peer);
    $('chat-subtitle').textContent = sub;
    $('chat-subtitle').classList.toggle('accent', !!typing || (chat.type === 'private' && !!chat.peer?.online));
  }

  function renderPinned() {
    const chat = state.current;
    const p = chat?.pinned;
    $('pinned').classList.toggle('hidden', !p);
    if (p) $('pinned-text').textContent = previewText(p);
  }
  $('pinned').onclick = (e) => { if (e.target !== $('unpin') && state.current?.pinned) jumpTo(state.current.pinned.id); };
  $('unpin').onclick = () => api('POST', `/api/chats/${state.current.id}/pin`, { message_id: null }).catch((e) => toast(e.message));

  // ---------------------------------------------------------------- сообщения
  function isReadByOthers(msg) {
    const chat = state.current;
    if (!chat || chat.is_saved) return true;
    return Object.entries(chat.read_state).some(([uid, last]) => +uid !== state.me.id && last >= msg.id);
  }

  function renderMessages(scrollToEnd) {
    const box = $('messages');
    box.replaceChildren();
    let prev = null;
    for (const msg of state.messages) {
      if (!prev || !sameDay(new Date(prev.created_at * 1000), new Date(msg.created_at * 1000))) {
        box.append(el('div', { class: 'date-sep' }, fmtDay(msg.created_at)));
      }
      box.append(renderMessage(msg));
      prev = msg;
    }
    if (scrollToEnd) scrollToBottom();
    updateScrollButton();
  }

  function appendMessage(msg) {
    if (state.messages.some((m) => m.id === msg.id)) return;
    const prev = state.messages[state.messages.length - 1];
    state.messages.push(msg);
    const box = $('messages');
    if (!prev || !sameDay(new Date(prev.created_at * 1000), new Date(msg.created_at * 1000))) {
      box.append(el('div', { class: 'date-sep' }, fmtDay(msg.created_at)));
    }
    box.append(renderMessage(msg));
  }

  function replaceMessage(msg) {
    const old = $('messages').querySelector(`.msg[data-id="${msg.id}"]`);
    if (old) old.replaceWith(renderMessage(msg));
  }

  function updateTicks() {
    for (const msg of state.messages) {
      const t = $('messages').querySelector(`.msg[data-id="${msg.id}"] .ticks`);
      if (t) t.textContent = isReadByOthers(msg) ? '✓✓' : '✓';
    }
  }

  function scrollToBottom() {
    const box = $('messages');
    box.scrollTop = box.scrollHeight;
    // картинки догружаются позже — докрутим ещё раз
    setTimeout(() => { box.scrollTop = box.scrollHeight; }, 150);
  }

  let pendingBelow = 0;
  function updateScrollButton(add = 0) {
    const box = $('messages');
    const far = box.scrollHeight - box.scrollTop - box.clientHeight > 300 || state.hasNewer;
    pendingBelow = far ? pendingBelow + add : 0;
    $('scroll-down').classList.toggle('hidden', !far);
    $('scroll-badge').textContent = pendingBelow;
    $('scroll-badge').classList.toggle('hidden', !pendingBelow);
  }
  $('scroll-down').onclick = async () => {
    pendingBelow = 0;
    if (state.hasNewer) await loadLatest(); else scrollToBottom();
    updateScrollButton();
  };

  $('messages').addEventListener('scroll', async () => {
    const box = $('messages');
    updateScrollButton();
    if (state.loading || !state.current) return;
    const chat = state.current;
    if (box.scrollTop < 150 && state.hasMore && state.messages.length) {
      state.loading = true;
      try {
        const older = await api('GET', `/api/chats/${chat.id}/messages?before=${state.messages[0].id}&limit=${PAGE}`);
        if (state.current !== chat) return;
        state.hasMore = older.length >= PAGE;
        const prevHeight = box.scrollHeight;
        state.messages = [...older, ...state.messages];
        renderMessages(false);
        box.scrollTop += box.scrollHeight - prevHeight;
      } finally { state.loading = false; }
    } else if (state.hasNewer && box.scrollHeight - box.scrollTop - box.clientHeight < 150) {
      state.loading = true;
      try {
        const newer = await api('GET', `/api/chats/${chat.id}/messages?after=${state.messages[state.messages.length - 1].id}&limit=${PAGE}`);
        if (state.current !== chat) return;
        state.hasNewer = newer.length >= PAGE;
        for (const m of newer) appendMessage(m);
        markRead();
      } finally { state.loading = false; }
    }
  });

  async function jumpTo(messageId) {
    const chat = state.current;
    let row = $('messages').querySelector(`.msg[data-id="${messageId}"]`);
    if (!row) {
      const msgs = await api('GET', `/api/chats/${chat.id}/messages?around=${messageId}`);
      if (state.current !== chat) return;
      state.messages = msgs;
      state.hasMore = true;
      state.hasNewer = msgs.filter((m) => m.id > messageId).length >= 30;
      renderMessages(false);
      row = $('messages').querySelector(`.msg[data-id="${messageId}"]`);
    }
    if (!row) return toast('Сообщение не найдено');
    row.scrollIntoView({ block: 'center' });
    row.classList.add('highlight');
    setTimeout(() => row.classList.remove('highlight'), 1500);
    updateScrollButton();
  }

  // ---------------------------------------------------------------- голосовой плеер
  let playingAudio = null;
  function voicePlayer(file) {
    const audio = new Audio(file.url);
    audio.preload = 'metadata';
    const total = () => (isFinite(audio.duration) && audio.duration > 0 ? audio.duration : file.duration || 0);
    const btn = el('button', { class: 'voice-btn', type: 'button' }, '▶');
    const fill = el('div', { class: 'voice-fill' });
    const bar = el('div', { class: 'voice-bar' }, fill);
    const time = el('div', { class: 'voice-time' }, fmtDuration(file.duration));
    btn.onclick = () => {
      if (audio.paused) {
        if (playingAudio && playingAudio !== audio) playingAudio.pause();
        playingAudio = audio;
        audio.play().catch(() => toast('Не удалось воспроизвести'));
      } else audio.pause();
    };
    audio.onplay = () => { btn.textContent = '❚❚'; };
    audio.onpause = () => { btn.textContent = '▶'; };
    audio.onended = () => { btn.textContent = '▶'; fill.style.width = '0'; time.textContent = fmtDuration(total()); };
    audio.ontimeupdate = () => {
      const t = total();
      fill.style.width = t ? Math.min(100, (audio.currentTime / t) * 100) + '%' : '0';
      time.textContent = fmtDuration(audio.currentTime) + ' / ' + fmtDuration(t);
    };
    bar.onclick = (e) => {
      const r = bar.getBoundingClientRect();
      const t = total();
      if (t) audio.currentTime = ((e.clientX - r.left) / r.width) * t;
    };
    return el('div', { class: 'voice' }, btn, el('div', { class: 'voice-body' }, bar, time));
  }

  function renderMedia(msg) {
    const f = msg.meta?.file;
    if (!f) return null;
    switch (msg.type) {
      case 'image':
        return el('img', { class: 'media-img', src: f.url, alt: f.name, loading: 'lazy', onclick: () => openLightbox(f.url) });
      case 'video':
        return el('video', { class: 'media-video', src: f.url, controls: true, preload: 'metadata' });
      case 'voice':
        return voicePlayer(f);
      default:
        return el('a', { class: 'file-box', href: f.url, download: f.name, target: '_blank', rel: 'noopener' },
          el('span', { class: 'file-icon' }, '📄'),
          el('span', {}, el('div', { class: 'file-name' }, f.name), el('div', { class: 'muted' }, fmtSize(f.size))));
    }
  }

  function openLightbox(url) {
    $('lightbox-img').src = url;
    $('lightbox').classList.remove('hidden');
  }
  $('lightbox').onclick = () => $('lightbox').classList.add('hidden');

  function renderMessage(msg) {
    const mine = msg.sender?.id === state.me.id;
    const isGroup = state.current?.type === 'group';
    const row = el('div', { class: 'msg' + (mine ? ' mine' : '') + (msg.type === 'system' ? ' system' : '') });
    row.dataset.id = msg.id;
    if (msg.type === 'system') {
      row.append(el('div', { class: 'bubble' }, msg.text));
      for (const fn of decorators) { try { fn(msg, row); } catch (e) { console.error(e); } }
      return row;
    }
    const showName = !mine && msg.sender && (isGroup || msg.sender.is_bot);
    const bubble = el('div', { class: 'bubble' },
      msg.forwarded_from ? el('div', { class: 'forwarded' }, '↪ Переслано от ' + msg.forwarded_from) : null,
      showName ? el('div', { class: 'sender', onclick: () => openProfile(msg.sender.id) }, msg.sender.display_name) : null,
      msg.reply ? el('div', { class: 'quote', onclick: () => !msg.reply.deleted && jumpTo(msg.reply.id) },
        el('div', { class: 'quote-name' }, msg.reply.sender_name || 'Сообщение'),
        el('div', { class: 'quote-text' }, msg.reply.deleted ? msg.reply.text : previewText({ ...msg.reply, meta: { file: { name: msg.reply.file_name } } }))) : null,
      renderMedia(msg),
      msg.text ? el('div', { class: 'text' }, richText(msg.text)) : null,
      el('div', { class: 'msg-footer' },
        msg.edited ? el('span', {}, 'изменено') : null,
        el('span', { class: 'time' }, fmtTime(msg.created_at)),
        mine ? el('span', { class: 'ticks' }, isReadByOthers(msg) ? '✓✓' : '✓') : null));
    if (!mine && msg.sender && isGroup) {
      const a = userAvatar({ ...msg.sender, online: false }, 'small');
      a.onclick = () => openProfile(msg.sender.id);
      row.append(a);
    }
    row.append(bubble);
    row.append(el('div', { class: 'msg-actions' },
      el('button', { title: 'Ответить', onclick: () => startReply(msg) }, '↩'),
      el('button', { title: 'Ещё', onclick: (e) => { e.stopPropagation(); openContextMenu(msg, e.clientX, e.clientY, row); } }, '⋯')));
    row.addEventListener('contextmenu', (e) => { e.preventDefault(); openContextMenu(msg, e.clientX, e.clientY, row); });
    row.addEventListener('dblclick', (e) => { if (!e.target.closest('a,button,img,video')) startReply(msg); });
    for (const fn of decorators) { try { fn(msg, row); } catch (e) { console.error(e); } }
    return row;
  }

  // ---------------------------------------------------------------- контекстное меню
  function openContextMenu(msg, x, y, row) {
    const chat = state.current;
    const mine = msg.sender?.id === state.me.id;
    const canDelete = mine || (chat.type === 'group' && chat.owner_id === state.me.id);
    const pinned = chat.pinned?.id === msg.id;
    const items = [
      ['↩ Ответить', () => startReply(msg)],
      msg.text ? ['📋 Копировать текст', () => navigator.clipboard.writeText(msg.text).then(() => toast('Скопировано'))] : null,
      mine && msg.type !== 'voice' ? ['✏️ Изменить', () => startEdit(msg)] : null,
      [pinned ? '📌 Открепить' : '📌 Закрепить', () => api('POST', `/api/chats/${chat.id}/pin`, { message_id: pinned ? null : msg.id }).catch((e) => toast(e.message))],
      ['↪ Переслать', () => forwardMessage(msg)],
      msg.meta?.file ? ['⬇ Скачать', () => { const a = el('a', { href: msg.meta.file.url, download: msg.meta.file.name }); a.click(); }] : null,
      ...actions.filter((a) => !a.filter || a.filter(msg)).map((a) => [a.label, (item) => a.fn(msg, item || row)]),
      canDelete ? ['🗑 Удалить', async () => {
        if (!confirm('Удалить сообщение у всех?')) return;
        try { await api('DELETE', `/api/messages/${msg.id}`); } catch (e) { toast(e.message); }
      }, 'danger-item'] : null,
    ].filter(Boolean);
    const menu = $('context-menu');
    menu.replaceChildren(...items.map(([label, fn, cls]) => {
      const b = el('button', { class: cls || '' }, label);
      b.onclick = (e) => { e.stopPropagation(); fn(b); menu.classList.add('hidden'); };
      return b;
    }));
    menu.classList.remove('hidden');
    const r = menu.getBoundingClientRect();
    menu.style.left = Math.min(x, innerWidth - r.width - 8) + 'px';
    menu.style.top = Math.min(y, innerHeight - r.height - 8) + 'px';
  }
  document.addEventListener('click', () => $('context-menu').classList.add('hidden'));
  window.addEventListener('blur', () => $('context-menu').classList.add('hidden'));

  async function forwardMessage(msg) {
    const list = el('div', {});
    for (const c of state.chats) {
      list.append(el('div', { class: 'member-row', onclick: async () => {
        try {
          await api('POST', `/api/messages/${msg.id}/forward`, { chat_id: c.id });
          closeModal();
          toast('Переслано в «' + c.title + '»');
        } catch (e) { toast(e.message); }
      } }, chatAvatar(c, 'small'), el('div', { class: 'item-body' }, c.title)));
    }
    modal('Переслать в…', list, null);
  }

  // ---------------------------------------------------------------- ответ / редактирование
  function showContext(title, text) {
    $('compose-context-title').textContent = title;
    $('compose-context-text').textContent = text;
    $('compose-context').classList.remove('hidden');
  }
  function cancelContext() {
    const wasEditing = !!state.editing;
    state.replyTo = null;
    state.editing = null;
    $('compose-context').classList.add('hidden');
    if (wasEditing) { input.value = ''; autosize(); }
    updateSendButton();
  }
  $('compose-context-cancel').onclick = cancelContext;

  function startReply(msg) {
    state.editing = null;
    state.replyTo = msg;
    showContext('Ответ ' + (msg.sender?.display_name || ''), previewText(msg));
    input.focus();
    updateSendButton();
  }

  function startEdit(msg) {
    state.replyTo = null;
    state.editing = msg;
    showContext('Редактирование', previewText(msg));
    input.value = msg.text;
    autosize();
    input.focus();
    updateSendButton();
  }

  // ---------------------------------------------------------------- поиск по чату
  $('toggle-search').onclick = () => {
    const box = $('chat-search');
    box.classList.toggle('hidden');
    if (!box.classList.contains('hidden')) { $('chat-search-input').value = ''; $('chat-search-results').replaceChildren(); $('chat-search-input').focus(); }
  };
  let chatSearchTimer;
  $('chat-search-input').oninput = () => {
    clearTimeout(chatSearchTimer);
    const q = $('chat-search-input').value.trim();
    chatSearchTimer = setTimeout(async () => {
      if (!q) { $('chat-search-results').replaceChildren(); return; }
      const found = await api('GET', `/api/chats/${state.current.id}/search?q=${encodeURIComponent(q)}`);
      $('chat-search-results').replaceChildren(...(found.length ? found.map((m) =>
        el('div', { class: 'item', onclick: () => { $('chat-search').classList.add('hidden'); jumpTo(m.id); } },
          el('div', { class: 'item-body' },
            el('div', { class: 'item-top' },
              el('span', { class: 'item-title' }, m.sender?.display_name || ''),
              el('span', { class: 'item-time' }, fmtShort(m.created_at))),
            el('div', { class: 'item-sub' }, previewText(m))))) :
        [el('div', { class: 'muted', style: 'padding:8px 4px' }, 'Ничего не найдено')]));
    }, 300);
  };

  // ================================================================ отправка
  const input = $('input');
  let lastTyping = 0;
  let hintIndex = 0;

  function autosize() {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 160) + 'px';
  }

  function updateSendButton() {
    const voice = !input.value.trim() && !state.editing;
    $('send').textContent = voice ? '🎤' : state.editing ? '✓' : '➤';
    $('send').title = voice ? 'Записать голосовое' : 'Отправить';
  }

  function currentHints() {
    const v = input.value;
    if (!v.startsWith('/') || v.includes(' ')) return [];
    return state.config.commands.filter((c) => ('/' + c.name).startsWith(v.toLowerCase()));
  }

  function renderHints() {
    const hints = currentHints();
    const box = $('command-hints');
    box.classList.toggle('hidden', !hints.length);
    hintIndex = Math.min(hintIndex, Math.max(hints.length - 1, 0));
    box.replaceChildren(...hints.map((c, i) => el('div', {
      class: i === hintIndex ? 'sel' : '',
      onclick: () => { input.value = '/' + c.name + ' '; renderHints(); input.focus(); },
    }, el('b', {}, '/' + c.name), ' — ' + c.description)));
  }

  function sendTyping(action = 'typing') {
    if (state.current && Date.now() - lastTyping > 2500) {
      lastTyping = Date.now();
      api('POST', `/api/chats/${state.current.id}/typing`, { action }).catch(() => {});
    }
  }

  input.addEventListener('input', () => {
    autosize();
    renderHints();
    updateSendButton();
    if (input.value.trim()) sendTyping();
  });

  input.addEventListener('keydown', (e) => {
    const hints = currentHints();
    if (hints.length && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
      e.preventDefault();
      hintIndex = (hintIndex + (e.key === 'ArrowDown' ? 1 : hints.length - 1)) % hints.length;
      renderHints();
      return;
    }
    if (hints.length && e.key === 'Tab') {
      e.preventDefault();
      input.value = '/' + hints[hintIndex].name + ' ';
      renderHints();
      return;
    }
    if (e.key === 'ArrowUp' && !input.value) {
      const lastMine = [...state.messages].reverse().find((m) => m.sender?.id === state.me.id && m.type !== 'voice');
      if (lastMine) { e.preventDefault(); startEdit(lastMine); }
      return;
    }
    if (e.key === 'Escape') { cancelContext(); return; }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      $('composer').requestSubmit();
    }
  });

  async function sendPayload(payload) {
    const chat = state.current;
    if (state.replyTo) payload.reply_to = state.replyTo.id;
    await api('POST', `/api/chats/${chat.id}/messages`, payload);
    if (state.hasNewer) await loadLatest();
  }

  $('composer').onsubmit = async (e) => {
    e.preventDefault();
    if (!state.current) return;
    const text = input.value.trim();
    if (!text && !state.editing) return startRecording();
    const editing = state.editing;
    input.value = '';
    autosize();
    renderHints();
    try {
      if (editing) await api('PATCH', `/api/messages/${editing.id}`, { text });
      else await sendPayload({ text });
      state.editing = null;
      cancelContext();
    } catch (err) {
      toast(err.message);
      input.value = text;
    }
    updateSendButton();
  };

  // ---------------------------------------------------------------- файлы
  $('attach').onclick = () => $('file-input').click();
  $('file-input').onchange = () => {
    const files = [...$('file-input').files];
    $('file-input').value = '';
    if (files.length) confirmFiles(files);
  };

  function confirmFiles(files) {
    if (!state.current) return;
    const preview = el('div', { class: 'upload-preview' });
    for (const f of files) {
      if (/^image\/(png|jpeg|gif|webp)$/.test(f.type)) {
        const img = el('img', { alt: f.name });
        img.src = URL.createObjectURL(f);
        preview.append(img);
      } else preview.append(el('div', { class: 'file-chip' }, '📄 ' + f.name + ' · ' + fmtSize(f.size)));
    }
    const caption = el('input', { placeholder: 'Подпись (необязательно)', value: input.value.trim() });
    modal(files.length > 1 ? `Отправить ${files.length} файла(ов)` : 'Отправить файл', el('div', { style: 'display:flex;flex-direction:column;gap:10px' }, preview, caption), async () => {
      $('modal-ok').disabled = true;
      $('modal-ok').textContent = 'Загрузка…';
      try {
        for (let i = 0; i < files.length; i++) {
          const f = await upload(files[i]);
          await sendPayload({ file_id: f.id, text: i === 0 ? caption.value : '' });
        }
        if (caption.value && caption.value === input.value.trim()) { input.value = ''; autosize(); updateSendButton(); }
        cancelContext();
      } finally {
        $('modal-ok').disabled = false;
      }
    }, 'Отправить');
    caption.focus();
  }

  input.addEventListener('paste', (e) => {
    const files = [...(e.clipboardData?.files || [])];
    if (files.length) { e.preventDefault(); confirmFiles(files); }
  });

  let dragDepth = 0;
  document.addEventListener('dragenter', (e) => {
    if (!state.current || !e.dataTransfer?.types.includes('Files')) return;
    dragDepth++;
    $('drop-overlay').classList.remove('hidden');
  });
  document.addEventListener('dragleave', () => { if (--dragDepth <= 0) { dragDepth = 0; $('drop-overlay').classList.add('hidden'); } });
  document.addEventListener('dragover', (e) => { if (state.current) e.preventDefault(); });
  document.addEventListener('drop', (e) => {
    dragDepth = 0;
    $('drop-overlay').classList.add('hidden');
    if (!state.current) return;
    e.preventDefault();
    const files = [...(e.dataTransfer?.files || [])];
    if (files.length) confirmFiles(files);
  });

  // ---------------------------------------------------------------- голосовые
  async function startRecording() {
    if (state.recording) return;
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      return toast('Запись голоса недоступна (нужен https или localhost и современный браузер)');
    }
    let stream;
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
    catch { return toast('Нет доступа к микрофону'); }
    const mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4']
      .find((t) => MediaRecorder.isTypeSupported(t)) || '';
    const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : {});
    const chunks = [];
    const startedAt = Date.now();
    const chat = state.current;
    const replyTo = state.replyTo;
    const r = { rec, cancelled: false };
    state.recording = r;
    rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    const timer = setInterval(() => {
      $('rec-time').textContent = fmtDuration((Date.now() - startedAt) / 1000);
      lastTyping = 0;
      sendTyping('recording');
    }, 1000);
    rec.onstop = async () => {
      clearInterval(timer);
      stream.getTracks().forEach((t) => t.stop());
      state.recording = null;
      $('recording').classList.add('hidden');
      $('composer').classList.remove('hidden');
      const duration = (Date.now() - startedAt) / 1000;
      if (r.cancelled || duration < 0.7) return;
      const type = (rec.mimeType || 'audio/webm').split(';')[0];
      const ext = type.includes('ogg') ? 'ogg' : type.includes('mp4') ? 'm4a' : 'webm';
      try {
        const f = await upload(new File(chunks, `voice.${ext}`, { type }));
        const payload = { type: 'voice', file_id: f.id, duration };
        if (replyTo) payload.reply_to = replyTo.id;
        await api('POST', `/api/chats/${chat.id}/messages`, payload);
        if (state.replyTo === replyTo) cancelContext();
      } catch (e) { toast(e.message); }
    };
    rec.start(250);
    $('rec-time').textContent = '0:00';
    $('recording').classList.remove('hidden');
    $('composer').classList.add('hidden');
    sendTyping('recording');
  }
  $('rec-send').onclick = () => state.recording?.rec.stop();
  $('rec-cancel').onclick = () => { if (state.recording) { state.recording.cancelled = true; state.recording.rec.stop(); } };

  // ---------------------------------------------------------------- горячие клавиши
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (!$('lightbox').classList.contains('hidden')) $('lightbox').classList.add('hidden');
    else if (!$('context-menu').classList.contains('hidden')) $('context-menu').classList.add('hidden');
    else if (!$('modal').classList.contains('hidden')) closeModal();
    else if (!$('chat-search').classList.contains('hidden')) $('chat-search').classList.add('hidden');
  });

  // ================================================================ поехали
  updateSendButton();
  if (state.token) start(); else $('auth').classList.remove('hidden');
})();
