(function () {
  'use strict';
  if (window.cabinReadingSupportLoaded) return;
  window.cabinReadingSupportLoaded = true;
  function text(tag, value) { var el = document.createElement(tag); el.textContent = value; return el; }
  // 管理后台已有自己的体检和反馈面板；这里必须退出，避免重复插入
  // 两套按钮而造成空白按钮、错位或重复请求。
  if (location.pathname.endsWith('/admin.html')) return;
  var content = document.getElementById('content'), paper = document.getElementById('paper');
  if (!content || !paper) return;
  var query = new URLSearchParams(location.search), path = query.get('p') || '', title = query.get('title') || path;
  var key = 'xiaomuwu-anchor:' + path, ready = false, restoring = false, saved, pending, last;
  try { saved = JSON.parse(localStorage.getItem(key) || 'null'); } catch (_) {}
  function paged() { return document.body.dataset.mode !== 'scroll'; }
  function capture() {
    if (!ready || restoring || content.hidden) return;
    var paragraphs = content.querySelectorAll('.chapter-text'), selected, offset = 0;
    for (var i = 0; i < paragraphs.length; i++) {
      var rect = paragraphs[i].getBoundingClientRect();
      if (rect.bottom > 90 && rect.top < innerHeight && rect.right > 20 && rect.left < innerWidth) { selected = paragraphs[i]; break; }
    }
    if (!selected || !selected.firstChild) return;
    var node = selected.firstChild, length = node.length, range = document.createRange();
    // Find the first visible character; this survives font-size and viewport changes.
    var low = 0, high = Math.max(0, length - 1);
    while (low < high) {
      var mid = Math.floor((low + high) / 2); range.setStart(node, mid); range.setEnd(node, Math.min(length, mid + 1));
      var r = range.getBoundingClientRect();
      if (paged() ? r.right < paper.getBoundingClientRect().left + 20 : r.bottom < 90) low = mid + 1; else high = mid;
    }
    offset = low;
    last = {chapter:selected.parentElement.id, offset:offset, sample:node.textContent.slice(offset, offset + 32)};
    try { localStorage.setItem(key, JSON.stringify(last)); } catch (_) {}
  }
  function restore(anchor) {
    if (!anchor) return;
    var section = document.getElementById(anchor.chapter), paragraph = section && section.querySelector('.chapter-text');
    if (!paragraph || !paragraph.firstChild) return;
    restoring = true;
    var node = paragraph.firstChild, offset = Math.min(anchor.offset || 0, Math.max(0,node.length-1));
    if (anchor.sample && node.textContent.slice(offset,offset+32) !== anchor.sample) { var match = node.textContent.indexOf(anchor.sample); if (match < 0) { restoring=false; return; } offset=match; }
    var range=document.createRange(); range.setStart(node,offset); range.setEnd(node,Math.min(node.length,offset+1));
    var rect=range.getBoundingClientRect();
    if (paged()) paper.scrollLeft = Math.floor((paper.scrollLeft + rect.left - paper.getBoundingClientRect().left)/paper.clientWidth)*paper.clientWidth;
    else window.scrollTo({top:window.scrollY+rect.top-90,behavior:'instant'});
    setTimeout(function(){restoring=false;capture();},80);
  }
  function schedule() { clearTimeout(pending); pending=setTimeout(capture,250); }
  var observer = new MutationObserver(function () {
    if (!ready && !content.hidden && content.querySelector('.chapter-text')) {
      observer.disconnect();
      setTimeout(function(){ready=true;restore(saved);capture();},500);
    }
  }); observer.observe(content,{childList:true,attributes:true});
  if (!content.hidden && content.querySelector('.chapter-text')) {observer.disconnect();setTimeout(function(){ready=true;restore(saved);capture();},500);}
  window.addEventListener('scroll',schedule,{passive:true}); paper.addEventListener('scroll',schedule,{passive:true});
  window.addEventListener('pagehide',capture); document.addEventListener('visibilitychange',function(){if(document.hidden)capture();});
  var resizeTimer, viewportWidth=innerWidth, resizeAnchor;
  window.addEventListener('resize',function(){
    if(!ready || innerWidth===viewportWidth)return;
    viewportWidth=innerWidth;
    if(!resizeTimer)resizeAnchor=last||saved;
    restoring=true;clearTimeout(pending);clearTimeout(resizeTimer);
    resizeTimer=setTimeout(function(){resizeTimer=null;restoring=false;restore(resizeAnchor);},550);
  });
  var controls=document.getElementById('controls');
  var resume=text('button','继续上次阅读'), report=text('button','反馈本书问题'); resume.type=report.type='button';
  resume.onclick=function(){restore(saved||last);};
  var sent=false;
  function reportIssue(type, detail) {
    return fetch('/api/events',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:type,visitor:'reader',detail:JSON.stringify({title:title,path:path,message:detail})}),keepalive:true}).then(function(r){if(!r.ok)throw new Error('发送失败');});
  }
  report.onclick=function(){var message=prompt('请描述这本书的问题（例如乱码、缺章或跳页）：');if(!message)return;report.disabled=true;reportIssue('reading_feedback',message.slice(0,800)).then(function(){report.textContent='反馈已发送';}).catch(function(){report.textContent='发送失败，点击重试';report.disabled=false;});};
  controls.append(resume,report);
  var status=document.getElementById('status');
  document.addEventListener('reader-load-error',function(event){if(!sent){sent=true;reportIssue('reading_error',event.detail.message+' (HTTP '+event.detail.code+')').catch(function(){sent=false;});}});
})();
