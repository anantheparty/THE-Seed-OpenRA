/**
 * THE-Seed OpenRA Console - Main Application
 */

// ========== Configuration ==========
const CONFIG = {
    // Detect if running through reverse proxy
    isSecure: window.location.protocol === 'https:',
    host: window.location.hostname || 'localhost',
    
    // Get URLs based on environment
    get vncUrl() {
        return this.isSecure 
            ? `https://${this.host}/vnc/vnc.html?autoconnect=true&resize=scale&path=vnc/`
            : `http://${this.host}:6080/vnc.html?autoconnect=true&resize=scale`;
    },
    
    get apiWsUrl() {
        return this.isSecure 
            ? `wss://${this.host}/api/`
            : `ws://${this.host}:8090`;
    },
    
    get serviceApiUrl() {
        return this.isSecure 
            ? `https://${this.host}/api/service`
            : `http://${this.host}:8087`;
    }
};

// ========== State ==========
let ws = null;
let reconnectTimer = null;
const DEBUG_MIN_HEIGHT = 180;
const DEBUG_DEFAULT_HEIGHT = 250;
const DEBUG_HEIGHT_KEY = 'theseed.debug.height';
let activeLogFilter = 'all';

// ========== Initialization ==========
document.addEventListener('DOMContentLoaded', () => {
    initVNC();
    initDebugResize();
    initLogFilter();
    connectWebSocket();
    refreshStatus();
    
    // 每 10 秒刷新一次状态
    setInterval(refreshStatus, 10000);
    
    log('info', '控制台已启动');
});

// ========== VNC ==========
function initVNC() {
    const vncFrame = document.getElementById('vnc-frame');
    vncFrame.src = CONFIG.vncUrl;
    log('info', `VNC: ${CONFIG.vncUrl}`);
}

function toggleFullscreen() {
    const vncFrame = document.getElementById('vnc-frame');
    if (vncFrame.requestFullscreen) {
        vncFrame.requestFullscreen();
    }
}

// ========== WebSocket ==========
function connectWebSocket() {
    log('info', `连接 WebSocket: ${CONFIG.apiWsUrl}`);
    
    try {
        ws = new WebSocket(CONFIG.apiWsUrl);
        
        ws.onopen = () => {
            log('success', 'Console 已连接');
            updateStatus('ai-status-dot', 'connected');
            strategyControl('strategy_status');
        };
        
        ws.onclose = () => {
            log('error', 'Console 连接断开');
            updateStatus('ai-status-dot', '');
            // Reconnect after 5 seconds
            reconnectTimer = setTimeout(connectWebSocket, 5000);
        };
        
        ws.onerror = (err) => {
            log('error', 'WebSocket 错误');
        };
        
        ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                handleMessage(data);
            } catch (e) {
                console.error('Parse error:', e);
            }
        };
    } catch (e) {
        log('error', `连接失败: ${e.message}`);
    }
}

