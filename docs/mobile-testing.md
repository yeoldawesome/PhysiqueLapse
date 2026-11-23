# Phone checklist

Things only a real phone can confirm. Open `https://betterlifts.<tailnet>.ts.net:8443` with Tailscale on. Tick each item on **iOS Safari (tab)**, **iOS installed to the home screen**, and **Android Chrome**.

## Login and install
- [ ] Login page appears; a wrong password says so; the right one opens the Dashboard.
- [ ] iOS: Share → *Add to Home Screen*. Android: ⋮ → *Install app*. Opens full screen with the PhysiqueLapse icon, no browser bar.
- [ ] Notch / home bar don't cover the header or the bottom tabs.

## Upload
- [ ] Upload tab → *Choose files* opens the photo library; pick 20+ photos, including HEIC on iPhone.
- [ ] Progress bar moves. Turn on airplane mode halfway, then off: the upload continues (it may pause up to ~30 s).
- [ ] The imported photos have the right **date taken** (not today). If they show today's date, your phone stripped EXIF while uploading. See quirks below.
- [ ] The job panel shows Import → Extract landmarks → Classify → Align.

## Everyday pages
- [ ] Library: 3 columns, Filters button, tap opens a photo full screen, long-press selects, then taps add to the selection.
- [ ] Viewer: swipe left/right steps through photos; the slider scrubs; the sliders button shows modes.

## Capture
- [ ] Capture tab → a pose → camera permission prompt → live picture with the reference ghost.
- [ ] The ghost and live picture line up (same framing) with the front and back camera; mirror toggle flips both.
- [ ] Opacity, Photo / Outline / Skeleton, Reference / Last / First all change the ghost; settings are remembered next time for that pose.
- [ ] Timer 3/5/10 counts down and shoots.
- [ ] Review: *Hold: before* flicks to the ghost; *Retake* discards; *Keep* opens the share sheet → *Save Image* (iOS) / save to Photos (Android).
- [ ] The saved photo appears in the camera roll **at the time it was taken**, not covered by the ghost.
- [ ] Cancelling the share sheet still uploads the photo.
- [ ] Airplane mode → take and keep a photo → close the app → airplane mode off → open the app: the photo uploads ("waiting to upload" goes away).
- [ ] Capture page → *Save today's photos to camera roll* → *Save N photos* gives one share sheet with all of them.

## Known platform quirks
- **iOS Safari has no ImageCapture**, so a capture is the full-resolution video frame. That's typically 1920×1440 or 1920×1080 depending on the device, not the 12 MP the Camera app takes. Android Chrome uses ImageCapture and gives the full sensor resolution.
- **iOS camera permission in the home-screen app**: iOS may ask again each time the app is reopened. That's an iOS limitation for web apps.
- **The share sheet needs a direct tap.** That's why *Save today's photos* takes two taps: one to fetch the files and one to open the sheet.
- **iOS photo picker and dates**: when Safari converts HEIC to JPEG for upload, it normally keeps the EXIF date. If dates come out as the upload time, upload from the Files app instead, or use the *Most Compatible* camera format.
- **Desktop browsers** without a share sheet for files download the photo instead (`PhysiqueLapse_<pose>_<date>.jpg`).
- **Screen lock**: the Capture page keeps the screen awake while the camera is open, where the browser supports it (Wake Lock: Android Chrome, iOS 16.4+).

Report anything that fails with the phone model, browser and iOS/Android version.
