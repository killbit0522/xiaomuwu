(function () {
  'use strict';
  var page = location.pathname;
  var isAdmin = /\/pages\/admin\.html$/.test(page);
  if (isAdmin) return;

  function navLink(nav, href, label, icon, english) {
    if (!nav) return null;
    var present = nav.querySelector('a[href="' + href + '"]');
    if (present) return present;
    var homeStyle = nav.classList.contains('gw-nav');
    var link = document.createElement('a');
    link.href = href;
    link.className = homeStyle ? 'gw-nav-btn' : 'sh-nav-btn';
    link.innerHTML = '<span aria-hidden="true">' + icon + '</span><span class="' + (homeStyle ? 'gw-nav-label' : 'sh-nav-label') + '"></span><span class="' + (homeStyle ? 'gw-nav-en' : 'sh-nav-en') + '"></span>';
    link.children[1].textContent = label;
    link.children[2].textContent = english;
    nav.appendChild(link);
    return link;
  }

  function unreadMarker(link, endpoint, itemsKey, dateKey, storageKey, destination) {
    var badge = null;
    if (link) { badge = document.createElement('span'); badge.className = 'nav-unread-badge'; badge.hidden = true; badge.setAttribute('aria-label', '未读消息'); link.appendChild(badge); }
    fetch(endpoint, { cache: 'no-store' }).then(function (response) { return response.ok ? response.json() : null; }).then(function (data) {
      var items = data && Array.isArray(data[itemsKey]) ? data[itemsKey] : [];
      var newest = items.reduce(function (value, item) { var next = String(item[dateKey] || ''); return next > value ? next : value; }, '');
      var seen = ''; try { seen = localStorage.getItem(storageKey) || ''; } catch (_) {}
      var onDestination = location.pathname.endsWith(destination);
      if (onDestination) { try { if (newest) localStorage.setItem(storageKey, newest); } catch (_) {} return; }
      var count = items.filter(function (item) { return String(item[dateKey] || '') > seen; }).length;
      if (count && badge) { badge.textContent = count > 99 ? '99+' : String(count); badge.hidden = false; link.title = count + ' 条未读'; }
      if (link) link.addEventListener('click', function () { try { if (newest) localStorage.setItem(storageKey, newest); } catch (_) {} });
    }).catch(function () {});
  }

  function showGuide() {
    var existing = document.getElementById('member-guide');
    if (existing) return existing;
    var modal = document.createElement('section');
    modal.id = 'member-guide'; modal.className = 'member-guide'; modal.hidden = true;
    modal.setAttribute('role', 'dialog'); modal.setAttribute('aria-modal', 'true'); modal.setAttribute('aria-label', '使用指南');
    var card = document.createElement('div'); card.className = 'member-guide-card';
    var close = document.createElement('button'); close.type = 'button'; close.className = 'member-guide-close'; close.textContent = '×'; close.setAttribute('aria-label', '关闭使用指南');
    var title = document.createElement('h2'); title.textContent = '使用指南';
    var list = document.createElement('ol');
    var externalTip=document.createElement('p');externalTip.textContent='导航中的“漫画”可选择漫蛙或漫画工具，“AO3 镜像”可直接打开外部页面。';card.appendChild(externalTip);
    ['点“目录”浏览书目；阅读卡在线阅读，下载卡下载，下载中断未完成会退回次数。', '新书会在首页提示；“近期新增”可查看最近 50 本。', '“木木有话说”保存我发的消息；“木木小广场”是交流区，我发布图文，会员可以写文字评论。', '缺书登记和“想看的类型”都可以留言，我会查看，也可以在后台回复。', '续费后用同一手机、同一浏览器登录，书架会保留；清理浏览器数据或换设备可能无法同步。'].forEach(function (text) { var item = document.createElement('li'); item.textContent = text; list.appendChild(item); });
    close.onclick = function () { modal.hidden = true; };
    modal.addEventListener('click', function (event) { if (event.target === modal) modal.hidden = true; });
    card.append(close, title, list); modal.appendChild(card); document.body.appendChild(modal);
    return modal;
  }

  function installGuide() {
    var gate = document.querySelector('.gw-gate-inner');
    if (!gate) return;
    var open = document.getElementById('member-guide-open');
    if (!open) { open = document.createElement('button'); open.id = 'member-guide-open'; open.className = 'member-guide-open'; open.type = 'button'; open.textContent = '使用指南'; var marker = document.getElementById('gw-gate-open') || gate.firstChild; gate.insertBefore(open, marker); }
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
    var messagesLink = navLink(nav, './messages.html', '木木有话说', '话', 'Messages');
    var communityLink = navLink(nav, './community.html', '木木小广场', '聊', 'Community');
    unreadMarker(messagesLink, '/api/messages', 'items', 'updatedAt', 'xiaomuwu-messages-read-at', '/pages/messages.html');
    unreadMarker(communityLink, '/api/community', 'items', 'created_at', 'xiaomuwu-community-read-at', '/pages/community.html');
    fetch('/api/announcement', { cache: 'no-store' }).then(function (response) { return response.ok ? response.json() : null; }).then(announcement).catch(function () {});
  }).catch(function () {});
})();
