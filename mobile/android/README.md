# Vision Sentinel — Sample Android App

Minimal, open-source Kotlin / Jetpack Compose (Material 3) reference client for the self-hosted Vision Sentinel Notification Service (`vs-notify`).

> **Template Notice:** This project contains zero hardcoded servers, tokens, Firebase credentials, or personal package IDs. It compiles and runs unit tests cleanly **even before** you add a real `app/google-services.json` (the `com.google.gms.google-services` Gradle plugin is applied conditionally only when `app/google-services.json` exists).

## What It Includes

- **Setup & Settings (`SettingsScreen`):**
  - Server Base URL, Shared Household Bearer Token (stored locally in Android Keystore via `EncryptedSharedPreferences`), and Device Name.
  - **Save & Test Connection** verifying both reachability (`GET /healthz`) and bearer-token authentication (`GET /v1/alerts?limit=1`).
  - **FCM Device Registration** (`POST /v1/devices/register` with `platform: "android"`).
  - Direct shortcut to Android's **`sentinel_urgent` Notification Channel & Do Not Disturb (DND) Bypass** settings.
- **FCM Push + Wear OS Inline Actions (`SentinelMessagingService` + `AlertActionReceiver`):**
  - Creates two channels on startup: `sentinel_default` (standard alerts) and `sentinel_urgent` (high-priority alarm audio, vibration, and DND override).
  - Displays alerts with inline **Acknowledge**, **Thumbs Up**, and **Thumbs Down** actions using `NotificationCompat.WearableExtender` so one-tap responses work on both the phone notification shade and paired Wear OS watches.
- **Alert Feed & Detail (`AlertFeedScreen` + `AlertDetailScreen`, Material 3 dark-mode friendly):**
  - Lists alerts (`GET /v1/alerts`) with filter chips (`All`, `Unacked`, `Urgent`), severity badges, Topic IDs, and ack/feedback status.
  - Shows authenticated thumbnails (`GET /v1/alerts/{id}/thumbnail`) and clip playback (`GET /v1/alerts/{id}/clip`).
  - Provides **Acknowledge** (`POST /v1/alerts/{id}/ack`) and a **Thumbs Up / Thumbs Down** feedback dialog with optional note (`POST /v1/alerts/{id}/feedback`).

## 1. Build & Run Unit Tests Locally (No Firebase Required)

Open `mobile/android/` in Android Studio, or run from the command line with JDK 17 and the Android SDK (`ANDROID_HOME` set):

```bash
cd mobile/android
./gradlew testDebugUnitTest assembleDebug
```

Without `app/google-services.json`, the app compiles cleanly and can browse/acknowledge/rate alerts over LAN or VPN right away.

## 2. Firebase / FCM Setup & Sideloading (Human Gate)

To enable direct outbound push notifications via Firebase Cloud Messaging (HTTP v1):

1. Create a free Firebase project in the Firebase Console (no paid plan required for FCM).
2. Add an Android app with your chosen package name (default template: `com.example.visionsentinel`; change `applicationId` in `app/build.gradle.kts` if you prefer your own).
3. Download `google-services.json` and place it at `mobile/android/app/google-services.json` (gitignored; see `app/google-services.json.example` for the expected shape).
4. Build and sideload the debug APK onto your phone:
   ```bash
   cd mobile/android
   ./gradlew installDebug
   ```
5. Open **Settings** in the app:
   - Enter your Notification Service URL and shared household bearer token (`uv run vs-notify token --show`).
   - Tap **Save & Test Connection**.
   - Grant notification permission and tap **Register FCM Device with Server**.
   - Optionally tap **Urgent Channel & DND Bypass Settings** and enable **Override Do Not Disturb** for `Urgent Vision Alerts`.

For generating the FCM service-account JSON for `vs-notify` and configuring remote reachability (Tailscale / Cloudflare Tunnel / reverse proxy), see [`docs/NOTIFICATIONS_SETUP.md`](../../docs/NOTIFICATIONS_SETUP.md).

## License

[MIT](../../LICENSE), the same as the rest of this repository. The Gradle wrapper scripts (`gradlew`, `gradlew.bat`, `gradle/wrapper/`) are the stock Apache-2.0 files from Gradle.
