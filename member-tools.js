(function () {
  'use strict';
  var page = location.pathname;
  var isAdmin = /\/pages\/admin\.html$/.test(page);
  if (isAdmin) return;

  function navLink(nav, href, label, icon, english) {
    if (!nav || nav.querySelector('a[href="' + href + '"]')) return;
    var homeStyle = nav.classList.contains('gw-nav');
    var link = document.createElement('a');
    link.href = href;
    link.className = homeStyle ? 'gw-nav-btn' : 'sh-nav-btn';
    link.innerHTML = '<span aria-hidden="true">' + icon + '</span><span class="' + (homeStyle ? 'gw-nav-label' : 'sh-nav-label') + '"></span><span class="' + (homeStyle ? 'gw-nav-en' : 'sh-nav-en') + '"></span>';
    link.children[1].textContent = label;
    link.children[2].textContent = english;
    nav.appendChild(link);
  }

  function showGuide() {
    if (document.getElementById('member-guide')) return;
    var modal = document.createElement('section');
    modal.id = 'member-guide'; modal.className = 'member-guide'; modal.hidden = true;
    modal.setAttribute('role', 'dialog'); modal.setAttribute('aria-modal', 'true'); modal.setAttribute('aria-label', '使用指南');
    var card = document.createElement('div'); card.className = 'member-guide-card';
    var close = document.createElement('button'); close.type = 'button'; close.className = 'member-guide-close'; close.textContent = '×'; close.setAttribute('aria-label', '关闭使用指南');
    var title = document.createElement('h2'); title.textContent = '使用指南';
    var list = document.createElement('ol');
    ['点导航可以查看目录；', '缺书登记我都会实时查看，能找到我就会上传，之后网站首页会跳出相关更新书目；', '我的书架里面的书籍不会变动，小宝们续费之后只要手机不变还是会存在；', '可以从想看的类型里面给我发，我一般都会更新到《新上传》里面，而且也可以回复大家，或者有什么想问的也可以随便发。'].forEach(function (text) { var item = document.createElement('li'); item.textContent = text; list.appendChild(item); });
    close.onclick = function () { modal.hidden = true; };
    modal.addEventListener('click', function (event) { if (event.target === modal) modal.hidden = true; });
    card.append(close, title, list); modal.appendChild(card); document.body.appendChild(modal);
    return modal;
  }

  function installGuide() {
    var gate = document.querySelector('.gw-gate-inner');
    if (!gate || document.getElementById('member-guide-open')) return;
    var open = document.createElement('button'); open.id = 'member-guide-open'; open.className = 'member-guide-open'; open.type = 'button'; open.textContent = '使用指南';
    var marker = document.getElementById('gw-gate-open') || gate.firstChild;
    gate.insertBefore(open, marker);
    open.onclick = function () { var dialog = showGuide(); if (dialog) dialog.hidden = false; };
  }

  function announcement(data) {
    var item = data && data.announcement;
    if (!item || !item.title || !item.body || document.getElementById('member-announcement')) return;
    var key = 'xiaomuwu-announcement-seen', seen = '';
    try { seen = localStorage.getItem(key) || ''; } catch (_) {}
    if (seen === item.updatedAt) return;
    var box = document.createElement('aside'); box.id = 'member-announcement'; box.className = 'member-announcement'; box.setAttribute('role', 'dialog'); box.setAttribute('aria-label', '会员公告');
    var close = document.createElement('button'); close.type = 'button'; close.className = 'member-announcement-close'; close.textContent = '×'; close.setAttribute('aria-label', '关闭公告');
    var title = document.createElement('h2'); title.textContent = item.title;
    var body = document.createElement('p'); body.textContent = item.body;
    box.append(close, title);
    if (item.imageUrl) { var image = document.createElement('img'); image.src = item.imageUrl; image.alt = ''; image.loading = 'lazy'; box.appendChild(image); }
    box.appendChild(body); close.onclick = function () { try { localStorage.setItem(key, item.updatedAt); } catch (_) {} box.remove(); };
    document.body.appendChild(box);
  }

  installGuide();
  fetch('/api/access').then(function (response) { return response.json(); }).then(function (access) {
    if (!access.ok || access.cardType !== 'reader') return;
    var nav = document.querySelector('.gw-nav,.sh-nav');
    navLink(nav, './recent.html', '近期新增', '新', 'Recent');
    fetch('/api/announcement', { cache: 'no-store' }).then(function (response) { return response.ok ? response.json() : null; }).then(announcement).catch(function () {});
  }).catch(function () {});
})();
