# Privacy

OfflineVideo has no account system, analytics, advertising, remote application backend, or download-history library.

## Local preferences

- `chrome.storage.local` saves your preferred quality in the browser profile.
- `~/Library/Application Support/OfflineVideo/settings.json` saves your selected destination folder.
- `picker.json` in the same directory contains the folder picker's temporary status or error.

## Current tab and requests

The `activeTab` permission lets the popup read the current tab's URL when you invoke the extension. There are no content scripts, broad host permissions, background page scans, browser-history access, or cookie extraction.

The current link stays in the popup while it is open. Starting a download passes that URL, quality, and selected destination to the installed native helper. Requests go directly from yt-dlp to the video service and its media hosts; those services receive the source IP address and ordinary request metadata. OfflineVideo does not relay requests through its own server.

## Temporary task state

A running helper records active task state in `~/Library/Application Support/OfflineVideo/jobs/`. It can include the requested URL, title, quality, destination, progress, and error. These files coordinate the detached worker; they are not exposed as a history list.

The completion receipt is removed when the open popup reads it, or within a minute of worker completion. Interrupted workers' stale state is cleaned up on a later status check. Download logs are not kept. Treat active request state as private because URLs can contain access parameters.

Media is downloaded and converted in a temporary directory. Only the finished MP4 is copied to your chosen folder. Saved videos remain until you move or delete them; the extension does not track those actions.

## Native integration

- `nativeMessaging` connects only to the registered `local.offlinevideo` helper.
- Native host manifests allow only OfflineVideo's fixed extension ID.
- The helper can write to the folder you select and open that folder in Finder.
- Installation downloads Python dependencies and places a runtime copy in Application Support. YouTube may also require an installed Node.js or Deno runtime.

Removing the browser extension clears its browser preferences, but does not remove the separately installed helper, folder preference, or videos. See the README for removal instructions.

## Support

GitHub issues are public. Redact personal URLs, folder paths, account information, and screenshots before posting them.
