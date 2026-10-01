(function () {
  'use strict';
  // Only metadata is stored here; the reader still enforces server access checks.
  function load(key) { try { return JSON.parse(localStorage.getItem(key) || 'null'); } catch (_) { return null; } }
  var book = load('xiaomuwu-last-read');
  if (book && typeof book.path === 'string' && typeof book.title === 'string') {
    var panel = document.createElement('aside');
    panel.style.cssText = 'position:relative;margin:16px auto;padding:14px;width:calc(100% - 28px);max-width:800px;box-sizing:border-box;background:#f4f1e9;color:#26231f;border:1px solid #aaa;border-radius:10px;';
    var label = document.createElement('p'); label.textContent = '上次读到：《' + book.title + '》';label.style.margin='0 0 8px';
    var link = document.createElement('a');link.textContent = '继续上次阅读';link.style.color='inherit';
    link.href = './reader.html?' + new URLSearchParams({p:book.path,title:book.title,format:book.format||'',return:location.pathname});
    var note=document.createElement('small');note.textContent='（此浏览器保存的记录）';
    panel.append(label,link,note);
    var main=document.querySelector('main');(main||document.body).prepend(panel);
  }
  var shelf=document.getElementById('shelf');
  if(shelf){
    function update(){shelf.querySelectorAll('a').forEach(function(link){
      var url=new URL(link.href,location.href),path=url.searchParams.get('p');
      if(!path)return;
      var saved=load('xiaomuwu-reader:'+path),anchor=load('xiaomuwu-anchor:'+path);
      if(saved||anchor)link.textContent='继续阅读';
      url.searchParams.set('return','/pages/bookshelf.html');link.href=url.href;
    });}
    new MutationObserver(update).observe(shelf,{childList:true});update();
  }
})();
