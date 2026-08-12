---
name: travelplanet-apk-builder
description: Build, validate, and clean a directly updatable TravelPlanet Android APK from the repository's Next.js and Capacitor frontend. Use when Codex is asked to package, rebuild, repair, verify, or deliver the TravelPlanet APK, especially when cloud API/data works but HTTP images fail, the fixed launcher icon must be preserved, the cloud IPv4 address must be embedded, or packaging intermediates must be cleaned safely.
---

# Build the TravelPlanet APK

Work from the repository root containing `前端/`. Inspect first, preserve user changes, and use the bundled scripts instead of reconstructing the packaging workflow.

## Invariants

Keep all of these unless the user explicitly changes them:

- Application ID: `com.travelplanet.app`.
- Cloud API: `http://114.132.201.196:8000/api`.
- Cloud assets: `http://114.132.201.196:8000`.
- Fixed icon source: `前端/design/travelplanet-app-icon.png`, SHA-256 `A013D537F49C1CCFE638E57B6069ECBEF1B728478EEE51963608A8A7C5F70A54`.
- Capacitor: `server.androidScheme="http"`, `server.cleartext=true`, `android.allowMixedContent=true`, and `plugins.CapacitorHttp.enabled=true`.
- Preserve the existing signing certificate and increase `versionCode` so Android can update the installed app directly.

`CapacitorHttp` handles fetch/XHR, but an HTTP `<img>` remains a WebView request. Using an HTTPS local scheme with the HTTP cloud asset URL blocks images as mixed content even when reports and plans load normally.

## Workflow

1. Run `git status --short` and inspect relevant diffs. Never discard unrelated or uncommitted user work.
2. Confirm `前端/next.config.mjs` contains both `output: "export"` and `images.unoptimized: true`. Add the missing static-export setting with `apply_patch`; do not package a stale existing `out/` tree.
3. Confirm `前端/capacitor.config.json` matches the invariants above. Repair the source config before building; do not merely patch an already-built APK.
4. Confirm the canonical icon exists and is unchanged. The build script rejects a different hash and copies the bundled, known-good Android launcher resources after Capacitor sync.
5. Run the build script from the repository root:

   ```powershell
   & "<skill-directory>\scripts\build-travelplanet-apk.ps1" -RepoRoot (Get-Location).Path
   ```

   It sets the cloud environment variables for the build, installs locked dependencies, increments the Android version, builds the static export, syncs Capacitor, restores the fixed launcher resources, builds with Gradle, validates the APK, copies it to the repository root, hash-checks the copy, and runs bounded Gradle cleanup.
6. If the user specifies a version or filename, pass `-VersionCode`, `-VersionName`, or `-OutputName`. Otherwise let the script increment the current version and use `TravelPlanet-YYYYMMDD.apk`.
7. Report the final absolute path, version, SHA-256, package ID, signature result, cloud URL result, and whether real-device testing occurred. Never claim phone validation without an authorized ADB device and an actual install/display check.
8. Before handoff, query the live backend and verify at least one current JSON endpoint plus one image URL from returned data. Require HTTP 200 and an `image/*` content type for the image; report a live-service outage separately from APK correctness.

## Validation and recovery

Run the verifier independently when diagnosing an existing APK:

```powershell
& "<skill-directory>\scripts\verify-travelplanet-apk.ps1" -ApkPath "<apk-path>" -PreviousApkPath "<known-working-apk>"
```

If Gradle reports a Chinese/non-ASCII path error, retain `android.overridePathCheck=true` in `前端/android/gradle.properties`. Use JDK 21 and Android SDK/Build Tools 36 when available. If sandboxed commands fail with `spawn EPERM` or a Gradle `.zip.lck` access error, rerun the same official command with the required approval; do not weaken checks.

Copy and hash-verify the final APK before cleanup. Use `gradlew.bat --no-daemon clean` for Android build intermediates. Do not recursively delete `前端/android`, `前端/out`, or `前端/.next` without explicit user confirmation; `android` is source-bearing, and `out`/`.next` may be useful for diagnosis.
