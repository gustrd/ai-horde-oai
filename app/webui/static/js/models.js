/**
 * Models tab – sortable/filterable table.
 */
function modelsTab() {
  return {
    models: [],
    search: '',
    sortKey: 'count',
    sortAsc: false,
    loading: false,
    cols: [
      { key: 'count',              label: 'Workers' },
      { key: 'max_context_length', label: 'Max Ctx' },
      { key: 'max_length',         label: 'Max Tok' },
      { key: 'queued',             label: 'Queued' },
      { key: 'eta',                label: 'ETA' },
      { key: 'performance',        label: 'T/s' },
      { key: 'name',               label: 'Name' },
    ],

    get filtered() {
      let list = this.models;
      if (this.search) {
        const q = this.search.toLowerCase();
        list = list.filter(m => m.name.toLowerCase().includes(q));
      }
      const key = this.sortKey;
      list = [...list].sort((a, b) => {
        const av = a[key] ?? 0;
        const bv = b[key] ?? 0;
        if (typeof av === 'string') return this.sortAsc ? av.localeCompare(bv) : bv.localeCompare(av);
        return this.sortAsc ? av - bv : bv - av;
      });
      return list;
    },

    async init() {
      document.addEventListener('tab-activated', (e) => {
        if (e.detail === 'models' && this.models.length === 0) this.load();
      });
      // Don't eagerly load on init — wait until tab is shown
    },

    async load() {
      this.loading = true;
      try {
        const r = await fetch('/ui/api/models');
        this.models = await r.json();
      } catch (e) {
        console.error('Models load error', e);
      } finally {
        this.loading = false;
      }
    },

    async refresh() {
      await fetch('/ui/api/models/invalidate', { method: 'POST' });
      await this.load();
    },

    sortBy(key) {
      if (this.sortKey === key) {
        this.sortAsc = !this.sortAsc;
      } else {
        this.sortKey = key;
        this.sortAsc = key === 'name';
      }
    },

    async selectModel(name) {
      await fetch('/ui/api/models/set-default', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: name }),
      });
      // Switch to chat tab
      document.dispatchEvent(new CustomEvent('switch-to-chat', { detail: name }));
    },
  };
}
