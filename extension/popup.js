const $ = (id) => document.getElementById(id);
const request = (message) => chrome.runtime.sendMessage(message);
let ready = false;
let connected = false;
let polling = false;
let submitting = false;
let choosing = false;
let activeTask = null;
let currentTabUrl = '';
let noticeTimer;

function notice(text, tone = 'info', dismiss = false) {
  clearTimeout(noticeTimer);
  $('notice').textContent = text;
  $('notice').dataset.tone = tone;
  $('notice').hidden = !text;
  if (dismiss) noticeTimer = setTimeout(() => notice(''), 6500);
}
function updateUrl() {
  $('clear-url').hidden = !$('url').value;
  try {
    const url = new URL($('url').value);
    $('source-context').textContent = url.hostname.replace(/^www\./, '');
  } catch { $('source-context').textContent = 'YouTube and other video sites'; }
}
function updateControls() {
  $('download').disabled = !ready || submitting || choosing || Boolean(activeTask);
  $('choose-folder').disabled = !connected || choosing;
  if (!activeTask) $('download-label').textContent = submitting ? 'Starting…' : 'Download video';
}
async function refresh() {
  if (polling) return;
  polling = true;
  try {
    const result = await request({action: 'status'});
    connected = Boolean(result.ok);
    ready = connected && result.capabilities.downloader && result.capabilities.ffmpeg;
    $('check').dataset.state = ready ? 'online' : 'offline';
    $('check').setAttribute('aria-label', ready ? 'Connected. Check connection again' : 'Disconnected. Check connection again');
    $('connection').textContent = ready ? 'Connected' : 'Disconnected';
    $('check').title = ready ? 'Helper connected. Click to check again.' : result.error || 'Run install.command to connect the helper.';
    if (result.ok) {
      const path = result.capabilities.folder || '';
      $('destination').textContent = path.split('/').filter(Boolean).pop() || 'Downloads';
      $('destination').title = path;
      const picker = result.capabilities.picker || {};
      choosing = picker.state === 'choosing' && Date.now() / 1000 - picker.started < 310;
      $('picker-status').hidden = !choosing && picker.state !== 'error';
      $('picker-status').textContent = choosing ? 'Choose a folder in the system dialog. Your choice is saved even if this window closes.' : picker.error || '';
    }
    if (activeTask && connected) {
      const task = await request({action: 'task_status', id: activeTask});
      if (!task.ok || ['done', 'error', 'expired'].includes(task.state)) {
        activeTask = null;
        if (task.state === 'done') notice('Video saved to your selected folder.', 'success', true);
        else if (task.state === 'error' || !task.ok) notice(task.error || 'Could not save the video. Please try again.', 'error');
        else notice('');
      } else {
        $('download-label').textContent = task.state === 'processing' ? 'Preparing MP4…' : 'Saving…';
      }
    }
  } catch {
    connected = false; ready = false;
    $('check').dataset.state = 'offline'; $('connection').textContent = 'Disconnected';
    $('check').setAttribute('aria-label', 'Disconnected. Check connection again');
    $('check').title = 'Could not connect. Run install.command from the project folder.';
  } finally { polling = false; updateControls(); }
}
async function scan() {
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  if (!tab || !/^https?:\/\//.test(tab.url || '')) return;
  currentTabUrl = tab.url;
  $('url').value = tab.url;
  updateUrl();
}

$('form').addEventListener('submit', async event => {
  event.preventDefault();
  if (!ready || submitting || choosing || activeTask) return;
  submitting = true; notice(''); updateControls();
  try {
    const url = new URL($('url').value.trim());
    if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) throw new Error('Enter an HTTP or HTTPS link to a video page.');
    const quality = document.querySelector('input[name="quality"]:checked').value;
    await chrome.storage.local.set({quality});
    const id = crypto.randomUUID();
    const result = await request({action: 'download', id, url: url.href, quality});
    if (!result.ok) throw new Error(result.error);
    activeTask = id;
    $('download-label').textContent = 'Saving…';
    notice('You can close this window. The download will continue.');
  } catch (error) { notice(error.message, 'error'); }
  finally { submitting = false; updateControls(); }
});
$('folder').addEventListener('click', async () => {
  try { const result = await request({action: 'open_folder'}); if (!result.ok) notice(result.error, 'error'); }
  catch (error) { notice(error.message, 'error'); }
});
$('choose-folder').addEventListener('click', async () => {
  $('choose-folder').disabled = true;
  try {
    const result = await request({action: 'choose_folder'});
    if (!result.ok) notice(result.error, 'error');
    await refresh();
  } catch (error) { notice(error.message, 'error'); updateControls(); }
});
$('check').addEventListener('click', async () => { await refresh(); if (!ready) notice($('check').title, 'error'); });
$('url').addEventListener('input', updateUrl);
$('clear-url').addEventListener('click', () => { $('url').value = ''; updateUrl(); $('url').focus(); });
$('use-tab').addEventListener('click', async () => {
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  currentTabUrl = /^https?:\/\//.test(tab?.url || '') ? tab.url : currentTabUrl;
  if (currentTabUrl) { $('url').value = currentTabUrl; updateUrl(); }
  else { notice('Open a video page or paste a link.'); $('url').focus(); }
});
chrome.storage.local.get('quality').then(({quality}) => {
  if (['720', '1080', 'best'].includes(quality)) document.querySelector(`input[name="quality"][value="${quality}"]`).checked = true;
});
scan().catch(() => {}); refresh(); setInterval(refresh, 2200);

chrome.tabs.getCurrent?.().then(tab => { if (tab) document.documentElement.classList.add('full-page'); }).catch(() => {});
