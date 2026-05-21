// Kill-switch service worker: unregisters itself and reloads all tabs.
// This replaces the old Flutter SW so browsers that still have it
// installed will automatically pick up this version, self-destruct,
// and start loading fresh assets from the network.
self.addEventListener('install', function() { self.skipWaiting(); });
self.addEventListener('activate', function(event) {
  event.waitUntil(
    caches.keys().then(function(names) {
      return Promise.all(names.map(function(n) { return caches.delete(n); }));
    }).then(function() {
      return self.registration.unregister();
    }).then(function() {
      return self.clients.matchAll({ type: 'window' });
    }).then(function(clients) {
      clients.forEach(function(c) { c.navigate(c.url); });
    })
  );
});
self.addEventListener('fetch', function() { /* no-op: let network handle everything */ });
