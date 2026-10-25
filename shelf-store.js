(function () {
  'use strict';
  var primary = 'xiaomuwu-shelf', backup = 'xiaomuwu-shelf-backup';
  function parse(value) {
    try { var items = JSON.parse(value || '[]'); return Array.isArray(items) ? items : []; }
    catch (_) { return []; }
  }
  function unique(items) {
    var byPath = new Map();
    items.forEach(function (book) {
      if (book && typeof book.searchablePath === 'string' && book.searchablePath) byPath.set(book.searchablePath, book);
    });
    return Array.from(byPath.values()).slice(-300);
  }
  function getDeviceKey() {
    var key = 'xiaomuwu-shelf-device', value = '';
    try { value = localStorage.getItem(key) || ''; } catch (_) {}
    if (/^[a-f0-9]{32}$/.test(value)) return value;
    var bytes = new Uint32Array(4);
    if (window.crypto && window.crypto.getRandomValues) window.crypto.getRandomValues(bytes);
    else for (var i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 0x100000000);
    value = Array.prototype.map.call(bytes, function (part) { return part.toString(16).padStart(8, '0'); }).join('');
    try { localStorage.setItem(key, value); } catch (_) {}
    return value;
  }
  var deviceKey = getDeviceKey();
  try {
    var current = localStorage.getItem(primary), saved = localStorage.getItem(backup);
    if (current === null && saved !== null) localStorage.setItem(primary, saved);
    else if (current !== null && current !== saved) localStorage.setItem(backup, current);
  } catch (_) {}
  if (window.__xiaomuwuShelfBackup) return;
  window.__xiaomuwuShelfBackup = true;

  var original = Storage.prototype.setItem, syncQueue = Promise.resolve(), syncTimer;
  function sendDelta(previous, next) {
    var before = new Map(unique(previous).map(function (book) { return [book.searchablePath, book]; }));
    var after = new Map(unique(next).map(function (book) { return [book.searchablePath, book]; }));
    var added = Array.from(after.values()).filter(function (book) { return !before.has(book.searchablePath) || JSON.stringify(before.get(book.searchablePath)) !== JSON.stringify(book); });
    var removed = Array.from(before.keys()).filter(function (path) { return !after.has(path); });
    if (!added.length && !removed.length) return Promise.resolve();
    syncQueue = syncQueue.catch(function () {}).then(function () {
      return fetch('/api/shelf', {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Shelf-Device': deviceKey }, credentials: 'same-origin',
        body: JSON.stringify({ items: added, remove: removed })
      }).then(function (response) { if (!response.ok) throw new Error('书架同步暂不可用'); });
    }).catch(function () {});
    return syncQueue;
  }

  Storage.prototype.setItem = function (key, value) {
    var previous = null;
    if (key === primary) { try { previous = parse(localStorage.getItem(primary)); } catch (_) { previous = []; } }
    original.call(this, key, value);
    if (key !== primary) return;
    try { original.call(this, backup, value); } catch (_) {}
    clearTimeout(syncTimer);
    var next = parse(value);
    syncTimer = setTimeout(function () { sendDelta(previous, next); }, 120);
  };

  window.cabinShelfReady = fetch('/api/shelf', { cache: 'no-store', credentials: 'same-origin', headers: { 'X-Shelf-Device': deviceKey } })
    .then(function (response) { if (!response.ok) return null; return response.json(); })
    .then(function (data) {
      if (!data || !Array.isArray(data.items)) return;
      var local = parse(localStorage.getItem(primary));
      var merged = unique(local.concat(data.items));
      if (JSON.stringify(merged) !== JSON.stringify(local)) localStorage.setItem(primary, JSON.stringify(merged));
      else sendDelta(data.items, merged);
      return new Promise(function (resolve) { setTimeout(resolve, 140); }).then(function () { return syncQueue; });
    }).catch(function () {});
})();