function handleMessage(data) {
    switch (data.type) {
        case 'init':
        case 'update':
            if (data.payload) {
                const state = data.payload.fsm_state || 'IDLE';
                document.getElementById('ai-state').textContent = state;
                updateStatus('game-status-dot', 'connected');
                
                // Add to chat if there's a message
                if (data.payload.blackboard?.action_result?.player_message) {
                    addChatMessage('ai', data.payload.blackboard.action_result.player_message);
                }
            }
            break;
        
        case 'status':
            // 处理阶段性状态更新（临时消息）
            if (data.payload) {
                const stageLabels = {
                    'received': '📩 收到指令',
                    'observing': '👁️ 观测游戏状态',
                    'thinking': '🤔 AI 思考中...',
                    'executing': '⚡ 执行代码中...',
                    'error': '❌ 错误'
                };
                const label = stageLabels[data.payload.stage] || data.payload.stage;
                const detail = data.payload.detail || '';
                updateThinkingStatus(label, detail);
                log('info', `[${data.payload.stage}] ${detail}`);
            }
            break;
        
        case 'result':
            // 处理最终结果，清除临时状态
            clearThinkingStatus();
            if (data.payload) {
                const msg = data.payload.message || (data.payload.success ? '执行成功' : '执行失败');
                addChatMessage(data.payload.success ? 'ai' : 'error', msg);
                
                // 如果有代码，显示在 debug 面板
                if (data.payload.code) {
                    log('code', `生成的代码:\n${data.payload.code}`);
                }
            }
            break;
            
        case 'log':
            if (data.payload) {
                log(data.payload.level || 'info', data.payload.message);
                // Don't add to chat here, 'result' will handle it
            }
            break;
            
        case 'trace_event':
            if (data.payload?.event_type === 'fsm_transition') {
                log('info', `状态: ${data.payload.from_state} → ${data.payload.to_state}`);
            }
            break;

        // ===== Enemy Agent Messages =====
        case 'enemy_chat':
            if (data.payload?.message) {
                addEnemyChatMessage('enemy', data.payload.message);
                log('info', `[敌方] ${data.payload.message}`);
            }
            break;

        case 'enemy_status':
            if (data.payload) {
                const enemyStageLabels = {
                    'online': '📡 上线',
                    'offline': '📴 下线',
                    'observing': '👁️ 侦查中',
                    'thinking': '🧠 策略分析',
                    'executing': '⚔️ 执行中',
                    'error': '❌ 错误'
                };
                const elabel = enemyStageLabels[data.payload.stage] || data.payload.stage;
                const edetail = data.payload.detail || '';
                updateEnemyThinkingStatus(elabel, edetail);
                log('info', `[敌方:${data.payload.stage}] ${edetail}`);
                addEnemyDebugEntry('status', `[${elabel}] ${edetail}`);
            }
            break;

        case 'enemy_result':
            clearEnemyThinkingStatus();
            if (data.payload) {
                const emsg = data.payload.message || (data.payload.success ? '执行成功' : '执行失败');
                addEnemyChatMessage(data.payload.success ? 'system' : 'error', `[行动] ${emsg}`);
                if (data.payload.code) {
                    log('code', `[敌方代码]\n${data.payload.code}`);
                }
            }
            break;

        case 'enemy_tick_detail':
            if (data.payload) {
                renderEnemyTickDetail(data.payload);
            }
            break;

        case 'enemy_agent_state':
            if (data.payload) {
                updateEnemyAgentState(data.payload);
            }
            break;

        case 'reset_done':
            addChatMessage('ai', data.payload?.message || '上下文已清空，敌方已重启');
            log('success', '新对局就绪');
            break;

        case 'strategy_state':
            if (data.payload) {
                updateStrategyState(data.payload);
            }
            break;

        case 'strategy_log':
            if (data.payload) {
                addStrategyDebugEntry(data.payload.level || 'info', data.payload.message || '');
            }
            break;

        case 'strategy_trace':
            if (data.payload) {
                log('strategy', formatStrategyTraceMessage(data.payload));
            }
            break;
    }
}

// ========== Chat ==========
function switchTab(tabName) {
    // Update tabs
    document.querySelectorAll('.chat-tabs .tab').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.tab === tabName);
    });
    
    // Update content
    document.querySelectorAll('.chat-content').forEach(content => {
        content.classList.toggle('active', content.id === `${tabName}-chat`);
    });
}

function sendCopilotCommand() {
    const input = document.getElementById('copilot-input');
    const command = input.value.trim();
    
    if (!command) return;
    
    if (!ws || ws.readyState !== WebSocket.OPEN) {
        log('error', 'Console 未连接');
        addChatMessage('error', '未连接到 AI');
        return;
    }
    
    // Add user message to chat
    addChatMessage('user', command);
    
    // Send command
    ws.send(JSON.stringify({
        type: 'command',
        payload: { command: command }
    }));
    
    log('command', `> ${command}`);
    input.value = '';
}

function quickCmd(cmd) {
    document.getElementById('copilot-input').value = cmd;
    sendCopilotCommand();
}

function addChatMessage(type, text) {
    // 先清除临时状态消息
    if (type === 'ai' || type === 'error') {
        clearThinkingStatus();
    }
    
    const messages = document.getElementById('copilot-messages');
    const msg = document.createElement('div');
    msg.className = `message ${type}`;
    msg.textContent = text;
    messages.appendChild(msg);
    messages.scrollTop = messages.scrollHeight;
    
    // Limit messages
    while (messages.children.length > 100) {
        messages.removeChild(messages.firstChild);
    }
}

