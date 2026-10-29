# Recording Stop audit and fix

Date: 2026-10-09. Scope: stopping from the app recording screen, including a scheduled session, native finalization, meeting/summary navigation, and browser recovery. Existing recordings and schedule settings are preserved.

## Findings

- **P1 — Confirmed from code:** completion navigation used only `!result.scheduled`, so an explicit Stop on a scheduled recording never opened meeting details. Native completion and the Stop response shared one deduplication gate, which could also consume an explicit navigation request.
- **P1 — Confirmed from code:** browser recovery listed unsaved IndexedDB metadata without checking the durable native session mapping. Missing completion events could offer an already saved recording for recovery. A stale metadata write could overwrite `savedToSQLite: true`.
- **P2 — Confirmed from code:** Stop failures were logged and local processing cleared, without an actionable error or immediate native state refresh. Same-frame repeated clicks relied on React state updates for exclusion.
- **P2 — Confirmed from code:** device-monitor shutdown awaited enumeration without a limit, model teardown did not bound lock acquisition/unload, and meeting-end analytics ran before final persistence. **Strongly inferred:** these could delay completion; their involvement in the reported recording was not established from runtime logs.
- **P1 — Runtime verified during the macOS smoke test:** final persistence failed with SQLite code 517 (`SQLITE_BUSY_SNAPSHOT`). The session lookup ran in a deferred transaction that could not upgrade its read snapshot after another connection wrote. Finalization now reserves the writer with `BEGIN IMMEDIATE` before reading the session mapping, retaining atomic, idempotent meeting creation. A file-backed WAL regression verifies saving waits for a background writer and retry creates no duplicate.
- The registered Stop command accepts an omitted legacy `save_path`; the unused older audio command's required field was not the active IPC contract and was excluded as a cause.

## Changes

Explicit app Stop opens `/meeting-details?id=<saved meeting>&source=recording` after native persistence, including scheduled recordings. Presentation work and navigation have separate session-based deduplication. Automatic scheduled completion keeps the current page. Existing auto-summary settings govern generation. A newer active session prevents stale completion from clearing controls or navigating away.

Stop shows immediate progress, blocks same-frame repeat clicks, displays the native error on rejection, and refreshes state to restore valid controls. Browser cleanup, history refresh, and analytics cannot hold up navigation.

Native device-monitor shutdown has a two-second limit; model config lookup, lock acquisition, and teardown share a five-second limit. Timed-out model work is cancelled before session ownership is released. Analytics starts after native persistence and completion events, with its own five-second limit. Existing transcription/audio-save limits and checkpoint retention are unchanged.

The read-only `get_recording_session_status(sessionId)` command returns `{ status, meetingId }` or `null` for an unknown session. Live state takes precedence over asynchronously persisted phases. Recovery excludes completed mapped sessions, deleted sessions, and active sessions. Interrupted/error sessions and unknown or unavailable lookups remain recoverable. Legacy timestamp-based browser checkpoints keep their existing behavior. IndexedDB metadata merges and saved-flag updates run within write transactions and wait for commit; a completion-before-initialization guard covers very short recordings.

## Verification

Automated checks use injected events and state, in-memory SQLite, an IndexedDB emulator, and pending futures. They do not record live audio, request permissions, or change enabled schedules.

- `pnpm exec tsc --noEmit`: passed.
- `pnpm build`: passed; after deferring the summary editor, meeting-details first-load JavaScript is approximately 261 kB (previously 801 kB).
- `bun test tests`: 62 suite tests passed, including isolated fixture groups. Added coverage includes both completion/Stop orderings, concurrent responses, paused recording, duplicate clicks, empty transcripts, newer-session ownership, failed Stop with retry, completed/active/interrupted recovery, late/concurrent browser writes, and deferred-editor loading with Save/Copy refs.
- `cargo test -p meetily scheduling --lib --offline`: 29 passed, including a file-backed SQLite contention regression.
- `cargo test -p meetily shutdown --lib --offline`: 3 passed, covering stalled monitor enumeration, cancelled model teardown, and nonblocking analytics.
- `git diff --check`: passed.

Functional matrix: 1.1–1.5 (recording completion, navigation, Stop control, interruptions, duplicates), 1.8 (checkpoint persistence), 1.10–1.12 (native contracts, notification deduplication, completion events) covered within this flow. Section 1.9 verifies the existing summary-route trigger and existing summary regressions, not live AI output quality. Sections 1.6, 1.7, and 1.13 were outside this narrowly requested flow.

