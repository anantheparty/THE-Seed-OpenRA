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
const DEBUG_DEFAULT_HEIGHT = 1000;
const STRATEGY_DEFAULT_HEIGHT = 1000;
const DEBUG_HEIGHT_KEY = 'theseed.debug.height';
let activeLogFilter = 'all';
const STRATEGY_STATUS_INTERVAL_MS = 1000;
let strategyStatusPollTimer = null;
let strategyMapLastState = null;
let strategyMapHitPoints = [];
let strategyMapTransform = null;
let strategyHoverCompanyId = '';
let enemyAgentRunning = false;

// ========== Initialization ==========
document.addEventListener('DOMContentLoaded', () => {
    initMainLayoutHeight();
    initVNC();
    initDebugResize();
    initLogFilter();
    initStrategyMapInteraction();
    initStrategyStatusPolling();
    connectWebSocket();
    refreshStatus();
    
    // 每 10 秒刷新一次状态
    setInterval(refreshStatus, 10000);
    
    log('info', '控制台已启动');
});

function initMainLayoutHeight() {
    const update = () => {
        const bar = document.querySelector('.service-bar');
        const h = bar ? Math.ceil(bar.getBoundingClientRect().height) : 56;
        document.documentElement.style.setProperty('--service-bar-height', `${h}px`);
    };
    update();
    window.addEventListener('resize', update);
}

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
            enemyControl('status');
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
                    'fallback': '🔁 NLU失败，回退LLM中...',
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
                const nlu = data.payload.nlu || {};
                const nluReason = String(nlu.reason || '');
                if (nluReason.startsWith('nlu_route_exec_failed:')) {
                    addChatMessage('system', 'NLU 规则执行失败，已自动回退到 LLM 重试');
                    log('info', `[副官] 自动回退LLM: ${nluReason}`);
                }

                const msg = data.payload.message || (data.payload.success ? '执行成功' : '执行失败');
                addChatMessage(data.payload.success ? 'ai' : 'error', msg);
                log(data.payload.success ? 'success' : 'error', `[副官结果] ${msg}`);
                
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

function quickToggleEnemyAgent() {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
        log('error', 'Console 未连接，无法切换敌方Agent');
        addChatMessage('error', '未连接到 Console');
        return;
    }
    enemyControl(enemyAgentRunning ? 'stop' : 'start');
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
    if (isStrategyPanelActive()) {
        strategyControl('strategy_status');
    }
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

    if (tabName === 'strategy-debug' && ws && ws.readyState === WebSocket.OPEN) {
        ensureStrategyPanelHeight();
        strategyControl('strategy_status');
    }
}

function ensureStrategyPanelHeight() {
    const current = getCurrentDebugHeight();
    if (current < STRATEGY_DEFAULT_HEIGHT) {
        setDebugHeight(STRATEGY_DEFAULT_HEIGHT, true);
    }
}

function isStrategyPanelActive() {
    const panel = document.getElementById('debug-panel');
    const strategyTab = document.querySelector('.debug-tabs .tab[data-tab="strategy-debug"]');
    return !!(
        panel &&
        strategyTab &&
        panel.classList.contains('expanded') &&
        strategyTab.classList.contains('active')
    );
}

