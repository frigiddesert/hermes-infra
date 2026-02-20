/**
 * openclaw VPS Watchdog — Cloudflare Worker
 *
 * Endpoints:
 *   GET  /health       Public — VPS alive status, last heartbeat
 *   POST /heartbeat    VPS → CF — bearer WATCHDOG_SECRET, stores status, returns pending commands
 *   POST /command      Admin → CF — bearer ADMIN_TOKEN, queues a command to send to VPS
 *   GET  /status       Admin → CF — bearer ADMIN_TOKEN, full detail view
 *   POST /telegram     Telegram webhook (optional) — /restart /status commands from Telegram
 */

const STALE_THRESHOLD_MS = 5 * 60 * 1000; // 5 minutes = VPS considered down

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;
    const method = request.method;

    if (method === 'GET' && path === '/health') return handleHealth(env);
    if (method === 'POST' && path === '/heartbeat') return handleHeartbeat(request, env);
    if (method === 'POST' && path === '/command') return handleCommand(request, env);
    if (method === 'GET' && path === '/status') return handleStatus(request, env);
    if (method === 'GET' && path === '/') return handleDashboard(env);
    if (method === 'POST' && path === '/telegram') return handleTelegramWebhook(request, env);

    return json({ error: 'not found' }, 404);
  },
};

// ── Auth helpers ──────────────────────────────────────────────────────────────

function bearerToken(request) {
  const auth = request.headers.get('Authorization') || '';
  return auth.startsWith('Bearer ') ? auth.slice(7).trim() : null;
}

function requireToken(request, expected) {
  const tok = bearerToken(request);
  if (!tok || tok !== expected) return json({ error: 'unauthorized' }, 401);
  return null;
}

// ── Handlers ──────────────────────────────────────────────────────────────────

