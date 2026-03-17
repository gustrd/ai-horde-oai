/**
 * Root Alpine.js component: tab navigation, keyboard shortcuts, WS status.
 * Must be loaded last so all tab component functions are defined.
 */
function appShell() {
  return {
    tabs: [
      { id: 'dashboard', label: 'Dashboard' },
      { id: 'chat',      label: 'Chat' },
      { id: 'models',    label: 'Models' },
      { id: 'config',    label: 'Config' },
      { id: 'logs',      label: 'Logs' },
    ],
    activeTab: 'dashboard',
    wsStatus: 'connecting',
    wsStatusText: 'Connecting…',

    init() {
      // Keyboard shortcuts 1-6
      document.addEventListener('keydown', (e) => {
        // Skip if focus is inside an input/textarea/select
        const tag = document.activeElement?.tagName;
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
        const idx = parseInt(e.key, 10);
        if (idx >= 1 && idx <= this.tabs.length) {
          this.switchTab(this.tabs[idx - 1].id);
        }
      });

      // WS status updates
      document.addEventListener('ws-status', (e) => {
        this.wsStatus = e.detail;
        this.wsStatusText = {
          connected: 'Connected',
          disconnected: 'Disconnected',
          connecting: 'Connecting…',
        }[e.detail] || e.detail;
      });

      // Models tab can request a switch to chat
      document.addEventListener('switch-to-chat', () => {
        this.switchTab('chat');
      });

      wsClient.start();
    },

    switchTab(id) {
      this.activeTab = id;
      // Dispatch event so tab components can refresh
      document.dispatchEvent(new CustomEvent('tab-activated', { detail: id }));
    },
  };
}
