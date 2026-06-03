// === STATE ===
let allTasks = [];
let agents = new Map();
let allModels = [];
let mergeQueue = [];

// SSE connection
const s = new EventSource('/events');
const wd = document.getElementById('wd');
const wt = document.getElementById('wt');

s.addEventListener('open', () => { wd.className = 'dot gn'; wt.textContent = 'Live'; });
s.onerror = () => { wd.className = 'dot rd'; wt.textContent = 'Retrying'; };