The local debug macOS bundle builds successfully using the Xcode 26.5 SDK (`SDKROOT`), avoiding a mismatched Command Line Tools SDK. Opening the updated bundle reconciled the completed session that the older installed app had incorrectly offered for recovery. A real Zoom/microphone recording showed immediate Stop progress and transcription processing; it exposed the database contention above and retained the failed session for recovery. After the transaction fix, that failed session was recovered through the UI with its stable meeting ID. A fresh real Zoom/microphone recording then showed immediate Stop progress, saved successfully as `completed`, opened the meeting-details route with `source=recording`, and displayed the AI summary side panel; automatic generation completed under the existing preference. There was one success toast and one meeting mapping. The app was quit and reopened after saving: neither smoke session appeared in recovery, and no active recording remained. CoreAudio's `AudioDeviceStart` delayed startup during the test but eventually returned; this was separate from Stop/final persistence.

The live smoke used a manual recording. Scheduled explicit Stop and automatic quiet completion are covered by the event-ordering fixtures and native scheduling tests. Debug builds deliberately disable unattended schedule dispatch, so enabled schedules were not changed. The debug test app was closed after verification and the original installed app reopened. At that stage, the installed `/Applications/meetily.app` still needed a release rebuild/update; the installation recorded below has now completed it.

Readiness: **ready-with-risks**. The manual native Stop/save/navigation smoke passed; scheduled runtime dispatch remains covered by automation rather than a release-bundle live run.

## Terminal-only follow-up: scope and assumptions

The user requested fixing remaining findings without computer use. This follow-up edits code and runs automated tests/builds only. It does not launch applications, capture audio, change permissions or schedules, read user recordings, or replace the installed app. The earlier live verification above predates this follow-up.

The updated debug macOS bundle was rebuilt successfully at `target/debug/bundle/macos/meetily.app`, including the startup timeout and smaller frontend bundle. It was not opened during this follow-up.

## Verified findings and fixes

- **P1 — Confirmed from code; timeout mechanics verified by regression:** synchronous CoreAudio startup could block the recording start and reconnection tasks in `AudioDeviceStart` (observed in the earlier native stack sample). `audio/capture/core_audio.rs:57` now uses a ten-second bounded startup for application, system, and legacy system-capture entry points. `audio/capture/startup.rs:19` runs the native work on a dedicated thread, signals cancellation on timeout or caller cancellation, and discards any late stream. Its single outstanding-start permit prevents retries accumulating blocked OS threads. CoreAudio callbacks check the same cancellation signal, so a late native return cannot deliver samples into another recording. Repro regression: hold the fake native worker, wait for the timeout, retry, release the worker, verify resource disposal and a successful later retry.
- **P2 — Confirmed by production build; fixed:** the summary editor was eagerly included in the meeting-details route. `components/MeetingDetails/SummaryPanel.tsx:27` now defers it with `React.lazy` and a visible loading fallback. A component regression verifies the empty state, suspended loading state, editor mount, Save/Copy ref operations, and ref cleanup. Initial meeting-details JavaScript fell from approximately 801 kB to 261 kB; the editor code remains available in a deferred chunk.

## Likely risks requiring runtime confirmation

macOS cannot cancel an in-flight `AudioDeviceStart` IPC call. The app now bounds its wait and suppresses late results, while the worker retains native resources until macOS returns; a permanently stalled call requires an app restart before another CoreAudio start. Actual OS-level permission-dialog and driver behavior after this change needs runtime confirmation. No new live smoke was performed because the user prohibited computer use.

## Missing tests by failure category

Timeout, native rejection, loading/empty state, caller cancellation, repeated retry, late-result disposal, and existing Stop/recovery flows have automated coverage. Real macOS driver/permission failures remain outside injected tests. Network/API protocol failures, feature toggles, and WebSocket/SSE disconnects were outside this native startup/editor-loading follow-up; it adds no new remote service or streaming protocol.

## Prioritized fix plan

The two remaining code findings are fixed, and the installed release app has been updated as recorded below. A release-bundle scheduled recording and a real OS timeout check remain verification work, rather than unresolved implementations of the Stop plan.

## Regression guardrails

Run the 62 frontend suite tests, TypeScript, the production build, 29 native scheduling tests, and the three `audio_startup` tests plus two `audio::application_stream` tests. The new native regressions do not access microphones or CoreAudio. Keep startup bounded, keep cancelled native results out of live session state, retain the shared startup gate, and preserve the summary editor's forwarded ref when changing its load boundary.

## Release rebuild and installation

On the user's explicit request, the current source was rebuilt into an optimized macOS release with `SDKROOT=<Xcode macOS 26.5 SDK> pnpm exec tauri build --bundles app --ci --config '{"bundle":{"createUpdaterArtifacts":false}}' -- --offline`. The frontend production build and native release build passed. The resulting app was signed locally with the configured ad-hoc identity.

The new bundle replaced `/Applications/meetily.app`. Before replacement, no Meetily process or active recording was present. The source bundle, staged copy, and installed bundle passed deep/strict code-signature verification; the copied file manifest matched the release build. Only after installed verification passed was the previous installed application copy deleted. Recordings, database, models, preferences, and schedules were not modified. No computer-use tools were used, and the rebuilt application was not launched during installation. Version remains `0.4.1` and bundle ID remains `com.meetily.ai`.