// ========== Thinking Status (临时状态消息) ==========
let thinkingElement = null;

function updateThinkingStatus(label, detail) {
    const messages = document.getElementById('copilot-messages');
    
    // 如果已有 thinking 元素，更新它；否则创建新的
    if (!thinkingElement) {
        thinkingElement = document.createElement('div');
        thinkingElement.className = 'message thinking';
        messages.appendChild(thinkingElement);
    }
    
    // 更新内容
    thinkingElement.innerHTML = `
        <span class="thinking-label">${escapeHtml(label)}</span>
        <span class="thinking-detail">${escapeHtml(detail)}</span>
        <span class="thinking-dots"><span>.</span><span>.</span><span>.</span></span>
    `;
    
    messages.scrollTop = messages.scrollHeight;
}

function clearThinkingStatus() {
    if (thinkingElement) {
        thinkingElement.remove();
        thinkingElement = null;
    }
}

// ========== Enemy Chat ==========
function addEnemyChatMessage(type, text) {
    clearEnemyThinkingStatus();

    const messages = document.getElementById('enemy-messages');
    const msg = document.createElement('div');
    msg.className = `message ${type}`;
    msg.textContent = text;
    messages.appendChild(msg);
    messages.scrollTop = messages.scrollHeight;

    while (messages.children.length > 100) {
        messages.removeChild(messages.firstChild);
    }
}

function sendEnemyMessage() {
    const input = document.getElementById('enemy-input');
    const message = input.value.trim();

    if (!message) return;

    if (!ws || ws.readyState !== WebSocket.OPEN) {
        addEnemyChatMessage('error', '未连接');
        return;
    }

    addEnemyChatMessage('user', message);

    ws.send(JSON.stringify({
        type: 'enemy_chat',
        payload: { message: message }
    }));

    log('command', `[对敌方] > ${message}`);
    input.value = '';
}

// ========== Enemy Thinking Status ==========
let enemyThinkingElement = null;

function updateEnemyThinkingStatus(label, detail) {
    const messages = document.getElementById('enemy-messages');

    if (!enemyThinkingElement) {
        enemyThinkingElement = document.createElement('div');
        enemyThinkingElement.className = 'message thinking';
        messages.appendChild(enemyThinkingElement);
    }

    enemyThinkingElement.innerHTML = `
        <span class="thinking-label">${escapeHtml(label)}</span>
        <span class="thinking-detail">${escapeHtml(detail)}</span>
        <span class="thinking-dots"><span>.</span><span>.</span><span>.</span></span>
    `;

    messages.scrollTop = messages.scrollHeight;
}

function clearEnemyThinkingStatus() {
    if (enemyThinkingElement) {
        enemyThinkingElement.remove();
        enemyThinkingElement = null;
    }
}

// ========== Service Controls ==========
async function serviceAction(action) {
    log('info', `执行服务操作: ${action}`);
    addChatMessage('system', `正在执行: ${action}...`);
    
    try {
        const serviceUrl = CONFIG.isSecure 
            ? `https://${CONFIG.host}/service/api/${action}`
            : `http://${CONFIG.host}:8087/api/${action}`;
        
        const response = await fetch(serviceUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin'
        });
        
        const result = await response.json();
        
        if (result.success) {
            log('success', `${action}: ${result.message}`);
            addChatMessage('ai', result.message);
        } else {
            log('error', `${action}: ${result.message}`);
            addChatMessage('error', result.message);
        }
        
        // 刷新状态
        await refreshStatus();
        
    } catch (e) {
        log('error', `服务调用失败: ${e.message}`);
        addChatMessage('error', `服务调用失败: ${e.message}`);
    }
}

async function refreshStatus() {
    try {
        const statusUrl = CONFIG.isSecure 
            ? `https://${CONFIG.host}/service/api/status`
            : `http://${CONFIG.host}:8087/api/status`;
        
        const response = await fetch(statusUrl, { credentials: 'same-origin' });
        const status = await response.json();
        
        // 更新状态指示
        updateStatus('game-status-dot', status.game === 'running' ? 'connected' : '');
        updateStatus('ai-status-dot', status.ai === 'running' ? 'connected' : '');
        
        // 更新 Debug 面板
        document.getElementById('game-state').textContent = status.game;
        document.getElementById('vnc-state').textContent = status.vnc;
        
    } catch (e) {
        console.error('状态获取失败:', e);
    }
}

