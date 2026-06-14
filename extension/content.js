(function () {
  const { extractAppId, observeNavigation } = globalThis.TJproxyContentUtils;

  function extractAndSend() {
    const appId = extractAppId(location.href);
    chrome.runtime.sendMessage({ type: 'appId', data: { appId } });
  }

  extractAndSend();
  observeNavigation(window, extractAndSend);
})();
