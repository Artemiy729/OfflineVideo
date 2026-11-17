const HOST = 'local.offlinevideo';
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (sender.id !== chrome.runtime.id || sender.url !== chrome.runtime.getURL('popup.html') || !['status', 'download', 'open_folder', 'choose_folder', 'task_status'].includes(message.action)) return;
  chrome.runtime.sendNativeMessage(HOST, message).then(respond).catch(() => {
    respond({ok: false, error: 'Helper disconnected. Run install.command from the OfflineVideo folder, then restart your browser.'});
  });
  return true;
});
