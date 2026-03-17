/**
 * Dashboard tab component.
 */
function dashboardTab() {
  return {
    d: {},
    lastRefresh: '',

    async init() {
      document.addEventListener('tab-activated', (e) => {
        if (e.detail === 'dashboard') this.load();
      });
      wsClient.on('log_entry', () => this.load());
      wsClient.on('dashboard_update', (data) => { this.d = data; });
      await this.load();
    },

    async load() {
      try {
        const r = await fetch('/ui/api/dashboard');
        this.d = await r.json();
        this.lastRefresh = new Date().toLocaleTimeString();
      } catch (e) {
        console.error('Dashboard load error', e);
      }
    },

    async unban() {
      await fetch('/ui/api/dashboard/unban', { method: 'POST' });
      await this.load();
    },

    fmtTime(ts) {
      if (!ts) return '—';
      try { return new Date(ts).toLocaleString(); } catch { return ts; }
    },
  };
}
