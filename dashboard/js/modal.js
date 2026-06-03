// === TASK DETAIL MODAL ===

async function showTaskDetail(id) {
  const data = await api('GET', `/api/tasks/${id}`);
  if (data.error) return alert(data.error);
  document.getElementById('modal-title').textContent = '📋 ' + data.title;
  document.getElementById('md-id').textContent = data.id;
  document.getElementById('md-title').textContent = data.title;
  document.getElementById('md-status').innerHTML = `<span class="bdg ${data.status.slice(0,2)}">${data.status}</span> (folder: ${data.folder})`;
  document.getElementById('md-assignee').textContent = data.assignee || '—';
  document.getElementById('md-priority').textContent = 'P' + data.priority;
  document.getElementById('md-from').textContent = data.from_agent;
  document.getElementById('md-retries').textContent = `${data.retry_count} / ${data.max_retries}`;
  document.getElementById('md-created').textContent = data.created_at || '—';
  document.getElementById('md-context').textContent = JSON.stringify(data.context, null, 2) || '{}';
  document.getElementById('md-result').textContent = JSON.stringify(data.result, null, 2) || '{}';

  // Worktree & Merge info — detailed view
  const mqEntry = mergeQueue.find(m => m.task_id === id);
  const wtInfo = document.getElementById('md-worktree');
  
  if (mqEntry) {
    const statusColors = { 
      pending: 'var(--y)', dry_run: 'var(--blu)', resolving: 'var(--y)', 
      merged: 'var(--g)', conflict: 'var(--r)', failed: 'var(--r)' 
    };
    const statusIcons = { 
      pending: '⏳', dry_run: '🔍', resolving: '🧠', 
      merged: '✅', conflict: '⚠️', failed: '❌' 
    };
    const color = statusColors[mqEntry.status] || 'var(--m)';
    const icon = statusIcons[mqEntry.status] || '❓';
    
    let conflictInfo = '';
    if (mqEntry.conflict_level > 0) {
      const levelNames = { 1: 'Simple', 2: 'Moderate', 3: 'Complex' };
      conflictInfo = `
        <div class="field"><label>Conflict Level</label>
          <div class="val"><span style="background:var(--rb);color:var(--r);padding:2px 6px;border-radius:4px;font-size:10px">
            ${mqEntry.conflict_level} — ${levelNames[mqEntry.conflict_level] || 'Unknown'}
          </span></div>
        </div>`;
    }
    
    let errorInfo = '';
    if (mqEntry.error_log) {
      errorInfo = `
        <div class="field"><label>Error Log</label>
          <div class="val" style="font-size:10px;color:var(--r);max-height:100px;overflow-y:auto">${mqEntry.error_log}</div>
        </div>`;
    }
    
    wtInfo.innerHTML = `
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
        <div class="field"><label>Status</label>
          <div class="val"><span style="color:${color};font-size:12px">${icon} ${mqEntry.status}</span></div>
        </div>
        <div class="field"><label>Agent</label><div class="val">${mqEntry.agent_id}</div></div>
        <div class="field"><label>Branch</label><div class="val" style="font-size:10px">${mqEntry.branch || '—'}</div></div>
        <div class="field"><label>Worktree</label><div class="val" style="font-size:10px">${mqEntry.worktree || '—'}</div></div>
        <div class="field"><label>Priority</label><div class="val">P${mqEntry.priority}</div></div>
        <div class="field"><label>Created</label><div class="val" style="font-size:10px">${mqEntry.created_at || '—'}</div></div>
      </div>
      ${conflictInfo}
      ${errorInfo}`;
  } else {
    wtInfo.innerHTML = `
      <div style="color:var(--m);font-size:11px;padding:8px">
        Not in merge queue
        ${data.status === 'done' ? `<button class="btn btn-primary btn-sm" onclick="triggerMerge('${data.id || data.full_id}');closeModal()" style="margin-left:12px">🔀 Add to Queue</button>` : ''}
      </div>`;
  }

  // Dynamic actions
  const acts = document.getElementById('md-actions');
  let actionsHtml = getActions(data);
  if (data.status === 'done' && !mqEntry) {
    actionsHtml += `<button class="btn btn-primary btn-sm" onclick="triggerMerge('${data.id || data.full_id}');closeModal()">🔀 Trigger Merge</button>`;
  }
  acts.innerHTML = actionsHtml.replace(/onclick="([^"]*)"/g, 'onclick="$1;closeModal()"');
  acts.innerHTML += '<button class="btn btn-secondary" onclick="closeModal()">Close</button>';

  document.getElementById('modal').classList.add('active');
}

function closeModal() { document.getElementById('modal').classList.remove('active'); }

// Keyboard shortcut
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });
