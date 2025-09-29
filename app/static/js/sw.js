// /app/static/sw.js
self.addEventListener('install', event => {
  self.skipWaiting(); // Take control immediately
});

self.addEventListener('activate', event => {
  event.waitUntil(self.clients.claim()); // Take control of all clients
});

self.addEventListener('fetch', event => {
  console.log('Service Worker fetching:', event.request.url);
  // Pass through media requests to /serve without caching
  if (event.request.url.includes('/serve?file=')) {
    event.respondWith(fetch(event.request));
  } else if (event.request.url.endsWith('.js') || event.request.url.endsWith('.css')) {
    // Cache JS and CSS files
    event.respondWith(
      caches.open('jclipper-cache').then(cache => {
        return cache.match(event.request).then(response => {
          return response || fetch(event.request).then(fetchResponse => {
            cache.put(event.request, fetchResponse.clone());
            return fetchResponse;
          });
        });
      })
    );
  }
});