// ========== Debug Panel ==========
function toggleDebug() {
    const panel = document.getElementById('debug-panel');
    panel.classList.toggle('expanded');
}

function getDebugMaxHeight() {
    return Math.max(DEBUG_MIN_HEIGHT, Math.floor(window.innerHeight * 0.75));
}

function clampDebugHeight(height) {
    const h = Number(height) || DEBUG_DEFAULT_HEIGHT;
    return Math.max(DEBUG_MIN_HEIGHT, Math.min(getDebugMaxHeight(), Math.round(h)));
}

function setDebugHeight(height, persist = true) {
    const panel = document.getElementById('debug-panel');
    if (!panel) return;
    const clamped = clampDebugHeight(height);
    panel.style.setProperty('--debug-content-height', `${clamped}px`);
    if (persist) {
        localStorage.setItem(DEBUG_HEIGHT_KEY, String(clamped));
    }
}

function getCurrentDebugHeight() {
    const panel = document.getElementById('debug-panel');
    if (!panel) return DEBUG_DEFAULT_HEIGHT;
    const raw = getComputedStyle(panel).getPropertyValue('--debug-content-height');
    const parsed = parseInt(raw, 10);
    if (Number.isFinite(parsed)) return parsed;
    return DEBUG_DEFAULT_HEIGHT;
}

function initDebugResize() {
    const panel = document.getElementById('debug-panel');
    const resizer = document.getElementById('debug-resizer');
    if (!panel || !resizer) return;

    const saved = parseInt(localStorage.getItem(DEBUG_HEIGHT_KEY) || '', 10);
    setDebugHeight(Number.isFinite(saved) ? saved : DEBUG_DEFAULT_HEIGHT, false);

    let dragging = false;
    let startY = 0;
    let startHeight = DEBUG_DEFAULT_HEIGHT;

    const onPointerMove = (event) => {
        if (!dragging) return;
        const delta = startY - event.clientY;
        setDebugHeight(startHeight + delta, false);
    };

    const onPointerUp = () => {
        if (!dragging) return;
        dragging = false;
        document.body.classList.remove('resizing-debug');
        setDebugHeight(getCurrentDebugHeight(), true);
        window.removeEventListener('pointermove', onPointerMove);
        window.removeEventListener('pointerup', onPointerUp);
    };

    resizer.addEventListener('pointerdown', (event) => {
        if (event.button !== 0) return;
        dragging = true;
        startY = event.clientY;
        startHeight = getCurrentDebugHeight();
        panel.classList.add('expanded');
        document.body.classList.add('resizing-debug');
        window.addEventListener('pointermove', onPointerMove);
        window.addEventListener('pointerup', onPointerUp);
        event.preventDefault();
    });

    window.addEventListener('resize', () => {
        setDebugHeight(getCurrentDebugHeight(), false);
    });
}

function switchDebugTab(tabName) {
    // Update tabs
    document.querySelectorAll('.debug-tabs .tab').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.tab === tabName);
    });
    
    // Update content
    document.querySelectorAll('.debug-tab-content').forEach(content => {
        content.classList.toggle('active', content.id === `${tabName}-content`);
    });
}

function initLogFilter() {
    const select = document.getElementById('log-level');
    if (!select) return;
    activeLogFilter = select.value || 'all';
    select.addEventListener('change', () => {
        activeLogFilter = select.value || 'all';
        applyLogFilter();
    });
}

// ========== Logging ==========
function log(level, message) {
    const output = document.getElementById('log-output');
    if (!output) return;
    const entry = document.createElement('div');
    entry.className = `log-entry ${level}`;
    entry.dataset.level = String(level || 'info');
    
    const time = new Date().toLocaleTimeString('zh-CN', { hour12: false });
    entry.innerHTML = `<span class="log-time">${time}</span>${escapeHtml(message)}`;

    entry.style.display = shouldShowLogLevel(entry.dataset.level) ? '' : 'none';
    
    output.appendChild(entry);
    output.scrollTop = output.scrollHeight;
    
    // Limit log entries
    while (output.children.length > 500) {
        output.removeChild(output.firstChild);
    }
}

