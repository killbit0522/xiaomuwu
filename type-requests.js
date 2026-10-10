(function () {
  'use strict';
  if (!/\/pages\/review\.html$/.test(location.pathname)) return;
  var editor = document.getElementById('review-editor');
  if (!editor || document.getElementById('type-request-list')) return;
  var section = document.createElement('section'); section.id = 'type-request-list'; section.className = 'type-request-list';
  var heading = document.createElement('h2'); heading.textContent = '大家想看的类型';
  var hint = document.createElement('p'); hint.className = 'type-request-hint'; hint.textContent = '管理员会在这里回复；回复为公开内容，请不要留下手机号、地址等隐私信息。';
  var list = document.createElement('div'); section.append(heading, hint, list); editor.after(section);
  function render(items) {
    list.replaceChildren();
    if (!items.length) { var empty = document.createElement('p'); empty.className = 'type-request-hint'; empty.textContent = '还没有留言，写下你想看的类型吧。'; list.appendChild(empty); return; }
    items.forEach(function (item) {
      var row = document.createElement('article'); row.className = 'type-request-item';
      var title = document.createElement('h3'); title.textContent = item.title || item.book || '想看的类型';
      var body = document.createElement('p'); body.textContent = item.body || '';
      row.append(title, body);
      if (item.admin_reply) { var reply = document.createElement('div'); reply.className = 'type-request-reply'; var label = document.createElement('strong'); label.textContent = '管理员回复：'; var text = document.createElement('span'); text.textContent = item.admin_reply; reply.append(label, text); row.appendChild(reply); }
      list.appendChild(row);
    });
  }
  fetch('/api/type-requests', { cache: 'no-store' }).then(function (response) { return response.ok ? response.json() : { items: [] }; }).then(function (data) { render(Array.isArray(data.items) ? data.items : []); }).catch(function () { render([]); });
})();
