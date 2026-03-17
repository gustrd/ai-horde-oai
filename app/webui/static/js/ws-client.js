/**
 * WebSocket client with auto-reconnect.
 * Exposes a singleton `wsClient` with `on(type, handler)` and `send(msg)`.
 */
(function () {
  const RECONNECT_DELAY = 3000;
  const handlers = {};

  let socket = null;
  let statusEl = null;  // set by app.js

  function updateStatus(state) {
    window._wsStatus = state;
    document.dispatchEvent(new CustomEvent('ws-status', { detail: state }));
  }

  function connect() {
    updateStatus('connecting');
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const url = `${proto}://${location.host}/ui/ws`;

    socket = new WebSocket(url);

    socket.onopen = () => {
      updateStatus('connected');
    };

    socket.onmessage = (event) => {
      let msg;
      try { msg = JSON.parse(event.data); } catch { return; }
      const type = msg.type;
      if (handlers[type]) {
        handlers[type].forEach(fn => { try { fn(msg.data); } catch (e) { console.error(e); } });
      }
      if (handlers['*']) {
        handlers['*'].forEach(fn => { try { fn(msg); } catch (e) { console.error(e); } });
      }
    };

    socket.onclose = () => {
      updateStatus('disconnected');
      socket = null;
      setTimeout(connect, RECONNECT_DELAY);
    };

    socket.onerror = () => {
      socket.close();
    };
  }

  window.wsClient = {
    on(type, fn) {
      if (!handlers[type]) handlers[type] = [];
      handlers[type].push(fn);
    },
    send(msg) {
      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify(msg));
      }
    },
    start() { connect(); },
  };
})();
