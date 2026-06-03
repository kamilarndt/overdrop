// === RENDER FUNCTIONS ===

function updateAgents() {
  const l = document.getElementById('agent-list'), c = document.getElementById('ac');
  const activeCount = Array.from(agents.values()).filter(a => a.live_status === 'thinking' || a.tasks_active > 0).length;
  c.textContent = activeCount + '/' + agents.size;

  if (!agents.size) {
    l.innerHTML = '<div style="grid-column:1/-1;text-align:center;color:var(--m);padding:16px">🤖 No agents loaded</div>';
    return;
  }

  const groups = { orchestrator: [], pipeline: [], specialist: [], factory: [], monitoring: [], hermes: [], unknown: [] };
  agents.forEach(a => {
    const g = a.type || 'unknown';
    if (groups[g]) groups[g].push(a);
    else groups.unknown.push(a);
  });

  const typeIcons = { orchestrator: '🎯', pipeline: '⚙️', specialist: '🔧', factory: '🏭', monitoring: '📊', hermes: '⚡', unknown: '❓' };
  const typeNames = { orchestrator: 'Orchestrator', pipeline: 'Pipeline', specialist: 'Specialist', factory: 'Factory', monitoring: 'Monitoring', hermes: 'Hermes', unknown: 'Other' };

  let html = '';
  for (const [type, list] of Object.entries(groups)) {
    if (!list.length) continue;
    html += `<div style="grid-column:1/-1;font-size:10px;color:var(--m);text-transform:uppercase;letter-spacing:.5px;padding:8px 0 4px;border-bottom:1px solid var(--b)">${typeIcons[type]} ${typeNames[type]}</div>`;

    list.sort((a, b) => b.tasks_active - a.tasks_active || b.tasks_total - a.tasks_total);
    list.forEach(a => {
      const isActive = a.live_status === 'thinking' || a.tasks_active > 0;
      const statusColor = isActive ? 'var(--g)' : a.manifest_status === 'active' ? 'var(--y)' : 'var(--m)';
      const statusDot = isActive ? '●' : a.manifest_status === 'active' ? '◐' : '○';
      const caps = (a.capabilities || []).slice(0, 3).map(c => `<span style="background:var(--s2);padding:1px 5px;border-radius:4px;font-size:8px">${c}</span>`).join(' ');
      const currentTask = a.current_task ? `<div style="font-size:9px;color:var(--blu);margin-top:3px">→ ${a.current_task}</div>` : '';
      const stats = a.tasks_total > 0 ? `<div style="font-size:8px;color:var(--m);margin-top:3px;display:flex;gap:6px"><span>✅${a.tasks_done}</span><span>🔧${a.tasks_active}</span><span>📊${a.tasks_total}</span></div>` : '';

      const modelOptions = allModels.map(m =>
        `<option value="${m.id}" ${m.id === a.model ? 'selected' : ''}>${m.name} (${m.cost})</option>`
      ).join('');
      const modelSelect = `<select onchange="changeAgentModel('${a.name}', this.value)" style="background:var(--s2);border:1px solid var(--b);color:var(--blu);padding:2px 4px;border-radius:4px;font-size:8px;font-family:var(--mon);margin-top:3px;width:100%;cursor:pointer">${modelOptions}</select>`;

      html += `<div class="ai" style="flex-direction:column;align-items:flex-start;gap:3px;padding:8px 10px;border-left:2px solid ${statusColor}">
        <div style="display:flex;align-items:center;gap:5px;width:100%">
          <span style="color:${statusColor};font-size:10px">${statusDot}</span>
          <span style="font-family:var(--mon);font-size:11px;color:var(--t)">${a.name}</span>
        </div>
        <div style="font-size:9px;color:var(--m);line-height:1.3">${a.description ? a.description.slice(0, 60) : ''}</div>
        <div style="display:flex;gap:3px;flex-wrap:wrap">${caps}</div>
        ${modelSelect}
        ${currentTask}
        ${stats}
      </div>`;
    });
  }
  l.innerHTML = html;
}

