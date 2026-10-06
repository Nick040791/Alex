// Ring IP Cam Service Worker for Push & Device Notifications
self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

// Handle notification requests from the web dashboard
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SHOW_NOTIFICATION') {
    const { title, options } = event.data;
    event.waitUntil(
      self.registration.showNotification(title, {
        icon: '/static/icon-192.png',
        badge: '/static/icon-192.png',
        vibrate: [250, 100, 250],
        tag: 'ring-motion-alert',
        renotify: true,
        requireInteraction: false,
        actions: [
          { action: 'open_live', title: '👁️ View Camera' },
          { action: 'close', title: 'Dismiss' }
        ],
        ...options
      })
    );
  }
});

// Notification click event handler
self.addEventListener('notificationclick', (event) => {
  event.notification.close();

  if (event.action === 'close') {
    return;
  }

  // Open or focus dashboard window
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if ('focus' in client) {
          return client.focus();
        }
      }
      if (clients.openWindow) {
        return clients.openWindow('/');
      }
    })
  );
});
