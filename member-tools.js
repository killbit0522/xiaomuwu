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
    ['首页可同时搜索本地书库和公开外部书源；未登录读者可查看书名，需有效卡密才能阅读、收藏或下载。', '阅读卡可站内阅读并加入书架；下载卡用于下载本地书。', '“木木账号查看”可打开我的小红书与抖音主页；点击账号名称即可跳转。', '点“目录”浏览书目；新书会在首页提示，“近期新增”可查看最近 50 本。', '“木木有话说”保存我发的消息；“木木小广场”是交流区，我发布图文，会员可以写文字评论。', '缺书登记可一次填写多本书名、作者或补充说明；“想看的类型”也可以留言，我会查看并回复。', '续费后用同一手机、同一浏览器登录，书架会保留；清理浏览器数据或换设备可能无法同步。'].forEach(function (text) { var item = document.createElement('li'); item.textContent = text; list.appendChild(item); });
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
  function addReaderNavigation(attempt) {
    fetch('/api/access', { cache: 'no-store' }).then(function (response) {
      if (!response.ok) throw new Error('access');
      return response.json();
    }).then(function (access) {
      if (!access.ok || access.cardType !== 'reader') return;
    var nav = document.querySelector('.gw-nav,.sh-nav');
    navLink(nav, './recent.html', '近期新增', '新', 'Recent');
    var messagesLink = navLink(nav, './messages.html', '木木有话说', '话', 'Messages');
    var communityLink = navLink(nav, './community.html', '木木小广场', '聊', 'Community');
    unreadMarker(messagesLink, '/api/messages', 'items', 'updatedAt', 'xiaomuwu-messages-read-at', '/pages/messages.html');
    unreadMarker(communityLink, '/api/community', 'items', 'created_at', 'xiaomuwu-community-read-at', '/pages/community.html');
    fetch('/api/announcement', { cache: 'no-store' }).then(function (response) { return response.ok ? response.json() : null; }).then(announcement).catch(function () {});
    }).catch(function () {
      if (attempt < 2) setTimeout(function () { addReaderNavigation(attempt + 1); }, 900 * (attempt + 1));
    });
  }
  addReaderNavigation(0);
})();
