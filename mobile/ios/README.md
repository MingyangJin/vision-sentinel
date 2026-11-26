# Vision Sentinel — Sample iOS App

Minimal, open-source Swift / SwiftUI reference client for the self-hosted Vision Sentinel Notification Service (`vs-notify`).

> **Template Notice:** This project contains zero hardcoded servers, tokens, Apple Team IDs, or bundle identifiers. Replace `com.example.visionsentinel` with your own unique reverse-DNS bundle ID before building for a physical device.

## What It Includes

- **`VisionSentinelCore` (`Package.swift`):** Standalone Swift package containing the `vs-notify` HTTP client, Codable models, Keychain bearer-token store, APNs payload parser, and notification action router. Compiles and runs unit tests directly on macOS with `swift test`.
- **SwiftUI App (`App/` + `project.yml`):**
  - **Settings:** Server Base URL, Shared Household Bearer Token (stored in iOS Keychain), Connection Test (`GET /healthz` + authenticated `GET /v1/alerts?limit=1`), and APNs push device registration (`POST /v1/devices/register` with `platform: "ios"`).
  - **APNs Actionable Notifications (`AppDelegate`):** Requests push permission, registers the device token, and registers category `VISION_ALERT` with inline **Acknowledge**, **Thumbs Up**, and **Thumbs Down** actions.
  - **Alert Feed & Detail:** Lists alerts (`GET /v1/alerts`) with severity badge, Topic ID, and timestamp; displays authenticated thumbnail (`GET /v1/alerts/{id}/thumbnail`) and clip playback (`GET /v1/alerts/{id}/clip`); provides **Acknowledge** (`POST /v1/alerts/{id}/ack`) and a **Thumbs Up / Thumbs Down** feedback sheet with optional note (`POST /v1/alerts/{id}/feedback`).

## 1. Run Core Unit Tests on macOS (No Xcode Setup Needed)

```bash
cd mobile/ios
swift test
```

## 2. Generate & Open the Xcode Project

Install [XcodeGen](https://github.com/yonaskolb/XcodeGen) (`brew install xcodegen`) and generate `VisionSentinel.xcodeproj`:

```bash
cd mobile/ios
xcodegen generate
open VisionSentinel.xcodeproj
```

*(Alternatively, create a new iOS App target in Xcode 15+ targeting iOS 17+, add `mobile/ios/` as a local Swift Package dependency for `VisionSentinelCore`, and add the files under `App/`.)*

## 3. Signing & APNs Setup (Human Gate)

APNs push notifications require an Apple Developer account and a physical iOS device:

1. In Xcode, open the **VisionSentinel** target → **Signing & Capabilities**.
2. Select your **Team** and change **Bundle Identifier** from `com.example.visionsentinel` to a unique reverse-DNS ID you own (e.g., `com.yourname.visionsentinel`).
3. Verify the **Push Notifications** and **Background Modes → Remote notifications** capabilities are present.
4. Build and run on your iPhone.
5. In the app's **Settings** tab:
   - Enter your Notification Service URL (LAN, Tailscale, or HTTPS tunnel) and shared household bearer token (`uv run vs-notify token --show`).
   - Tap **Save & Test Connection**.
   - Tap **Request Push Permission & Fetch APNs Token**, then **Register Device with Server**.

For complete APNs `.p8` Auth Key generation, sandbox vs. production configuration, and self-hosted remote reachability options, see [`docs/NOTIFICATIONS_SETUP.md`](../../docs/NOTIFICATIONS_SETUP.md).

## License

[MIT](../../LICENSE), the same as the rest of this repository.
