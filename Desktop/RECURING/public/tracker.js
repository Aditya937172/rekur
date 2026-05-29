(function () {
  "use strict";

  var script = document.currentScript || {};
  var globalConfig = window.RetentionTracker || {};
  var endpoint =
    script.dataset && script.dataset.endpoint
      ? script.dataset.endpoint
      : globalConfig.endpoint || "http://127.0.0.1:8010/events";
  var storeIdValue =
    script.dataset && script.dataset.storeId
      ? script.dataset.storeId
      : globalConfig.storeId || "1";
  var storeId = Number.parseInt(storeIdValue, 10);
  var customerId =
    (script.dataset && script.dataset.customerId) ||
    globalConfig.customerId ||
    detectShopifyCustomerId();

  var sessionKey = "retention_session_id";
  var firstSeenKey = "retention_first_seen_at";
  var lastVisitKey = "retention_last_visit_at";
  var sessionId = getOrCreateSessionId();
  var now = Date.now();
  var firstSeen = localStorage.getItem(firstSeenKey);
  var isFirstTime = !firstSeen;
  var previousVisitAt = Number.parseInt(localStorage.getItem(lastVisitKey) || "0", 10);
  var timeSinceLastVisit = previousVisitAt ? now - previousVisitAt : null;
  var visibleSince = document.visibilityState === "visible" ? now : null;
  var visibleMs = 0;
  var eventQueue = Promise.resolve();

  if (!firstSeen) {
    localStorage.setItem(firstSeenKey, String(now));
  }
  localStorage.setItem(lastVisitKey, String(now));

  sendEvent("session_start", { time_spent: 0 });
  sendEvent("page_view", { time_spent: 0 });

  if (window.location.pathname.indexOf("/products/") !== -1) {
    sendEvent("product_view", {
      product_id: detectProductId(),
      time_spent: 0,
    });
  }

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") {
      closeVisibleWindow();
      sendEvent("session_end", { time_spent: visibleMs });
      return;
    }
    visibleSince = Date.now();
  });

  window.addEventListener("pagehide", function () {
    closeVisibleWindow();
    sendEvent("session_end", { time_spent: visibleMs });
  });

  window.RetentionTracker = Object.assign({}, globalConfig, {
    track: sendEvent,
    sessionId: sessionId,
  });

  function getOrCreateSessionId() {
    var existing = localStorage.getItem(sessionKey);
    if (existing) {
      return existing;
    }
    var generated =
      window.crypto && window.crypto.randomUUID
        ? window.crypto.randomUUID()
        : "sess_" + Date.now() + "_" + Math.random().toString(16).slice(2);
    localStorage.setItem(sessionKey, generated);
    return generated;
  }

  function closeVisibleWindow() {
    if (visibleSince === null) {
      return;
    }
    visibleMs += Date.now() - visibleSince;
    visibleSince = null;
  }

  function sendEvent(eventType, extra) {
    var payload = Object.assign(
      {
        store_id: Number.isFinite(storeId) ? storeId : undefined,
        session_id: sessionId,
        event_type: eventType,
        page_url: window.location.href,
        referrer: document.referrer || null,
        device_type: detectDeviceType(),
        timestamp: new Date().toISOString(),
        customer_id: customerId || undefined,
        is_first_time: isFirstTime,
        time_since_last_visit: timeSinceLastVisit,
        metadata: {
          path: window.location.pathname,
          title: document.title,
        },
      },
      extra || {}
    );

    eventQueue = eventQueue
      .catch(function () {
        // A failed analytics event should not block later events.
      })
      .then(function () {
        return fetch(endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
          keepalive: true,
        });
      })
      .catch(function () {
        // Tracking should never break the storefront experience.
      });
    return eventQueue;
  }

  function detectDeviceType() {
    return window.matchMedia && window.matchMedia("(max-width: 767px)").matches
      ? "mobile"
      : "desktop";
  }

  function detectProductId() {
    if (
      window.ShopifyAnalytics &&
      window.ShopifyAnalytics.meta &&
      window.ShopifyAnalytics.meta.product &&
      window.ShopifyAnalytics.meta.product.id
    ) {
      return String(window.ShopifyAnalytics.meta.product.id);
    }
    var productNode = document.querySelector("[data-product-id]");
    return productNode ? productNode.getAttribute("data-product-id") : undefined;
  }

  function detectShopifyCustomerId() {
    if (
      window.ShopifyAnalytics &&
      window.ShopifyAnalytics.meta &&
      window.ShopifyAnalytics.meta.page &&
      window.ShopifyAnalytics.meta.page.customerId
    ) {
      return String(window.ShopifyAnalytics.meta.page.customerId);
    }
    return undefined;
  }
})();
