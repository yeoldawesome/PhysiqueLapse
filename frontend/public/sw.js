// PhysiqueLapse service worker: only here so the app can be installed to the home screen.
// It caches nothing: every request goes to the server as usual (photos are private and
// always fetched fresh behind the login).
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {
  // no respondWith: the browser handles the request normally
});