async function handleDashboard(env) {
  const raw = await env.KV.get('heartbeat:latest');
  const hb = raw ? JSON.parse(raw) : null;
  const alive = hb ? (Date.now() - hb.received_at) < STALE_THRESHOLD_MS : false;
  const age = hb ? Math.floor((Date.now() - hb.received_at) / 1000) : null;

  const svcRows = hb?.services
    ? Object.entries(hb.services).map(([k, v]) =>
        `<tr><td>${v === 'active' ? '✅' : '❌'}</td><td><code>${k}</code></td><td>${v}</td></tr>`
      ).join('')
    : '<tr><td colspan="3">No data</td></tr>';

  const html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>openclaw VPS</title>
<style>
  body { font-family: -apple-system, sans-serif; max-width: 480px; margin: 2rem auto; padding: 1rem; background: #0f0f0f; color: #e0e0e0; }
  h1 { font-size: 1.4rem; margin-bottom: 0.5rem; }
  .status { font-size: 2rem; margin: 1rem 0; }
  table { width: 100%; border-collapse: collapse; margin: 1rem 0; }
  td { padding: 0.4rem 0.5rem; border-bottom: 1px solid #333; }
  code { background: #222; padding: 2px 6px; border-radius: 4px; font-size: 0.85rem; }
  .meta { color: #888; font-size: 0.85rem; margin: 0.5rem 0; }
  .actions { margin-top: 1.5rem; display: flex; flex-direction: column; gap: 0.5rem; }
  button { padding: 0.75rem 1rem; border: none; border-radius: 8px; font-size: 1rem; cursor: pointer; }
  .btn-restart { background: #e65c00; color: white; }
  .btn-status  { background: #1a6b3a; color: white; }
  .btn-danger  { background: #555; color: white; }
  .token-form { margin-top: 1rem; }
  input[type=text] { width: 100%; padding: 0.5rem; background: #222; border: 1px solid #444; color: #e0e0e0; border-radius: 6px; font-size: 0.9rem; box-sizing: border-box; }
  .note { color: #888; font-size: 0.8rem; margin-top: 1rem; }
</style>
</head>
<body>
<h1>openclaw VPS</h1>
<div class="status">${alive ? '🟢' : '🔴'} ${alive ? 'Online' : 'Offline'}</div>
${hb ? `
<div class="meta">Last seen: ${age}s ago | Uptime: ${hb.uptime}</div>
<div class="meta">💾 ${hb.disk_free_gb}GB disk free &nbsp;|&nbsp; 🧠 ${hb.mem_free_mb}MB RAM free</div>
` : '<div class="meta">No heartbeat data yet</div>'}
<table>${svcRows}</table>

<div class="token-form">
  <p style="font-size:0.85rem;color:#888">Admin token required for actions:</p>
  <input type="text" id="tok" placeholder="Admin token" />
</div>
<div class="actions">
  <button class="btn-status" onclick="sendCmd('status')">📊 Send status to Telegram</button>
  <button class="btn-restart" onclick="sendCmd('restart:gateway')">⚡ Restart gateway</button>
  <button class="btn-restart" onclick="sendCmd('restart:all')">🔄 Restart all services</button>
  <button class="btn-danger" onclick="sendCmd('restart:ollama')">🔁 Restart Ollama</button>
</div>
<p class="note">Commands execute on next heartbeat (≤2 min).</p>

<script>
async function sendCmd(cmd) {
  const tok = document.getElementById('tok').value.trim();
  if (!tok) { alert('Enter admin token first'); return; }
  const r = await fetch('/command', {
    method: 'POST',
    headers: {'Authorization': 'Bearer ' + tok, 'Content-Type': 'application/json'},
    body: JSON.stringify({command: cmd})
  });
  const d = await r.json();
  alert(d.message || d.error || JSON.stringify(d));
}
</script>
</body>
</html>`;
  return new Response(html, { headers: { 'Content-Type': 'text/html' } });
}

async function handleHealth(env) {
  const raw = await env.KV.get('heartbeat:latest');
  if (!raw) {
    return json({ alive: false, message: 'No heartbeat received yet' });
  }
  const hb = JSON.parse(raw);
  const age_ms = Date.now() - hb.received_at;
  const alive = age_ms < STALE_THRESHOLD_MS;
  return json({
    alive,
    last_seen_seconds_ago: Math.floor(age_ms / 1000),
    hostname: hb.hostname,
    services: hb.services,
    uptime: hb.uptime,
    disk_free_gb: hb.disk_free_gb,
    mem_free_mb: hb.mem_free_mb,
    timestamp: new Date(hb.received_at).toISOString(),
  });
}

async function handleHeartbeat(request, env) {
  const authErr = requireToken(request, env.WATCHDOG_SECRET);
  if (authErr) return authErr;

  let body;
  try { body = await request.json(); } catch { return json({ error: 'invalid json' }, 400); }

  const payload = {
    ...body,
    received_at: Date.now(),
  };
  await env.KV.put('heartbeat:latest', JSON.stringify(payload), { expirationTtl: 600 }); // expire after 10m

  // Return and clear pending commands
  const queued = await env.KV.get('commands:queue');
  const commands = queued ? JSON.parse(queued) : [];
  if (commands.length > 0) {
    await env.KV.delete('commands:queue');
  }

  // If any services are down, notify Telegram (fire and forget)
  const downServices = body.services
    ? Object.entries(body.services).filter(([, s]) => s !== 'running').map(([k]) => k)
    : [];
  if (downServices.length > 0 && env.TELEGRAM_BOT_TOKEN && env.TELEGRAM_USER_ID) {
    ctx_notify(env, `⚠️ *VPS Alert* — services down on \`${body.hostname}\`:\n${downServices.map(s => `• \`${s}\``).join('\n')}`);
  }

  return json({ ok: true, commands });
}

async function handleCommand(request, env) {
  const authErr = requireToken(request, env.ADMIN_TOKEN);
  if (authErr) return authErr;

  let body;
  try { body = await request.json(); } catch { return json({ error: 'invalid json' }, 400); }

  const { command } = body;
  const valid = ['restart:all', 'restart:gateway', 'restart:ollama', 'restart:postgres',
                  'restart:telegram-router', 'status', 'reboot'];
  if (!valid.includes(command)) {
    return json({ error: `unknown command. valid: ${valid.join(', ')}` }, 400);
  }

  const queued = await env.KV.get('commands:queue');
  const commands = queued ? JSON.parse(queued) : [];
  if (!commands.includes(command)) {
    commands.push(command);
  }
  await env.KV.put('commands:queue', JSON.stringify(commands), { expirationTtl: 300 }); // 5m TTL

  return json({ ok: true, queued: commands, message: `Command '${command}' will run on next VPS heartbeat (≤2 min)` });
}

async function handleStatus(request, env) {
  const authErr = requireToken(request, env.ADMIN_TOKEN);
  if (authErr) return authErr;

  const [hbRaw, cmdRaw] = await Promise.all([
    env.KV.get('heartbeat:latest'),
    env.KV.get('commands:queue'),
  ]);

  const heartbeat = hbRaw ? JSON.parse(hbRaw) : null;
  const commands = cmdRaw ? JSON.parse(cmdRaw) : [];
  const alive = heartbeat ? (Date.now() - heartbeat.received_at) < STALE_THRESHOLD_MS : false;

  return json({ alive, heartbeat, pending_commands: commands });
}

async function handleTelegramWebhook(request, env) {
  // Optional: allow Telegram commands /restart /status /cmd:restart:all
  // Verify it's from our bot by checking secret token header
  const secret = request.headers.get('X-Telegram-Bot-Api-Secret-Token');
  if (!secret || secret !== env.TELEGRAM_WEBHOOK_SECRET) {
    return json({ error: 'unauthorized' }, 401);
  }

  let update;
  try { update = await request.json(); } catch { return json({ ok: true }); }

  const msg = update.message || update.channel_post;
  if (!msg || !msg.text) return json({ ok: true });

  const chatId = String(msg.chat.id);
  const userId = String(msg.from?.id);
  const text = msg.text.trim();

  // Only respond to authorized user
  if (userId !== env.TELEGRAM_USER_ID) return json({ ok: true });

  let reply;
  if (text.startsWith('/status') || text === '/health') {
    const hbRaw = await env.KV.get('heartbeat:latest');
    if (!hbRaw) {
      reply = '❓ No heartbeat data yet.';
    } else {
      const hb = JSON.parse(hbRaw);
      const age = Math.floor((Date.now() - hb.received_at) / 1000);
      const alive = age < 300;
      const svcLines = hb.services
        ? Object.entries(hb.services).map(([k, v]) => `  ${v === 'running' ? '✅' : '❌'} \`${k}\``)
        : [];
      reply = `${alive ? '✅' : '🔴'} *VPS Status* (${age}s ago)\n${svcLines.join('\n')}\n💾 Disk: ${hb.disk_free_gb}GB free\n🧠 RAM: ${hb.mem_free_mb}MB free\n⏱ Uptime: ${hb.uptime}`;
    }
  } else if (text.startsWith('/restart')) {
    const parts = text.split(' ');
    const svc = parts[1] || 'all';
    const cmd = `restart:${svc}`;
    const valid = ['restart:all', 'restart:gateway', 'restart:ollama', 'restart:postgres', 'restart:telegram-router'];
    if (!valid.includes(cmd)) {
      reply = `❓ Unknown service. Try:\n/restart all\n/restart gateway\n/restart ollama\n/restart postgres\n/restart telegram-router`;
    } else {
      const queued = await env.KV.get('commands:queue');
      const commands = queued ? JSON.parse(queued) : [];
      if (!commands.includes(cmd)) commands.push(cmd);
      await env.KV.put('commands:queue', JSON.stringify(commands), { expirationTtl: 300 });
      reply = `⚡ Queued: \`${cmd}\`\nWill execute on next heartbeat (≤2 min).`;
    }
  } else if (text === '/help') {
    reply = `*VPS Watchdog Commands*\n/status — VPS health\n/restart all — restart all services\n/restart gateway — restart openclaw\n/restart ollama — restart Ollama\n/restart postgres — restart PostgreSQL\n/restart telegram-router — restart router services`;
  } else {
    return json({ ok: true });
  }

  await telegramSend(env.TELEGRAM_BOT_TOKEN, chatId, reply);
  return json({ ok: true });
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function json(data, status = 200) {
  return new Response(JSON.stringify(data, null, 2), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

async function telegramSend(token, chatId, text) {
  await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chat_id: chatId, text, parse_mode: 'Markdown' }),
  });
}

// Fire-and-forget Telegram notification (used inside heartbeat handler)
function ctx_notify(env, message) {
  if (env.TELEGRAM_BOT_TOKEN && env.TELEGRAM_USER_ID) {
    telegramSend(env.TELEGRAM_BOT_TOKEN, env.TELEGRAM_USER_ID, message).catch(() => {});
  }
}
