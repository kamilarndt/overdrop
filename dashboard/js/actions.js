// === TASK ACTIONS ===

async function createTask() {
  const title = document.getElementById('new-title').value.trim();
  if (!title) return alert('Enter task title');
  const assignee = document.getElementById('new-assignee').value || null;
  const priority = parseInt(document.getElementById('new-priority').value);
  await api('POST', '/api/tasks', { title, assignee, priority, from: 'dashboard' });
  document.getElementById('new-title').value = '';
}

async function claimTask(id) {
  const agent = prompt('Agent name:', 'builder');
  if (!agent) return;
  const r = await api('POST', `/api/tasks/${id}/claim`, { agent });
  if (r.error) alert(r.error);
}

async function doneTask(id) {
  const resultStr = prompt('Result JSON (optional):', '{}');
  let result;
  try {
    result = resultStr ? JSON.parse(resultStr) : { completed_via: 'dashboard' };
  } catch(e) {
    alert('Invalid JSON: ' + e.message);
    return;
  }
  await api('POST', `/api/tasks/${id}/done`, { result });
}

async function blockTask(id) {
  const reason = prompt('Block reason:', 'Waiting for decision');
  if (!reason) return;
  await api('POST', `/api/tasks/${id}/block`, { reason });
}

async function unblockTask(id) {
  await api('POST', `/api/tasks/${id}/unblock`, {});
}

async function failTask(id) {
  const error = prompt('Error message:', 'Manual fail from dashboard');
  if (!error) return;
  await api('POST', `/api/tasks/${id}/fail`, { error });
}

async function retryTask(id) {
  await api('POST', `/api/tasks/${id}/claim`, { agent: 'retry' });
}

async function deleteTask(id) {
  if (!confirm('Delete this task?')) return;
  await api('DELETE', `/api/tasks/${id}`);
}

async function triggerMerge(id) {
  const r = await api('POST', `/api/merge-queue/${id}/trigger`);
  if (r.error) alert(r.error);
  else { fetchMergeQueue(); renderTasks(allTasks); }
}

// === MERGE QUEUE ACTIONS ===

async function processMerge(taskId) {
  const r = await api('POST', `/api/merge-queue/${taskId}/process`);
  if (r.error) alert(r.error);
  else { fetchMergeQueue(); renderTasks(allTasks); }
}

async function cancelMerge(taskId) {
  if (!confirm('Cancel this merge request?')) return;
  const r = await api('POST', `/api/merge-queue/${taskId}/cancel`);
  if (r.error) alert(r.error);
  else { fetchMergeQueue(); renderTasks(allTasks); }
}

async function retryMerge(taskId) {
  const r = await api('POST', `/api/merge-queue/${taskId}/retry`);
  if (r.error) alert(r.error);
  else { fetchMergeQueue(); renderTasks(allTasks); }
}

async function changeAgentModel(agentName, modelId) {
  try {
    const res = await fetch(`/api/agents/${agentName}/model`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: modelId })
    });
    const data = await res.json();
    if (data.ok) {
      const agent = agents.get(agentName);
      if (agent) {
        agent.model = modelId;
        updateAgents();
      }
    } else {
      alert(data.error || 'Failed to update model');
    }
  } catch(e) {
    alert('Error updating model: ' + e.message);
  }
}
