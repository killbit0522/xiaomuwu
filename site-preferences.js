(function () {
  var shelfScript=document.createElement('script');shelfScript.src='../shelf-store.js?v=20261009-1';document.head.appendChild(shelfScript);
  var memberTools=document.createElement('script');memberTools.src='../member-tools.js?v=20261022-1';document.head.appendChild(memberTools);
  if (/\/pages\/(admin|reader)\.html$/.test(location.pathname)) {
    var enhancement = document.createElement('script');
    enhancement.src = '../reading-support.js?v=20261001';
    document.head.appendChild(enhancement);
  }
  if (/\/pages\/admin\.html$/.test(location.pathname)) {
    var communityScript = document.createElement('script');
    communityScript.src = '../admin-community.js?v=20261012-3';
    document.head.appendChild(communityScript);
  }
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
  var primaryNav=document.querySelector('.gw-nav,.sh-nav');
  if(primaryNav){var comics=document.createElement('details');comics.className='external-comics';var heading=document.createElement('summary');heading.textContent='漫画';comics.appendChild(heading);[['漫蛙','https://appgu.cc/rank/'],['漫画工具（GitHub）','https://github.com/kanasimi/work_crawler']].forEach(function(item){var link=document.createElement('a');link.textContent=item[0];link.href=item[1];link.rel='noreferrer';comics.appendChild(link)});primaryNav.appendChild(comics);var ao3=document.createElement('a');ao3.className=primaryNav.classList.contains('gw-nav')?'gw-nav-btn':'sh-nav-btn';ao3.textContent='AO3 镜像';ao3.href='https://aoya.moe/works/search?page=1&view_adult=true&work_search%5Bquery%5D=%E7%A6%85%E9%99%A2%E7%9B%B4%E5%93%89';ao3.rel='noreferrer';primaryNav.appendChild(ao3)}
  if(primaryNav&&!primaryNav.querySelector('a[href="./online-search.html"]')){var onlineLink=document.createElement('a');onlineLink.href='./online-search.html';onlineLink.className=primaryNav.classList.contains('gw-nav')?'gw-nav-btn':'sh-nav-btn';onlineLink.innerHTML='<span aria-hidden="true">网</span><span class="'+(primaryNav.classList.contains('gw-nav')?'gw-nav-label':'sh-nav-label')+'">联网找书</span><span class="'+(primaryNav.classList.contains('gw-nav')?'gw-nav-en':'sh-nav-en')+'">Online</span>';primaryNav.appendChild(onlineLink)}
  fetch('/api/access',{cache:'no-store'}).then(function(r){return r.json()}).then(function(data){if(data.ok&&data.cardType==='reader'&&!document.querySelector('a[href="./bookshelf.html"]')){var nav=document.querySelector('.gw-nav,.sh-nav'),link=document.createElement('a');link.href='./bookshelf.html';if(nav){link.className=nav.classList.contains('gw-nav')?'gw-nav-btn':'sh-nav-btn';link.innerHTML='<span aria-hidden="true">书</span><span class="'+(nav.classList.contains('gw-nav')?'gw-nav-label':'sh-nav-label')+'">我的书架</span><span class="'+(nav.classList.contains('gw-nav')?'gw-nav-en':'sh-nav-en')+'">Bookshelf</span>';nav.appendChild(link)}else{link.className='site-shelf-link';link.textContent='我的书架';document.body.appendChild(link)}}if((location.pathname==='/'||/\/pages\/home\.html$/.test(location.pathname))&&data.ok&&data.cardType==='reader'&&Number(data.expiresInSeconds)>0&&Number(data.expiresInSeconds)<=86400&&!document.querySelector('.card-expiry-notice')){var expiry=document.createElement('aside'),expiryText=document.createElement('span'),expiryClose=document.createElement('button');expiry.className='card-expiry-notice';expiry.setAttribute('role','status');expiryText.textContent='您的卡密还有一天到期，如需续期找木木哦';expiryClose.type='button';expiryClose.textContent='×';expiryClose.setAttribute('aria-label','关闭到期提醒');expiryClose.onclick=function(){expiry.remove()};expiry.append(expiryText,expiryClose);document.body.appendChild(expiry)}}).catch(function(){});
  if(!/\/pages\/(admin|reader)\.html$/.test(location.pathname)){fetch('/api/updates',{cache:'no-store'}).then(function(response){return response.ok?response.json():null}).then(function(data){if(!data||!data.version||!Array.isArray(data.items)||!data.items.length)return;var noticeKey='xiaomuwu-last-update-notice',seen='';try{seen=localStorage.getItem(noticeKey)||''}catch(error){}if(seen===data.version)return;var notice=document.createElement('aside'),titles=data.items.slice(0,2).map(function(item){return '《'+item.title+'》'}).join('、'),message=document.createElement('span'),link=document.createElement('a'),close=document.createElement('button');notice.className='site-update-notice';notice.setAttribute('role','status');message.textContent='新书已入库：'+titles;link.href='./catalog.html';link.textContent='去目录';close.type='button';close.textContent='×';close.setAttribute('aria-label','关闭新书提示');close.addEventListener('click',function(){notice.remove()});notice.append(message,link,close);document.body.appendChild(notice);try{localStorage.setItem(noticeKey,data.version)}catch(error){}}).catch(function(){})}
  var shelfButton=document.getElementById('add-shelf');
  if(shelfButton){var params=new URLSearchParams(location.search),bookPath=params.get('p')||'',bookTitle=params.get('title')||'未命名',bookFormat=(params.get('format')||'txt').toLowerCase(),bookOnlineId=params.get('onlineId')||'',bookSource=params.get('source')||'',bookSourceUrl=params.get('sourceUrl')||'';function readShelf(){try{var value=JSON.parse(localStorage.getItem('xiaomuwu-shelf')||'[]');return Array.isArray(value)?value:[]}catch(error){return []}}function refreshShelfButton(){var exists=readShelf().some(function(item){return item.searchablePath===bookPath});shelfButton.textContent=exists?'已在书架':'加入书架';shelfButton.classList.toggle('active',exists)}refreshShelfButton();shelfButton.addEventListener('click',function(event){event.stopPropagation();var books=readShelf();if(!books.some(function(item){return item.searchablePath===bookPath})){books.push({title:bookTitle,searchablePath:bookPath,format:bookFormat,category:bookOnlineId?'联网书籍':'阅读中',onlineId:bookOnlineId,source:bookSource,sourceUrl:bookSourceUrl});try{localStorage.setItem('xiaomuwu-shelf',JSON.stringify(books))}catch(error){shelfButton.textContent='保存失败';return}}refreshShelfButton()})}
  var side=document.querySelector('.gw-sidebar,.sh-side');
  if(side){var reveal=document.createElement('button');reveal.type='button';reveal.className='site-side-reveal';document.body.appendChild(reveal);var mobile=window.matchMedia('(max-width:700px)').matches,timer;function paintSideButton(open){reveal.textContent=open?'收起':'导航';reveal.setAttribute('aria-label',open?'关闭左侧导航':'展开左侧导航');reveal.classList.toggle('is-open',open);reveal.classList.toggle('is-visible',!open)}function hideSide(){clearTimeout(timer);side.classList.add('is-auto-hidden');paintSideButton(false)}function showSide(){side.classList.remove('is-auto-hidden');paintSideButton(true);scheduleHide()}function scheduleHide(){clearTimeout(timer);timer=setTimeout(hideSide,10000)}if(mobile){hideSide()}else{paintSideButton(true);scheduleHide()}side.addEventListener('click',function(event){if(event.target.closest('a,button'))return;scheduleHide()});reveal.onclick=function(){if(side.classList.contains('is-auto-hidden'))showSide();else hideSide()}}
  /* reader.html has its own complete pager.  Loading the legacy pager there
     created a second set of page controls and could overwrite reading progress. */
  if(document.body.hasAttribute('data-mode')&&!document.getElementById('paper')){var turnScript=document.createElement('script');turnScript.src='../reader-page-turn.js?v=2';document.body.appendChild(turnScript)}
})();
