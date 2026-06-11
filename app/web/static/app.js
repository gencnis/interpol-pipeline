(function () {
  const list = document.getElementById('alerts-list');
  const indicator = document.getElementById('ws-indicator');
  if (!list) return; // not on a page with alerts panel

  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(proto + '//' + location.host + '/ws/alerts');

  ws.onopen = function () {
    if (indicator) { indicator.textContent = '● connected'; indicator.className = 'ws-connected'; }
  };

  ws.onclose = function () {
    if (indicator) { indicator.textContent = '● disconnected'; indicator.className = 'ws-disconnected'; }
  };

  ws.onerror = function () {
    if (indicator) { indicator.textContent = '● error'; indicator.className = 'ws-disconnected'; }
  };

  ws.onmessage = function (evt) {
    var data;
    try { data = JSON.parse(evt.data); } catch (e) { return; }

    var changeType = data.change_type || '';
    var name = [data.forename, data.name].filter(Boolean).join(' ') || data.notice_id;
    var diff = data.diff;
    var ts = data.recorded_at ? new Date(data.recorded_at).toLocaleTimeString() : '';

    var diffHtml = '';
    if (diff && typeof diff === 'object') {
      var parts = Object.keys(diff).map(function (field) {
        var c = diff[field];
        return '<span class="diff-field">' + field + ': <span class="diff-old">"' +
          c.old + '"</span> → <span class="diff-new">"' + c.new + '"</span></span>';
      });
      diffHtml = '<div class="diff">' + parts.join('') + '</div>';
    }

    var li = document.createElement('li');
    li.className = 'alert-item alert-' + changeType;
    li.innerHTML =
      '<span class="alert-badge badge-' + changeType + '">' + changeType.toUpperCase() + '</span>' +
      '<span class="alert-name">' + name + '</span>' +
      '<span class="alert-time">' + ts + '</span>' +
      diffHtml;

    list.insertBefore(li, list.firstChild);

    while (list.children.length > 50) {
      list.removeChild(list.lastChild);
    }
  };
})();
