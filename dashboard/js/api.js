// === API HELPER ===
async function api(method, url, body = null) {
  try {
    const opts = { method, headers: { 'Content-Type': 'application/json' } };
    if (body) opts.body = JSON.stringify(body);
    const res = await fetch(url, opts);
    const data = await res.json();
    if (!res.ok) {
      console.error(`API error: ${method} ${url} → ${res.status}`, data);
    }
    return data;
  } catch(e) {
    console.error(`API request failed: ${method} ${url}`, e);
    return { error: e.message || 'Network error' };
  }
}

// === FETCH FUNCTIONS ===
async function fetchModels() {
  try {
    const res = await fetch('/api/models');
    allModels = await res.json();
  } catch(e) {}
}

async function fetchAgents() {
  try {
    const res = await fetch('/api/agents');
    const data = await res.json();
    agents.clear();
    data.forEach(a => agents.set(a.name, a));
    updateAgents();
  } catch(e) {}
}

async function fetchMergeQueue() {
  try {
    const res = await fetch('/api/merge-queue');
    mergeQueue = await res.json();
    document.getElementById('smq').textContent = mergeQueue.length;
    document.getElementById('merge-queue-section').style.display = mergeQueue.length ? '' : 'none';
    renderMergeQueue();
  } catch(e) {}
}
