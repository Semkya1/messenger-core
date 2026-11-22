// Клиентская часть плагина reactions. Доступен глобальный объект Messenger.
(() => {
  const style = document.createElement('style');
  style.textContent = `
    .reactions { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }
    .reaction { background: rgba(51,144,236,.12); color: #2b7bd0; padding: 1px 8px; border-radius: 12px; font-size: 13px; }
    .reaction:hover { background: rgba(51,144,236,.22); }
    .reaction.mine { background: #3390ec; color: #fff; }
    .reaction-picker { position: fixed; display: flex; gap: 2px; background: #fff; padding: 4px; border-radius: 20px;
                       box-shadow: 0 4px 16px rgba(0,0,0,.18); z-index: 30; }
    .reaction-picker button { background: none; font-size: 20px; padding: 2px 6px; border-radius: 50%; }
    .reaction-picker button:hover { background: #f0f2f5; }
  `;
  document.head.append(style);

  let EMOJIS = ['👍', '❤️', '😂', '😮', '😢', '🔥'];
  Messenger.api('GET', '/api/plugins/reactions/emojis').then((e) => { EMOJIS = e; }).catch(() => {});

  const toggle = (msg, emoji) =>
    Messenger.api('POST', '/api/plugins/reactions/toggle', { message_id: msg.id, emoji })
      .catch((e) => Messenger.toast(e.message));

  // Пункт «Реакция» в меню сообщения
  Messenger.addMessageAction(Messenger.t('reactions.action'), (msg, button) => {
    document.querySelector('.reaction-picker')?.remove();
    const picker = Messenger.el('div', { class: 'reaction-picker' },
      EMOJIS.map((e) => Messenger.el('button', { onclick: () => { toggle(msg, e); picker.remove(); } }, e)));
    const r = button.getBoundingClientRect();
    picker.style.left = Math.max(8, Math.min(r.left - 100, innerWidth - 280)) + 'px';
    picker.style.top = Math.max(8, r.top - 48) + 'px';
    document.body.append(picker);
    setTimeout(() => document.addEventListener('click', () => picker.remove(), { once: true }));
  });

  // Отрисовка реакций под текстом
  Messenger.addMessageDecorator((msg, row) => {
    if (!msg.reactions?.length) return;
    const box = Messenger.el('div', { class: 'reactions' },
      msg.reactions.map((r) => Messenger.el('button', {
        class: 'reaction' + (r.users.includes(Messenger.me.id) ? ' mine' : ''),
        onclick: () => toggle(msg, r.emoji),
      }, `${r.emoji} ${r.users.length}`)));
    row.querySelector('.bubble').insertBefore(box, row.querySelector('.msg-footer'));
  });
})();
