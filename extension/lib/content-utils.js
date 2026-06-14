(function (root) {
  const APPID_REGEX = /application\/([^/]+)\/chat/;

  function extractAppId(url) {
    const match = url.match(APPID_REGEX);
    return match ? match[1] : null;
  }

  function observeNavigation(target, notify) {
    let previousUrl = target.location.href;
    const checkUrl = () => {
      if (target.location.href !== previousUrl) {
        previousUrl = target.location.href;
        notify();
      }
    };

    target.addEventListener('popstate', checkUrl);
    target.addEventListener('hashchange', checkUrl);
    const intervalId = target.setInterval(checkUrl, 500);

    return () => {
      target.clearInterval(intervalId);
      target.removeEventListener('popstate', checkUrl);
      target.removeEventListener('hashchange', checkUrl);
    };
  }

  root.TJproxyContentUtils = Object.freeze({ extractAppId, observeNavigation });
})(globalThis);
