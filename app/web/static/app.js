(function () {
  var panel = document.getElementById('alerts-panel');
  var countBadge = document.getElementById('alert-count');
  var noAlertsMsg = document.getElementById('no-alerts-msg');
  if (!panel) return;

  var alertCount = 0;

  function connect() {
    var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    var ws = new WebSocket(proto + '//' + location.host + '/ws/alerts');

    ws.onmessage = function (e) {
      var data;
      try { data = JSON.parse(e.data); } catch (_) { return; }

      if (noAlertsMsg) { noAlertsMsg.remove(); noAlertsMsg = null; }
      alertCount++;
      if (countBadge) countBadge.textContent = alertCount;

      var row = document.createElement('div');
      var colorClass = data.event === 'created' ? 'info' : (data.event === 'updated' ? 'warning' : 'danger');
      var badgeClass = data.event === 'created' ? 'bg-info text-dark' : (data.event === 'updated' ? 'bg-warning text-dark' : 'bg-danger');
      row.className = 'alert alert-' + colorClass + ' py-2 mb-1';

      var name = data.notice_name || data.notice_id || 'Unknown';
      var diffText = '';
      if (data.diff && Object.keys(data.diff).length > 0) {
        diffText = ' <small class="text-muted">&middot; changed: ' + Object.keys(data.diff).join(', ') + '</small>';
      }
      var ts = data.recorded_at ? new Date(data.recorded_at).toLocaleTimeString() : '';
      row.innerHTML = '<span class="badge ' + badgeClass + '">' + data.event + '</span> <strong>' + name + '</strong>' + diffText + ' <small class="text-muted float-end">' + ts + '</small>';

      panel.insertBefore(row, panel.firstChild);
      while (panel.children.length > 50) {
        panel.removeChild(panel.lastChild);
      }
    };

    ws.onclose = function () { setTimeout(connect, 2000); };
    ws.onerror = function () { ws.close(); };
  }

  connect();
})();