function clearLogs() {
    const output = document.getElementById('log-output');
    if (output) output.innerHTML = '';
}

function shouldShowLogLevel(level) {
    if (!activeLogFilter || activeLogFilter === 'all') return true;
    return level === activeLogFilter;
}

function applyLogFilter() {
    const output = document.getElementById('log-output');
    if (!output) return;
    Array.from(output.children).forEach((entry) => {
        const level = entry.dataset.level || '';
        entry.style.display = shouldShowLogLevel(level) ? '' : 'none';
    });
}

function formatStrategyTraceMessage(data) {
    const event = String(data.event || 'trace');
    const payload = data.payload || {};
    const clip = (text, n = 1600) => {
        const s = String(text || '');
        return s.length > n ? `${s.slice(0, n)}...<truncated:${s.length - n}>` : s;
    };

    if (event === 'decision_parsed') {
        const thoughts = String(payload.thoughts || '').trim();
        const orders = Array.isArray(payload.orders) ? payload.orders : [];
        return `[Strategy/${event}] thoughts=${clip(thoughts, 500) || 'N/A'}; orders=${clip(JSON.stringify(orders), 1200)}`;
    }
    if (event === 'order_dispatched') {
        return `[Strategy/${event}] ${clip(JSON.stringify(payload), 1200)}`;
    }
    if (event === 'tick_context') {
        const squad = payload.squad || {};
        const companies = Array.isArray(squad.companies) ? squad.companies : [];
        return `[Strategy/${event}] cmd=${clip(payload.user_command || '', 120)}; zones=${payload.zone_count || 0}; visible=${payload.visible_zones || 0}; companies=${clip(JSON.stringify(companies), 1200)}`;
    }

    return `[Strategy/${event}] ${clip(JSON.stringify(payload), 1400)}`;
}