function initStrategyStatusPolling() {
    if (strategyStatusPollTimer) {
        clearInterval(strategyStatusPollTimer);
    }
    strategyStatusPollTimer = setInterval(() => {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        if (!isStrategyPanelActive()) return;
        strategyControl('strategy_status');
    }, STRATEGY_STATUS_INTERVAL_MS);
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
    const normalizedLevel = String(level || 'info');
    const entry = document.createElement('div');
    entry.className = `log-entry ${normalizedLevel}`;
    entry.dataset.level = normalizedLevel;
    
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

    if (activeLogFilter === 'info') {
        return ['info', 'success', 'command', 'code'].includes(level);
    }
    if (activeLogFilter === 'error') {
        return level === 'error';
    }
    if (activeLogFilter === 'strategy') {
        return level === 'strategy';
    }
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
    const topDot = document.getElementById('enemy-status-dot');
    const topLabel = document.getElementById('enemy-status-label');
    const quickToggleBtn = document.getElementById('quick-enemy-toggle-btn');

    enemyAgentRunning = Boolean(state.running);

    startBtn.disabled = enemyAgentRunning;
    stopBtn.disabled = !enemyAgentRunning;
    dot.classList.toggle('connected', enemyAgentRunning);
    stateText.textContent = enemyAgentRunning ? '运行中' : '已停止';

    if (topDot) {
        topDot.classList.toggle('connected', enemyAgentRunning);
    }
    if (topLabel) {
        topLabel.textContent = enemyAgentRunning ? 'Enemy Agent: ON' : 'Enemy Agent: OFF';
    }
    if (quickToggleBtn) {
        quickToggleBtn.textContent = enemyAgentRunning ? '👹 停止敌方' : '👹 启动敌方';
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
    const dot = document.getElementById('strategy-state-dot');
    const text = document.getElementById('strategy-state-text');

    if (!dot || !text) return;

    const bridge = (state && typeof state === 'object' && state.job_bridge && typeof state.job_bridge === 'object')
        ? state.job_bridge
        : {};
    const attackJobCount = Number(bridge.attack_job_count || 0) || 0;
    const controlledCount = bridge.controlled_count == null ? null : Number(bridge.controlled_count || 0);

    if (!state.available) {
        dot.classList.remove('connected');
        text.textContent = '不可用';
        renderStrategyRoster([], state.unassigned_count || 0, state.player_count || 0, false, attackJobCount, controlledCount);
        renderStrategyMap(state || {});
        if (state.last_error && state.last_error !== _lastStrategyError) {
            addStrategyDebugEntry('error', state.last_error);
            _lastStrategyError = state.last_error;
        }
        return;
    }

    if (state.running) {
        dot.classList.add('connected');
        text.textContent = `自动运行中（AttackJob:${attackJobCount}）`;
    } else {
        dot.classList.remove('connected');
        text.textContent = attackJobCount > 0 ? `启动中（AttackJob:${attackJobCount}）` : '自动待机（等待 AttackJob）';
    }

    renderStrategyRoster(
        state.companies || [],
        state.unassigned_count || 0,
        state.player_count || 0,
        !!state.running,
        attackJobCount,
        controlledCount
    );
    renderStrategyMap(state || {});

    if (state.last_error && state.last_error !== _lastStrategyError) {
        addStrategyDebugEntry('error', state.last_error);
        _lastStrategyError = state.last_error;
    }
    if (!state.last_error) {
        _lastStrategyError = '';
    }

    if (state.last_command && state.last_command !== _lastStrategyCommand) {
        addStrategyDebugEntry('info', `战略指令: ${state.last_command}`);
        _lastStrategyCommand = state.last_command;
    }
}

function renderStrategyRoster(
    companies,
    unassignedCount = 0,
    playerCount = 0,
    running = false,
    attackJobCount = 0,
    controlledCount = null
) {
    const container = document.getElementById('strategy-roster');
    if (!container) return;

    if (!Array.isArray(companies) || companies.length === 0) {
        if (running) {
            container.innerHTML = '<p class="placeholder">战略栈运行中，等待 AttackJob 单位同步到连队...</p>';
        } else if (attackJobCount > 0) {
            container.innerHTML = `<p class="placeholder">AttackJob 单位=${attackJobCount}，战略栈正在自动启动...</p>`;
        } else {
            container.innerHTML = '<p class="placeholder">自动待机：请先用副官下达进攻命令（dispatch_attack）分配单位。</p>';
        }
        return;
    }

    let html = `
        <div class="strategy-roster-summary">
            连队数: ${companies.length}
            | AttackJob: ${attackJobCount}
            | 战略受控: ${controlledCount == null ? '-' : controlledCount}
            | 未分配: ${unassignedCount}
            | 玩家直控: ${playerCount}
        </div>
    `;

    html += companies.map((company) => {
        const center = company.center && typeof company.center === 'object'
            ? `(${company.center.x ?? '-'}, ${company.center.y ?? '-'})`
            : '-';
        const target = company.target && typeof company.target === 'object'
            ? `(${company.target.x ?? '-'}, ${company.target.y ?? '-'})`
            : '-';
        const status = company.order_status ? `${escapeHtml(company.order_status)}` : 'idle';
        const pending = company.pending_order?.type ? `待执行:${escapeHtml(company.pending_order.type)}` : '';
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
                <div class="strategy-company-meta">人数: ${company.count ?? 0} | 战力: ${company.power ?? 0} | 权重: ${company.weight ?? 1} | 状态: ${status}${pending ? ` | ${pending}` : ''}</div>
                <div class="strategy-company-meta">中心: ${center} | 目标: ${target}</div>
                <div class="strategy-members">${membersHtml}</div>
            </div>
        `;
    }).join('');

    container.innerHTML = html;
}

function initStrategyMapInteraction() {
    const canvas = document.getElementById('strategy-map-canvas');
    const hoverBox = document.getElementById('strategy-map-hover');
    if (!canvas || !hoverBox) return;

    const handleHover = (event) => {
        const rect = canvas.getBoundingClientRect();
        if (!rect.width || !rect.height || strategyMapHitPoints.length === 0) {
            const changed = strategyHoverCompanyId !== '';
            strategyHoverCompanyId = '';
            hoverBox.textContent = '鼠标悬停连队中心/目标，查看详细信息';
            canvas.style.cursor = 'default';
            if (changed && strategyMapLastState) renderStrategyMap(strategyMapLastState);
            return;
        }

        const px = (event.clientX - rect.left) * (canvas.width / rect.width);
        const py = (event.clientY - rect.top) * (canvas.height / rect.height);

        let nearest = null;
        let nearestDist2 = Infinity;
        strategyMapHitPoints.forEach((item) => {
            const dx = px - item.x;
            const dy = py - item.y;
            const d2 = dx * dx + dy * dy;
            if (d2 <= item.radius * item.radius && d2 < nearestDist2) {
                nearest = item;
                nearestDist2 = d2;
            }
        });

        if (!nearest) {
            const changed = strategyHoverCompanyId !== '';
            strategyHoverCompanyId = '';
            hoverBox.textContent = '鼠标悬停连队中心/目标，查看详细信息';
            canvas.style.cursor = 'default';
            if (changed && strategyMapLastState) renderStrategyMap(strategyMapLastState);
            return;
        }

        const nextId = String(nearest.company.id || '');
        const changed = strategyHoverCompanyId !== nextId;
        strategyHoverCompanyId = nextId;
        hoverBox.textContent = formatStrategyHoverText(nearest.company, nearest.kind);
        canvas.style.cursor = 'pointer';
        if (changed && strategyMapLastState) renderStrategyMap(strategyMapLastState);
    };

    canvas.addEventListener('mousemove', handleHover);
    canvas.addEventListener('mouseleave', () => {
        const changed = strategyHoverCompanyId !== '';
        strategyHoverCompanyId = '';
        canvas.style.cursor = 'default';
        hoverBox.textContent = '鼠标悬停连队中心/目标，查看详细信息';
        if (changed && strategyMapLastState) renderStrategyMap(strategyMapLastState);
    });

    window.addEventListener('resize', () => {
        if (strategyMapLastState) {
            renderStrategyMap(strategyMapLastState);
        }
    });
}

function strategyCompanyColor(companyId, fallbackIndex = 0) {
    const palette = [
        '#60a5fa', '#f59e0b', '#22c55e', '#e879f9', '#f43f5e', '#a3e635', '#38bdf8', '#f97316'
    ];
    const text = String(companyId || fallbackIndex);
    let hash = 0;
    for (let i = 0; i < text.length; i += 1) {
        hash = (hash * 31 + text.charCodeAt(i)) >>> 0;
    }
    return palette[hash % palette.length];
}

function formatPoint(point) {
    if (!point || typeof point !== 'object') return '-';
    const x = Number.isFinite(Number(point.x)) ? Number(point.x) : '-';
    const y = Number.isFinite(Number(point.y)) ? Number(point.y) : '-';
    return `(${x}, ${y})`;
}

function formatStrategyHoverText(company, kind) {
    const tag = kind === 'target' ? '目标点' : '连队中心';
    const status = company.order_status ? ` | 状态: ${company.order_status}` : '';
    const pending = company.pending_order?.type ? ` | 待执行: ${company.pending_order.type}` : '';
    return [
        `[${tag}] ${company.name || `Company ${company.id}`} (${company.id})`,
        `人数: ${company.count ?? 0} | 战力: ${company.power ?? 0}${status}${pending}`,
        `中心: ${formatPoint(company.center)} | 目标: ${formatPoint(company.target)}`,
    ].join('\n');
}

function setStrategyMapEmpty(text) {
    const canvas = document.getElementById('strategy-map-canvas');
    const empty = document.getElementById('strategy-map-empty');
    const hover = document.getElementById('strategy-map-hover');
    const meta = document.getElementById('strategy-map-meta');
    const mapTime = document.getElementById('strategy-map-time');
    if (empty) {
        empty.style.display = 'flex';
        empty.textContent = text;
    }
    if (hover) {
        hover.textContent = '鼠标悬停连队中心/目标，查看详细信息';
    }
    if (meta) meta.textContent = '地图: --';
    if (mapTime) mapTime.textContent = '更新时间: --';
    strategyMapHitPoints = [];
    strategyMapTransform = null;
    if (canvas) {
        const ctx = canvas.getContext('2d');
        if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
    }
}

function renderStrategyMap(state) {
    strategyMapLastState = state;
    const map = state.map || null;
    const companies = Array.isArray(state.companies) ? state.companies : [];

    const canvas = document.getElementById('strategy-map-canvas');
    const wrap = document.getElementById('strategy-map-canvas-wrap');
    const empty = document.getElementById('strategy-map-empty');
    const meta = document.getElementById('strategy-map-meta');
    const mapTime = document.getElementById('strategy-map-time');
    if (!canvas || !wrap || !meta || !mapTime) return;

    if (!map || !map.ok) {
        setStrategyMapEmpty(map?.error ? `map_query 失败: ${map.error}` : '等待 map_query 数据...');
        return;
    }

    const width = Number(map.width || 0);
    const height = Number(map.height || 0);
    const fogRows = Array.isArray(map.fog_rows) ? map.fog_rows : [];
    if (width <= 0 || height <= 0 || fogRows.length === 0) {
        setStrategyMapEmpty('地图尺寸无效，等待下一次刷新...');
        return;
    }

    const dpr = window.devicePixelRatio || 1;
    const rect = wrap.getBoundingClientRect();
    const renderW = Math.max(120, Math.floor(rect.width * dpr));
    const renderH = Math.max(120, Math.floor(rect.height * dpr));
    if (canvas.width !== renderW || canvas.height !== renderH) {
        canvas.width = renderW;
        canvas.height = renderH;
    }

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const scale = Math.max(1, Math.min(renderW / width, renderH / height));
    const drawW = width * scale;
    const drawH = height * scale;
    const offsetX = Math.floor((renderW - drawW) * 0.5);
    const offsetY = Math.floor((renderH - drawH) * 0.5);

    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, renderW, renderH);
    ctx.fillStyle = '#070b10';
    ctx.fillRect(0, 0, renderW, renderH);

    for (let y = 0; y < height; y += 1) {
        const row = String(fogRows[y] || '');
        for (let x = 0; x < width; x += 1) {
            const code = row.charAt(x) || '0';
            if (code === '2') ctx.fillStyle = '#587b5a';
            else if (code === '1') ctx.fillStyle = '#37465a';
            else ctx.fillStyle = '#0f141b';
            const px = offsetX + x * scale;
            const py = offsetY + y * scale;
            ctx.fillRect(px, py, Math.ceil(scale), Math.ceil(scale));
        }
    }

    const toCanvasPoint = (point) => {
        if (!point || typeof point !== 'object') return null;
        const x = Number(point.x);
        const y = Number(point.y);
        if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
        return {
            x: offsetX + (x + 0.5) * scale,
            y: offsetY + (y + 0.5) * scale,
        };
    };

    (map.resources || []).forEach((node) => {
        const p = toCanvasPoint(node);
        if (!p) return;
        ctx.fillStyle = node.resource_type === 'Gem' ? '#f472b6' : '#facc15';
        ctx.fillRect(p.x - 1.5, p.y - 1.5, 3, 3);
    });
    (map.oil_wells || []).forEach((well) => {
        const p = toCanvasPoint(well);
        if (!p) return;
        ctx.fillStyle = '#22d3ee';
        ctx.fillRect(p.x - 2, p.y - 2, 4, 4);
    });

    strategyMapHitPoints = [];
    companies.forEach((company, index) => {
        const color = strategyCompanyColor(company.id, index);
        const hovered = strategyHoverCompanyId && String(company.id) === String(strategyHoverCompanyId);
        const center = toCanvasPoint(company.center);
        const targetPoint = toCanvasPoint(company.target || company.pending_order?.target);
        const members = Array.isArray(company.members) ? company.members : [];

        members.forEach((m) => {
            const mp = toCanvasPoint(m.position);
            if (!mp) return;
            ctx.beginPath();
            ctx.arc(mp.x, mp.y, Math.max(2.1, scale * 0.36) + (hovered ? 0.8 : 0), 0, Math.PI * 2);
            ctx.fillStyle = hovered ? 'rgba(248, 250, 252, 0.96)' : 'rgba(226, 232, 240, 0.76)';
            ctx.fill();
        });

        if (center && targetPoint) {
            ctx.beginPath();
            ctx.moveTo(center.x, center.y);
            ctx.lineTo(targetPoint.x, targetPoint.y);
            ctx.strokeStyle = hovered ? '#f8fafc' : `${color}cc`;
            ctx.lineWidth = Math.max(1.6, scale * 0.22) + (hovered ? 0.8 : 0);
            ctx.stroke();
        }

        if (targetPoint) {
            const size = Math.max(5.5, scale * 0.72) + (hovered ? 1.5 : 0);
            ctx.beginPath();
            ctx.moveTo(targetPoint.x - size, targetPoint.y - size);
            ctx.lineTo(targetPoint.x + size, targetPoint.y + size);
            ctx.moveTo(targetPoint.x + size, targetPoint.y - size);
            ctx.lineTo(targetPoint.x - size, targetPoint.y + size);
            ctx.strokeStyle = hovered ? '#fde68a' : '#f59e0b';
            ctx.lineWidth = Math.max(1.8, scale * 0.27) + (hovered ? 0.8 : 0);
            ctx.stroke();
            strategyMapHitPoints.push({
                kind: 'target',
                x: targetPoint.x,
                y: targetPoint.y,
                radius: Math.max(10, scale * 1.2),
                company,
            });
        }

        if (center) {
            const r = Math.max(5.4, scale * 0.8) + (hovered ? 1.8 : 0);
            ctx.beginPath();
            ctx.arc(center.x, center.y, r + 2.6, 0, Math.PI * 2);
            ctx.fillStyle = '#0b0f15';
            ctx.fill();
            ctx.beginPath();
            ctx.arc(center.x, center.y, r, 0, Math.PI * 2);
            ctx.fillStyle = hovered ? '#f8fafc' : color;
            ctx.fill();
            if (hovered) {
                ctx.beginPath();
                ctx.arc(center.x, center.y, r + 5.2, 0, Math.PI * 2);
                ctx.strokeStyle = `${color}cc`;
                ctx.lineWidth = 2;
                ctx.stroke();
            }
            ctx.fillStyle = '#e2e8f0';
            ctx.font = `${Math.max(10, Math.floor(scale * 0.86))}px Consolas`;
            ctx.textAlign = 'left';
            ctx.textBaseline = 'middle';
            ctx.fillText(String(company.id), center.x + r + 4, center.y);
            strategyMapHitPoints.push({
                kind: 'center',
                x: center.x,
                y: center.y,
                radius: Math.max(12, scale * 1.5),
                company,
            });
        }
    });

    strategyMapTransform = { offsetX, offsetY, scale, width, height };

    if (empty) empty.style.display = 'none';
    const visiblePct = Number(map.visible_ratio || 0) * 100;
    const exploredPct = Number(map.explored_ratio || 0) * 100;
    meta.textContent = `地图: ${width}x${height} | 可见 ${visiblePct.toFixed(1)}% | 已探索 ${exploredPct.toFixed(1)}%`;
    if (Number(map.updated_ms) > 0) {
        const t = new Date(Number(map.updated_ms));
        mapTime.textContent = `更新时间: ${t.toLocaleTimeString('zh-CN', { hour12: false })}`;
    } else {
        mapTime.textContent = '更新时间: --';
    }
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
