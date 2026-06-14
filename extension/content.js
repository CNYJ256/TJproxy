(function () {
  const APPID_REGEX = /application\/([^/]+)\/chat/;

  function extractAndSend() {
    const match = location.href.match(APPID_REGEX);
    const appId = match ? match[1] : null;
    chrome.runtime.sendMessage({ type: 'appId', data: { appId } });
  }

  extractAndSend();

  window.addEventListener('popstate', extractAndSend);
  window.addEventListener('hashchange', extractAndSend);
})();
