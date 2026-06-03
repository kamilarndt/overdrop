// === FILTERS ===

function applyFilters() {
  const status = document.getElementById('f-status').value;
  const agent = document.getElementById('f-agent').value;
  const search = document.getElementById('f-search').value.toLowerCase();
  let filtered = allTasks;
  if (status) filtered = filtered.filter(t => t.status === status);
  if (agent) filtered = filtered.filter(t => t.assignee === agent || t.from === agent);
  if (search) filtered = filtered.filter(t => t.title.toLowerCase().includes(search));
  renderTasks(filtered);
}

function resetFilters() {
  document.getElementById('f-status').value = '';
  document.getElementById('f-agent').value = '';
  document.getElementById('f-search').value = '';
  renderTasks(allTasks);
}
