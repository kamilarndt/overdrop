// === APP INITIALIZATION ===

// Stats from SSE
s.addEventListener('stats', e => {
  const d = JSON.parse(e.data);
  document.getElementById('si').textContent = d.inbox || 0;
  document.getElementById('sa').textContent = d.active || 0;
  document.getElementById('sd').textContent = d.done || 0;
  document.getElementById('sf').textContent = d.failed || 0;
  document.getElementById('sbn').textContent = (d.blocked || 0);
  document.getElementById('ri').textContent = d.cycle || '—';
});

// Tasks from SSE
s.addEventListener('tasks', e => {
  allTasks = JSON.parse(e.data);
  renderTasks(allTasks);
  populateAgentFilter();
});

// Merge queue updates from SSE (real-time)
s.addEventListener('merge_queue', e => {
  const update = JSON.parse(e.data);
  console.log('Merge queue update:', update);
  // Refetch merge queue on any update
  fetchMergeQueue();
});

// Activity log from SSE
s.addEventListener('log', e => {
  const m = JSON.parse(e.data), log = document.getElementById('log');
  const tc = { dispatch: 'di', worker_done: 'wd', escalate: 'es', broadcast: 'br', progress_update: 'tl' }[m.type] || '';
  log.insertAdjacentHTML('afterbegin', `<div class="le"><span class="lt">${(m.time || '').slice(11, 19)}</span><span class="la">${m.actor}</span><span class="ly ${tc}">${m.type}</span><span class="lm">${m.msg}</span></div>`);
  if (log.children.length > 80) log.lastChild.remove();
});

// Initialize on load
fetchModels();
fetchAgents();
fetchMergeQueue();
setInterval(fetchAgents, 5000);
// No need for setInterval(fetchMergeQueue) — SSE handles updates now