// ========== Utilities ==========
function updateStatus(elementId, status) {
    const dot = document.getElementById(elementId);
    dot.classList.remove('connected', 'error');
    if (status) {
        dot.classList.add(status);
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ========== Enemy Debug Panel ==========
function enemyControl(action) {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
        log('error', 'Console 未连接，无法控制敌方');
        return;
    }

    ws.send(JSON.stringify({
        type: 'enemy_control',
        payload: { action: action }
    }));

    log('info', `敌方控制: ${action}`);
}

function enemySetInterval() {
    const input = document.getElementById('enemy-interval');
    const interval = parseFloat(input.value);

    if (isNaN(interval) || interval < 10 || interval > 300) {
        log('error', '间隔值无效 (10-300秒)');
        return;
    }

    if (!ws || ws.readyState !== WebSocket.OPEN) {
        log('error', 'Console 未连接');
        return;
    }

    ws.send(JSON.stringify({
        type: 'enemy_control',
        payload: { action: 'set_interval', interval: interval }
    }));

    log('info', `敌方间隔设置: ${interval}s`);
}

function updateEnemyAgentState(state) {
    const startBtn = document.getElementById('enemy-start-btn');
    const stopBtn = document.getElementById('enemy-stop-btn');
    const dot = document.getElementById('enemy-agent-dot');
    const stateText = document.getElementById('enemy-agent-state');
    const tickCounter = document.getElementById('enemy-tick-counter');

    if (state.running) {
        startBtn.disabled = true;
        stopBtn.disabled = false;
        dot.classList.add('connected');
        stateText.textContent = '运行中';
    } else {
        startBtn.disabled = false;
        stopBtn.disabled = true;
        dot.classList.remove('connected');
        stateText.textContent = '已停止';
    }

    tickCounter.textContent = `Tick: ${state.tick_count || 0}`;

    if (state.interval) {
        document.getElementById('enemy-interval').value = state.interval;
    }
}

function renderEnemyTickDetail(detail) {
    const logDiv = document.getElementById('enemy-debug-log');
    const time = new Date(detail.timestamp).toLocaleTimeString('zh-CN', { hour12: false });
    const icon = detail.success ? '✓' : '✗';
    const cls = detail.success ? 'success' : 'error';

    const entry = document.createElement('div');
    entry.className = `enemy-tick-entry ${cls}`;

    const header = document.createElement('div');
    header.className = 'enemy-tick-header';
    header.innerHTML = `<strong>[Tick #${detail.tick} | ${time}]</strong> ${icon} ${escapeHtml(detail.command || '?')}`;
    header.onclick = () => entry.classList.toggle('expanded');

    const body = document.createElement('div');
    body.className = 'enemy-tick-detail';

    let bodyHtml = '';
    if (detail.game_state) {
        bodyHtml += `<strong>观测:</strong>\n${escapeHtml(detail.game_state)}\n\n`;
    }
    if (detail.command) {
        bodyHtml += `<strong>指令:</strong> ${escapeHtml(detail.command)}\n`;
    }
    if (detail.code) {
        bodyHtml += `<strong>代码:</strong>\n${escapeHtml(detail.code)}\n\n`;
    }
    bodyHtml += `<strong>结果:</strong> ${detail.success ? '成功' : '失败'} - ${escapeHtml(detail.message || '')}\n`;
    if (detail.taunt) {
        bodyHtml += `<strong>嘲讽:</strong> ${escapeHtml(detail.taunt)}\n`;
    }

    body.innerHTML = bodyHtml;
    entry.appendChild(header);
    entry.appendChild(body);
    logDiv.appendChild(entry);
    logDiv.scrollTop = logDiv.scrollHeight;

    // Limit entries
    while (logDiv.children.length > 200) {
        logDiv.removeChild(logDiv.firstChild);
    }
}

function addEnemyDebugEntry(type, text) {
    const logDiv = document.getElementById('enemy-debug-log');
    if (!logDiv) return;

    const time = new Date().toLocaleTimeString('zh-CN', { hour12: false });
    const entry = document.createElement('div');
    entry.className = `log-entry ${type === 'error' ? 'error' : 'info'}`;
    entry.innerHTML = `<span class="log-time">${time}</span>${escapeHtml(text)}`;
    logDiv.appendChild(entry);
    logDiv.scrollTop = logDiv.scrollHeight;
}

function clearEnemyDebugLog() {
    document.getElementById('enemy-debug-log').innerHTML = '';
}

// ========== Strategy Debug Panel ==========
function strategyControl(action, extraPayload = {}) {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
        log('error', 'Console 未连接，无法控制战略栈');
        return;
    }

    ws.send(JSON.stringify({
        type: 'enemy_control',
        payload: { action, ...extraPayload }
    }));
}

function strategySendCommand() {
    const input = document.getElementById('strategy-command-input');
    const command = (input.value || '').trim();
    if (!command) return;

    if (!ws || ws.readyState !== WebSocket.OPEN) {
        addStrategyDebugEntry('error', 'Console 未连接，无法发送战略指令');
        return;
    }

    strategyControl('strategy_cmd', { command });
    addStrategyDebugEntry('info', `指令已发送: ${command}`);
    setTimeout(() => strategyControl('strategy_status'), 120);
    input.value = '';
}

let _lastStrategyError = '';
let _lastStrategyCommand = '';

function updateStrategyState(state) {
    const startBtn = document.getElementById('strategy-start-btn');
    const stopBtn = document.getElementById('strategy-stop-btn');
    const dot = document.getElementById('strategy-state-dot');
    const text = document.getElementById('strategy-state-text');

    if (!startBtn || !stopBtn || !dot || !text) return;

    if (!state.available) {
        startBtn.disabled = true;
        stopBtn.disabled = true;
        dot.classList.remove('connected');
        text.textContent = '不可用';
        renderStrategyRoster([], state.unassigned_count || 0, state.player_count || 0, false);
        if (state.last_error && state.last_error !== _lastStrategyError) {
            addStrategyDebugEntry('error', state.last_error);
            _lastStrategyError = state.last_error;
        }
        return;
    }

    if (state.running) {
        startBtn.disabled = true;
        stopBtn.disabled = false;
        dot.classList.add('connected');
        text.textContent = '运行中';
    } else {
        startBtn.disabled = false;
        stopBtn.disabled = true;
        dot.classList.remove('connected');
        text.textContent = '已停止';
    }

    renderStrategyRoster(
        state.companies || [],
        state.unassigned_count || 0,
        state.player_count || 0,
        !!state.running
    );

    if (state.last_error && state.last_error !== _lastStrategyError) {
        addStrategyDebugEntry('error', state.last_error);
        _lastStrategyError = state.last_error;
    }
    if (!state.last_error) {
        _lastStrategyError = '';
    }

    if (state.last_command && state.last_command !== _lastStrategyCommand) {
        addStrategyDebugEntry('info', `当前指令: ${state.last_command}`);
        _lastStrategyCommand = state.last_command;
    }
}

