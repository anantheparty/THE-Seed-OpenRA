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
            ? `https://${this.host}/vnc/vnc.html?autoconnect=true&resize=scale`
            : `http://${this.host}:6080/vnc.html?autoconnect=true&resize=scale`;
    },
    
    get apiWsUrl() {
        return this.isSecure 
            ? `wss://${this.host}/api/`
            : `ws://${this.host}:8080`;
    },
    
    get serviceApiUrl() {
        return this.isSecure 
            ? `https://${this.host}/api/service`
            : `http://${this.host}:8080/service`;
    }
};

// ========== State ==========
let ws = null;
let reconnectTimer = null;

// ========== Initialization ==========
document.addEventListener('DOMContentLoaded', () => {
    initVNC();
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
            log('success', 'Dashboard 已连接');
            updateStatus('ai-status-dot', 'connected');
        };
        
        ws.onclose = () => {
            log('error', 'Dashboard 连接断开');
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
            
        case 'log':
            if (data.payload) {
                log(data.payload.level || 'info', data.payload.message);
                // Also show in chat
                if (data.payload.level === 'info' || data.payload.level === 'success') {
                    addChatMessage('ai', data.payload.message);
                } else if (data.payload.level === 'error') {
                    addChatMessage('error', data.payload.message);
                }
            }
            break;
            
        case 'trace_event':
            if (data.payload?.event_type === 'fsm_transition') {
                log('info', `状态: ${data.payload.from_state} → ${data.payload.to_state}`);
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
        log('error', 'Dashboard 未连接');
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

// ========== Service Controls ==========
async function serviceAction(action) {
    log('info', `执行服务操作: ${action}`);
    addChatMessage('system', `正在执行: ${action}...`);
    
    try {
        const serviceUrl = CONFIG.isSecure 
            ? `https://${CONFIG.host}/service/api/${action}`
            : `http://${CONFIG.host}:8085/api/${action}`;
        
        const response = await fetch(serviceUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
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
            : `http://${CONFIG.host}:8085/api/status`;
        
        const response = await fetch(statusUrl);
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

// ========== Logging ==========
function log(level, message) {
    const output = document.getElementById('log-output');
    const entry = document.createElement('div');
    entry.className = `log-entry ${level}`;
    
    const time = new Date().toLocaleTimeString('zh-CN', { hour12: false });
    entry.innerHTML = `<span class="log-time">${time}</span>${escapeHtml(message)}`;
    
    output.appendChild(entry);
    output.scrollTop = output.scrollHeight;
    
    // Limit log entries
    while (output.children.length > 500) {
        output.removeChild(output.firstChild);
    }
}

function clearLogs() {
    document.getElementById('log-output').innerHTML = '';
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
