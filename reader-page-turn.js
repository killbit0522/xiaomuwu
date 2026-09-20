(function () {
  var paper = document.getElementById('paper');
  var progress = document.getElementById('progress');
  if (!paper || !progress) { return; }

  var previous = document.createElement('button');
  previous.type = 'button'; previous.className = 'page-turn-zone page-turn-prev';
  previous.setAttribute('aria-label', '上一页'); previous.textContent = '‹';
  var next = document.createElement('button');
  next.type = 'button'; next.className = 'page-turn-zone page-turn-next';
  next.setAttribute('aria-label', '下一页'); next.textContent = '›';
  var cover = document.createElement('span');
  cover.className = 'page-turn-cover'; cover.setAttribute('aria-hidden', 'true');
  document.body.append(previous, next, cover);

  var turning = false;
  function updateProgress() {
    var max = Math.max(0, paper.scrollWidth - paper.clientWidth);
    var value = max ? Math.round(paper.scrollLeft / max * 1000) : 0;
    progress.value = Math.max(0, Math.min(1000, value));
    var label = document.getElementById('progress-text');
    if (label) { label.textContent = Math.round(progress.value / 10) + '%'; }
    try {
      var path = new URLSearchParams(location.search).get('p') || '';
      var key = 'xiaomuwu-reader:' + path;
      var saved = JSON.parse(localStorage.getItem(key) || '{}');
      saved.progress = Number(progress.value); saved.mode = 'page';
      localStorage.setItem(key, JSON.stringify(saved));
    } catch (error) {}
  }
  function turn(direction) {
    if (document.body.dataset.mode !== 'page' || turning) { return; }
    var max = Math.max(0, paper.scrollWidth - paper.clientWidth);
    var gap = parseFloat(getComputedStyle(paper).columnGap) || 36;
    var step = paper.clientWidth + gap;
    var target = Math.max(0, Math.min(max, paper.scrollLeft + direction * step));
    if (Math.abs(target - paper.scrollLeft) < 2) { return; }
    turning = true;
    cover.className = 'page-turn-cover active ' + (direction > 0 ? 'turn-next' : 'turn-prev');
    window.setTimeout(function () { paper.scrollLeft = target; }, 150);
    window.setTimeout(function () { cover.className = 'page-turn-cover'; turning = false; updateProgress(); }, 360);
  }
  previous.addEventListener('click', function (event) { event.stopPropagation(); turn(-1); });
  next.addEventListener('click', function (event) { event.stopPropagation(); turn(1); });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'ArrowLeft') { turn(-1); }
    if (event.key === 'ArrowRight') { turn(1); }
  });
})();
