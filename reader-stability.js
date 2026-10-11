(function () {
  'use strict';
  var paper = document.getElementById('paper');
  var content = document.getElementById('content');
  if (!paper || !content) return;
  var pending = 0, correcting = false;

  function paged() { return document.body.dataset.mode && document.body.dataset.mode !== 'scroll'; }
  function hasVisibleText() {
    var frame = paper.getBoundingClientRect();
    var nodes = content.querySelectorAll('.chapter-text');
    for (var i = 0; i < nodes.length; i++) {
      var range = document.createRange(); range.selectNodeContents(nodes[i]);
      var rects = range.getClientRects();
      for (var j = 0; j < rects.length; j++) {
        var rect = rects[j];
        if (rect.right > frame.left + 8 && rect.left < frame.right - 8 && rect.bottom > frame.top + 8 && rect.top < frame.bottom - 8) return true;
      }
    }
    return false;
  }
  function stabilize() {
    pending = 0;
    if (correcting || !paged() || content.hidden || !content.textContent.trim() || hasVisibleText()) return;
    var original = paper.scrollLeft, max = Math.max(0, paper.scrollWidth - paper.clientWidth), step = Math.max(1, paper.clientWidth);
    correcting = true;
    var candidates = [original - step, original + step, original - step * 2, original + step * 2];
    for (var i = 0; i < candidates.length; i++) {
      var target = Math.max(0, Math.min(max, candidates[i]));
      if (Math.abs(target - original) < 2) continue;
      paper.scrollLeft = target;
      if (hasVisibleText()) { correcting = false; return; }
    }
    paper.scrollLeft = original;
    correcting = false;
  }
  function queue() { if (!pending) pending = setTimeout(stabilize, 40); }
  paper.addEventListener('scroll', queue, { passive: true });
  document.addEventListener('pointerup', queue, true);
  document.addEventListener('keydown', function (event) { if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') queue(); });
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(function () { window.dispatchEvent(new Event('resize')); });
})();
