(function () {
  var storageKey = 'xiaomuwu-theme';
  var root = document.documentElement;
  function savedTheme() { try { return localStorage.getItem(storageKey) || 'day'; } catch (error) { return 'day'; } }
  function apply(theme) {
    var dark = theme === 'dark';
    root.setAttribute('data-theme', dark ? 'dark' : 'day');
    var button = document.getElementById('site-theme-toggle');
    if (button) {
      button.setAttribute('aria-pressed', dark ? 'true' : 'false');
      button.setAttribute('aria-label', dark ? '切换到白天模式' : '切换到黑夜模式');
      button.innerHTML = '<span aria-hidden="true">' + (dark ? '☀' : '☾') + '</span><span class="site-theme-label">' + (dark ? '白天' : '黑夜') + '</span>';
    }
  }
  apply(savedTheme());
  if (!document.body.hasAttribute('data-mode')) {
    var button = document.createElement('button');
    button.id = 'site-theme-toggle'; button.className = 'site-theme-toggle'; button.type = 'button';
    document.body.appendChild(button); apply(savedTheme());
    button.addEventListener('click', function () {
      var next = root.getAttribute('data-theme') === 'dark' ? 'day' : 'dark';
      try { localStorage.setItem(storageKey, next); } catch (error) {}
      apply(next);
    });
  }
  fetch('/api/access').then(function(r){return r.json()}).then(function(data){if(data.ok&&data.cardType==='reader'&&!document.querySelector('a[href="./bookshelf.html"]')){var nav=document.querySelector('.gw-nav,.sh-nav'),link=document.createElement('a');link.href='./bookshelf.html';if(nav){link.className=nav.classList.contains('gw-nav')?'gw-nav-btn':'sh-nav-btn';link.innerHTML='<span aria-hidden="true">书</span><span class="'+(nav.classList.contains('gw-nav')?'gw-nav-label':'sh-nav-label')+'">我的书架</span><span class="'+(nav.classList.contains('gw-nav')?'gw-nav-en':'sh-nav-en')+'">Bookshelf</span>';nav.appendChild(link)}else{link.className='site-shelf-link';link.textContent='我的书架';document.body.appendChild(link)}}}).catch(function(){});
  var shelfButton=document.getElementById('add-shelf');
  if(shelfButton){var params=new URLSearchParams(location.search),bookPath=params.get('p')||'',bookTitle=params.get('title')||'未命名',bookFormat=(params.get('format')||'txt').toLowerCase();function readShelf(){try{var value=JSON.parse(localStorage.getItem('xiaomuwu-shelf')||'[]');return Array.isArray(value)?value:[]}catch(error){return []}}function refreshShelfButton(){var exists=readShelf().some(function(item){return item.searchablePath===bookPath});shelfButton.textContent=exists?'已在书架':'加入书架';shelfButton.classList.toggle('active',exists)}refreshShelfButton();shelfButton.addEventListener('click',function(event){event.stopPropagation();var books=readShelf();if(!books.some(function(item){return item.searchablePath===bookPath})){books.push({title:bookTitle,searchablePath:bookPath,format:bookFormat,category:'阅读中'});try{localStorage.setItem('xiaomuwu-shelf',JSON.stringify(books))}catch(error){shelfButton.textContent='保存失败';return}}refreshShelfButton()})}
  var side=document.querySelector('.gw-sidebar,.sh-side');
  if(side){var reveal=document.createElement('button');reveal.type='button';reveal.className='site-side-reveal';reveal.textContent='导航';reveal.setAttribute('aria-label','展开左侧导航');document.body.appendChild(reveal);var mobile=window.matchMedia('(max-width:700px)').matches,timer;function hideSide(){side.classList.add('is-auto-hidden');reveal.classList.add('is-visible')}function scheduleHide(){clearTimeout(timer);timer=setTimeout(hideSide,3000)}if(mobile){hideSide()}else{scheduleHide()}side.addEventListener('click',scheduleHide);reveal.onclick=function(){side.classList.remove('is-auto-hidden');reveal.classList.remove('is-visible');scheduleHide()}}
  /* reader.html has its own complete pager.  Loading the legacy pager there
     created a second set of page controls and could overwrite reading progress. */
  if(document.body.hasAttribute('data-mode')&&!document.getElementById('paper')){var turnScript=document.createElement('script');turnScript.src='../reader-page-turn.js?v=2';document.body.appendChild(turnScript)}
})();