function renderMergeQueue() {
  const el = document.getElementById('merge-queue-list');
  if (!mergeQueue.length) { el.innerHTML = ''; return; }
  el.innerHTML = mergeQueue.map(m => {
    const statusColors = { pending: 'var(--y)', dry_run: 'var(--blu)', resolving: 'var(--y)' };
    const statusIcons = { pending: '⏳', dry_run: '🔍', resolving: '🧠' };
    const color = statusColors[m.status] || 'var(--m)';
    const icon = statusIcons[m.status] || '❓';
    return `<div class="ai" style="border-left:3px solid ${color};flex-direction:column;gap:4px">
      <div style="display:flex;align-items:center;gap:6px">
        <span style="color:${color};font-size:12px">${icon}</span>
        <span style="font-family:var(--mon);font-size:11px;color:var(--t)">${(m.task_id||'').slice(0,8)}</span>
        <span style="font-size:10px;color:var(--m);margin-left:auto">${m.status}</span>
      </div>
      <div style="font-size:9px;color:var(--m)">Agent: ${m.agent_id} | Branch: ${(m.branch||'').slice(0,30)}</div>
      <div style="font-size:9px;color:var(--m)">Priority: P${m.priority} | Conflict: ${m.conflict_level||0}</div>
    </div>`;
  }).join('');
}

function renderTasks(tasks) {
  const tbody = document.getElementById('tt');
  if (!tasks.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="em"><div class="ic">📭</div><p>No tasks</p></td></tr>';
    return;
  }
  tbody.innerHTML = tasks.map(t => {
    const bdg = { inbox: 'in', active: 'ac', claimed: 'cl', done: 'dn', failed: 'fl', blocked: 'bl', feedback: 'fb' }[t.status] || 'in';
    const actions = getActions(t);
    const mqEntry = mergeQueue.find(m => m.task_id === (t.full_id || t.id));
    let mergeBadge = '';
    if (mqEntry) {
      const mColors = { pending: 'var(--y)', dry_run: 'var(--blu)', resolving: 'var(--y)', merged: 'var(--g)', conflict: 'var(--r)' };
      const mColor = mColors[mqEntry.status] || 'var(--m)';
      mergeBadge = `<span style="background:${mColor};color:#fff;padding:2px 6px;border-radius:4px;font-size:9px">${mqEntry.status}</span>`;
    } else if (t.status === 'done') {
      mergeBadge = `<button class="btn btn-secondary btn-sm" onclick="triggerMerge('${t.full_id || t.id}')" style="font-size:9px;padding:2px 6px">🔀 Merge</button>`;
    } else {
      mergeBadge = '<span style="color:var(--m);font-size:9px">—</span>';
    }
    return `<tr data-task="${t.full_id || t.id}" onclick="showTaskDetail('${t.full_id || t.id}')">
      <td>${t.id}</td><td>${t.title}</td>
      <td><span class="bdg ${bdg}">${t.status}</span></td>
      <td>${t.from}</td><td>${t.assignee}</td><td>P${t.priority || 5}</td>
      <td>${mergeBadge}</td>
      <td><div class="actions" onclick="event.stopPropagation()">${actions}</div></td></tr>`;
  }).join('');
}

function getActions(t) {
  let btns = '';
  if (t.status === 'inbox') btns += `<button class="btn btn-primary btn-sm" onclick="claimTask('${t.full_id}')">Claim</button>`;
  if (t.status === 'active' || t.status === 'claimed') {
    btns += `<button class="btn btn-success btn-sm" onclick="doneTask('${t.full_id}')">Done</button>`;
    btns += `<button class="btn btn-warning btn-sm" onclick="blockTask('${t.full_id}')">Block</button>`;
    btns += `<button class="btn btn-danger btn-sm" onclick="failTask('${t.full_id}')">Fail</button>`;
  }
  if (t.status === 'blocked') btns += `<button class="btn btn-secondary btn-sm" onclick="unblockTask('${t.full_id}')">Unblock</button>`;
  if (t.status === 'failed' && t.retry_count < (t.max_retries || 3)) {
    btns += `<button class="btn btn-primary btn-sm" onclick="retryTask('${t.full_id}')">Retry</button>`;
  }
  btns += `<button class="btn btn-danger btn-sm" onclick="deleteTask('${t.full_id}')" style="opacity:0.6">✕</button>`;
  return btns;
}

function populateAgentFilter() {
  const sel = document.getElementById('f-agent');
  const assigneeSel = document.getElementById('new-assignee');
  const agentSet = new Set();
  allTasks.forEach(t => { if (t.assignee && t.assignee !== '-') agentSet.add(t.assignee); if (t.from) agentSet.add(t.from); });
  const agents = [...agentSet].sort();
  sel.innerHTML = '<option value="">All agents</option>' + agents.map(a => `<option value="${a}">${a}</option>`).join('');
  assigneeSel.innerHTML = '<option value="">Unassigned</option>' + agents.map(a => `<option value="${a}">${a}</option>`).join('');
}