function renderStrategyRoster(companies, unassignedCount = 0, playerCount = 0, running = false) {
    const container = document.getElementById('strategy-roster');
    if (!container) return;

    if (!Array.isArray(companies) || companies.length === 0) {
        container.innerHTML = running
            ? '<p class="placeholder">战略栈已启动，等待单位同步到连队...</p>'
            : '<p class="placeholder">战略栈未启动。点击“启动战略栈”或发送指令自动启动。</p>';
        return;
    }

    let html = `
        <div class="strategy-roster-summary">
            连队数: ${companies.length} | 未分配: ${unassignedCount} | 玩家直控: ${playerCount}
        </div>
    `;

    html += companies.map((company) => {
        const center = company.center && typeof company.center === 'object'
            ? `(${company.center.x ?? '-'}, ${company.center.y ?? '-'})`
            : '-';
        const members = Array.isArray(company.members) ? company.members : [];
        const membersHtml = members.length > 0
            ? members.map((m) => {
                const pos = m.position && typeof m.position === 'object'
                    ? `(${m.position.x ?? '-'},${m.position.y ?? '-'})`
                    : '(-,-)';
                return `<span class="strategy-member">#${m.id} ${escapeHtml(m.type || '?')} HP${m.hp_percent ?? 0}% ${pos}</span>`;
            }).join('')
            : '<span class="strategy-member">空</span>';

        return `
            <div class="strategy-company">
                <div class="strategy-company-title">${escapeHtml(company.name || `Company ${company.id}`)} (${company.id})</div>
                <div class="strategy-company-meta">人数: ${company.count ?? 0} | 战力: ${company.power ?? 0} | 权重: ${company.weight ?? 1} | 中心: ${center}</div>
                <div class="strategy-members">${membersHtml}</div>
            </div>
        `;
    }).join('');

    container.innerHTML = html;
}

function addStrategyDebugEntry(level, text) {
    const logDiv = document.getElementById('strategy-debug-log');
    if (!logDiv || !text) return;

    const time = new Date().toLocaleTimeString('zh-CN', { hour12: false });
    const entry = document.createElement('div');
    entry.className = `log-entry ${level === 'error' ? 'error' : 'info'}`;
    entry.innerHTML = `<span class="log-time">${time}</span>${escapeHtml(text)}`;
    logDiv.appendChild(entry);
    logDiv.scrollTop = logDiv.scrollHeight;

    while (logDiv.children.length > 300) {
        logDiv.removeChild(logDiv.firstChild);
    }
}

function clearStrategyDebugLog() {
    const logDiv = document.getElementById('strategy-debug-log');
    if (logDiv) {
        logDiv.innerHTML = '';
    }
}

// ========== 新对局：清空所有上下文并重启敌方 ==========
function resetAndStartGame() {
    // 1. 清空前端所有聊天和日志
    document.getElementById('copilot-messages').innerHTML = '';
    document.getElementById('enemy-messages').innerHTML = '';
    document.getElementById('log-output').innerHTML = '';
    document.getElementById('enemy-debug-log').innerHTML = '';
    clearThinkingStatus();
    clearEnemyThinkingStatus();

    // 2. 通知后端清空上下文并重启敌方
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
            type: 'enemy_control',
            payload: { action: 'reset_all' }
        }));
        addChatMessage('system', '新对局：上下文已清空，敌方AI重启中...');
        log('info', '新对局：清空所有上下文，重启敌方AI');
    } else {
        addChatMessage('error', 'Console 未连接');
    }
}
