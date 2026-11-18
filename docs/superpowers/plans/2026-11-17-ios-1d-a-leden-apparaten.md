# iOS Leden en apparaten (fase 1d, deel A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A phone that follows the `brievie://server#...` link from the host's invitation page asks for the eight-character pairing code and pairs with the server named in the link **without ever sending the code**: it sends a proof bound to the certificate or host it connected to; every request carries the app version; an expired device token ends in the same pairing screen as a revoked one, with a text that fits both.

**Architecture:** A new pure `ServerLink` (in `BrievieCore/Pairing`) parses the link under the same strict rules as the QR payload, including the code's `pairing` id. A new pure `PairingProof` computes the proof of the host plan's Task 8 (PBKDF2-HMAC-SHA256 from CommonCrypto, HMAC-SHA256 from CryptoKit). `PairingService` gains a second entry, `pair(link:code:deviceName:)`, that reuses the QR path's verification (CA pin, health) and then fetches a nonce and sends the proof; the QR path is unchanged. A small `ServerLinkModel` (in `FeaturePairing`) owns the offer, the refusals and the pairing task; `RootView` routes `onOpenURL` to it by host, so `brievie://task/...` keeps its no-op. The app version is one `AppVersionHeader` in `BrievieAPI`, used by an API client middleware and by the background upload request. The 401 handling already covers expiry and is pinned by a test.

**Tech Stack:** As in 1c (Swift 6 strict concurrency, SwiftUI, iOS 18+, XcodeGen, swift-openapi-generator 1.12.2, Swift Testing, SwiftLint). No new package dependency; CommonCrypto (system library, macOS and iOS) for PBKDF2.

**Spec:** `../Brievie_host/docs/superpowers/specs/2026-11-17-fase-1d-leden-rollen-sso-design.md` ("D" below; sections 6.2 "Straight into the app", 7.1, 13, 14, 20, 21). Host plan: `../Brievie_host/docs/superpowers/plans/2026-11-17-host-1d-a-leden-apparaten.md` (its Task 7 defines the header and the link, its Task 8 the proof protocol and the shared fixture, its Task 9 the expiry; its last section "What the iOS plan for part A must cover" is the checklist for this plan). Previous plan: `docs/superpowers/plans/2026-11-16-ios-fase-1c.md`.

**Prerequisite:** the host's part-A branch has Tasks 7 and 8 done and `docs/api/openapi.json` regenerated (`PairIn.app_version`, `DeviceOut.app_version`, `PairingCodeOut.server_link`, `createPairingChallenge`, `pairDeviceWithProof`). On `main` of the host's worktree today none of these is in the contract; Task 1 step 1 fails the sync check until they are.

## Global Constraints

- `CLAUDE.md` of the iOS repo holds for every task. Binding lines, verbatim: "Code, comments, commits in English. Visible text in Dutch (B1, je-vorm), only via `App/Localizable.xcstrings`; no literal strings in views" (in the packages: through the target's `...Strings` enum and String Catalog, parity tests stay green); "Swift 6, strict concurrency complete. `async/await` only: no `DispatchQueue`, no manual threads."; "No `!`, `try!` or `as!` outside tests. SwiftLint clean (`force_unwrapping` on)."; "Secrets (device token, CA certificate, server config) only in the Keychain"; "Views are dumb: render model state, forward actions. No networking or Keychain in views."; "HTTPS only; no `NSAllowsArbitraryLoads`; non-`https` URLs are rejected at parse time."; "Every screen works with VoiceOver and the largest Dynamic Type size."; "`BrievieAPI` and `BrievieCore` must compile for macOS; guard iOS-only code with `#if canImport(UIKit)` / `#if os(iOS)`."; "No new dependency without a line in `docs/DECISIONS.md`."; "A field the server added later is optional: read it with a default, so the app still works against an older server."; "Tests use Swift Testing. Conventional commits. Never push without being asked."
- `Brievie.xcodeproj` is generated from `project.yml`. This plan does not touch `project.yml` or `App/Info.plist` (the `brievie` scheme is registered since 1c, see Task 5), so `make generate` is not needed.
- No API type written by hand: the generated types stay inside `LiveBrievieServer`. After `make sync-api` the generated names of the new fields and operations (`appVersion: String?`, `pairingId`, `createPairingChallenge`, `pairDeviceWithProof`) must be checked before use.
- No document content, tokens, pairing codes, proofs, nonces or CA fingerprints in logs. The link and the typed code are never logged; diagnostics use `PairingError.failed(String)` as before.
- **The typed code never leaves the phone on the link path** (D 6.2 as changed by D 20). Only `PairingProof` reads it; no request body, header or URL built on the link path contains it. The QR path keeps sending the code to `pairDevice`, as in 1c.
- Header (D 7.1, host Task 7): name `X-Brievie-App-Version`, value `<CFBundleShortVersionString> (<CFBundleVersion>)`, for example `1.4.0 (12)`; the server accepts `^[0-9A-Za-z.+()\- ]{1,50}$` and ignores anything else, so the app sends nothing rather than a value outside that set.
- Link (host Task 7): `brievie://server#v=1&url=<https url, percent-encoded>[&url=...][&ca_sha256=<64 hex>]&pairing=<uuid>`; no code, no token. Unknown keys are ignored.
- Proof (host Task 8), byte for byte: `key = PBKDF2-HMAC-SHA256(password: normalised code (upper case, no "-" or space) as ASCII, salt: "brievie-pair-v1" + pairing id in lower-case hyphenated form as ASCII, iterations: 600000, length: 32)`; `proof = lower-case hex of HMAC-SHA256(key, "brievie-pair-v1" + nonce + trust binding)`; trust binding = the link's `ca_sha256` (lower-case) when the link has one, else the lower-case host name of the URL the app connected to (no brackets, no port, no trailing dot). Note: Swift's `UUID.uuidString` is upper case; use `uuidString.lowercased()`.
- Dutch texts: B1, je-vorm. Words as in the web: "koppelcode", "Ontkoppelen", "toestel" or "telefoon" for this device.
- Commits end with `Co-Authored-By: Claude Code <noreply@anthropic.com>`. Branch `feat/1d-a` from `main`, in its own worktree: `git worktree add ../Brievie_ios-1d-a -b feat/1d-a main`. iCloud conflict copies (" 2", " 3") are never committed. Never push.
- Commands from the repo root: `make test` (whole package), one suite: `swift test --package-path Packages/BrievieKit --filter <Suite>`; `make lint`; `make build` (app target, simulator).

## Review Focus

1. **A link from someone else.** Another app can claim `brievie://`; a hostile page or message can send a link naming its own server. Receiving a link must make no network call and store nothing; only the user's tap on "Koppel" starts contact, and the address is on screen first. Even then the typed code is never sent: a relay server receives a proof bound to its own certificate or host, which the real server refuses. A code smuggled into the link (`code=...`) is never read. → Task 2 (`aCodeInTheLinkIsIgnored`), Task 3 (`theLinkPathNeverSendsTheCode`, `aRelayGetsAProofForItsOwnBinding`), Task 4 (`receivingALinkSendsNothing`).
2. **The proof matches the host byte for byte.** → Task 3 (`proofMatchesTheSharedVector`, the fixture copied verbatim from the host).
3. **Bad links.** `http`, user info in the address, no `v`, another `v`, a pin that is not 64 hex, two pins, no or two `pairing` ids, a `pairing` that is not a UUID, hundreds of `url` items, a fragment of megabytes, a malformed percent escape, `brievie://task/...`. Each is refused with one Dutch sentence, or (task links) left alone, and never crashes or hangs. → Task 2, Task 5 (`taskLinksAreNotServerLinks`).
4. **The app is already paired, or locked, when the link arrives.** Paired and open: refuse with the way out (Ontkoppelen first), change nothing. Paired and locked (Face ID): drop the link; nothing shows above the lock and nothing is kept. An unpaired app never locks. → Task 4.
5. **The typed code.** Lower case, spaces, no dash, trailing newline, 7 or 9 characters, a wrong or used code, too many attempts. Normalised and checked before anything goes out; a refused proof clears the field; a double tap pairs once. → Task 2 (`PairingCode`), Task 4.
6. **A second link while one is open or pairing.** The latest link replaces an open offer (field cleared); while a pairing runs, new links are ignored. → Task 4.
7. **Largest Dynamic Type and VoiceOver on the new screen**, a long server address that must wrap and stay readable. → Task 5 (manual checklist; the layout rules are in the step).

## File Structure

```
Packages/BrievieKit/Sources/
  BrievieAPI/AppVersionHeader.swift           # NEW name, value, current(); no dependency on Core
  BrievieAPI/APIClient.swift                  # + appVersion parameter, AppVersionMiddleware
  BrievieCore/Pairing/PairingCode.swift       # NEW typed code -> "ABCD-EFGH", normalised "ABCDEFGH"
  BrievieCore/Pairing/ServerLink.swift        # NEW parser, address, pairingID
  BrievieCore/Pairing/PairingProof.swift      # NEW key (PBKDF2), proof (HMAC), trust binding
  BrievieCore/Pairing/PairingPayload.swift    # shared URL/hash checks made static internal
  BrievieCore/Pairing/BrievieServer.swift     # + pairingChallenge, pairWithProof (with default implementations)
  BrievieCore/Pairing/PairingService.swift    # + pair(link:code:deviceName:)
  BrievieCore/Server/LiveBrievieServer.swift  # + appVersion; the two new calls
  BrievieCore/Upload/BackgroundUploadTransport.swift  # + static request(for:appVersion:)
  BrievieCore/App/AppStrings.swift (+ Resources/Localizable.xcstrings)   # session.revoked text
  FeaturePairing/PairingFailure.swift         # NEW message + canRetry per PairingError (moved out of PairingModel)
  FeaturePairing/ServerLinkModel.swift        # NEW
  FeaturePairing/ServerLinkView.swift         # NEW sheet
  FeaturePairing/PairingStrings.swift (+ Resources/Localizable.xcstrings)
  FeaturePairing/PairingView.swift            # + closesScanner
App/RootView.swift                            # onOpenURL routing, the sheet
Packages/BrievieKit/Tests/BrievieCoreTests/Fixtures/pairing_proof_v1.json   # NEW, verbatim copy of the host's fixture
Packages/BrievieKit/Tests/...                 # one file per task, named in the tasks
docs/DECISIONS.md  CLAUDE.md                  # Task 7
```

---

### Task 1: The app version on every request

**Files:**
- Create: `Packages/BrievieKit/Sources/BrievieAPI/AppVersionHeader.swift`, `Packages/BrievieKit/Tests/BrievieCoreTests/AppVersionTests.swift`
- Modify: `Packages/BrievieKit/Sources/BrievieAPI/openapi.json` and `API_VERSION` (by `make sync-api`), `BrievieAPI/APIClient.swift`, `BrievieCore/Server/LiveBrievieServer.swift`, `BrievieCore/Upload/BackgroundUploadTransport.swift:78-93` (`start`), `Tests/BrievieCoreTests/LiveBrievieServerTests.swift` (the body assertion of `pairSendsCodeAndNameAndReturnsDevice`)

**Interfaces:**
- Produces (`BrievieAPI`):
  ```swift
  public enum AppVersionHeader {
      public static let name = "X-Brievie-App-Version"
      /// "<short> (<build>)"; nil when either part is missing or the result is outside ^[0-9A-Za-z.+()\- ]{1,50}$.
      public static func value(shortVersion: String?, build: String?) -> String?
      public static func current(bundle: Bundle = .main) -> String?   // reads CFBundleShortVersionString, CFBundleVersion
  }
  public static func APIClient.make(baseURL: URL, session: URLSession, token: String?, maxJSONBytes: Int, appVersion: String? = nil) -> Client
  ```
- Produces (`BrievieCore`): `LiveBrievieServer.init(appVersion: String? = AppVersionHeader.current())` (public); the internal test init gains `appVersion: String? = nil`. `BackgroundUploadTransport.init(trustProvider:appVersion: String? = AppVersionHeader.current())` (public convenience; the internal init gains the same parameter) and `static func request(for upload: UploadRequest, appVersion: String?) -> URLRequest` (internal, pure: method, URL, `Content-Type`, `Authorization`, `Accept`, and the version header when non-nil).
- `BrievieServer.pair(at:trust:code:deviceName:)` does not change: the live server adds `app_version` to the body itself, so the fakes stay as they are for this task.
- The CA download (`CACertificateDownload`) gets no header: it talks to a server nobody has verified yet, and the server only needs the version from a paired device.

- [ ] **Step 1: Sync the contract.** `make sync-api HOST=../Brievie_host/.claude/worktrees/fase-1d` (or the host checkout once part A is merged). Check `git diff --stat` shows `openapi.json` and `API_VERSION` only, and `rg 'app_version|pairDeviceWithProof|createPairingChallenge' Packages/BrievieKit/Sources/BrievieAPI/openapi.json` finds all three. Run `make test`: the existing suite must still pass on the new contract (this is the first compatibility check of Task 7). Commit `chore(api): sync the 1d part A contract`.

- [ ] **Step 2: Write the failing tests** in `AppVersionTests.swift`:
  ```swift
  @Suite struct AppVersionTests {
      @Test func joinsShortVersionAndBuild() { #expect(AppVersionHeader.value(shortVersion: "1.4.0", build: "12") == "1.4.0 (12)") }
      @Test(arguments: [(nil, "1"), ("1.0", nil), ("", "1"), ("1.0", ""), ("<script>", "1"), ("1.0\r\nX-Evil: 1", "1"),
                        (String(repeating: "1", count: 50), "1")] as [(String?, String?)])
      func refusesWhatTheServerWouldIgnore(short: String?, build: String?) { #expect(AppVersionHeader.value(shortVersion: short, build: build) == nil) }
      @Test func everyApiRequestCarriesTheVersion() async throws   // StubURLProtocol: workspaces with a token and health without one -> header "1.4.0 (12)"; Authorization still "Bearer tok" on the first
      @Test func noVersionMeansNoHeader() async throws              // LiveBrievieServer with appVersion nil -> value(forHTTPHeaderField:) == nil
      @Test func pairingSendsTheVersionInTheBodyAndTheHeader() async throws   // body == ["code","device_name","app_version": "1.4.0 (12)"]
      @Test func aBackgroundUploadCarriesTheVersion()               // BackgroundUploadTransport.request(for: upload, appVersion: "1.4.0 (12)"): header, Authorization, Content-Type unchanged; nil -> no header
      @Test func theCACertificateRequestCarriesNoVersion() async throws   // CACertificateDownload.pem against the stub: header nil
  }
  ```
  Update `pairSendsCodeAndNameAndReturnsDevice`: with the default test init (`appVersion` nil) the body is still exactly `["code", "device_name"]`.

- [ ] **Step 3: Run** `swift test --package-path Packages/BrievieKit --filter AppVersionTests` → FAIL (types missing).

- [ ] **Step 4: Implement** `AppVersionHeader` (character check over `utf8`, as `PairingPayload` does; no regex), `AppVersionMiddleware` in `APIClient.swift` (adds the header with `HTTPField.Name(AppVersionHeader.name)`; a failed `Name` init sends the request without it, no `!`), the `appVersion` plumbing, and `BackgroundUploadTransport.request(for:appVersion:)` used by `start` (the task, description and `earliestBeginDate` code stays). `LiveBrievieServer.pair` passes `appVersion` into the generated `PairIn`.

- [ ] **Step 5: Run** `swift test --package-path Packages/BrievieKit --filter AppVersionTests` and `make test`, then `make lint` → PASS.

- [ ] **Step 6: Commit** `feat(api): send the app version on every request`

---

### Task 2: The server link and the typed code

**Files:**
- Create: `Packages/BrievieKit/Sources/BrievieCore/Pairing/PairingCode.swift`, `.../ServerLink.swift`, `Packages/BrievieKit/Tests/BrievieCoreTests/ServerLinkTests.swift`, `PairingCodeTests.swift`
- Modify: `BrievieCore/Pairing/PairingPayload.swift` (make `httpsURL(from:)` and `isSHA256Hex(_:)` `static` internal)

**Interfaces:**
- Produces:
  ```swift
  public struct PairingCode: Equatable, Sendable {
      public let text: String                 // "ABCD-EFGH", shown and typed
      public var normalised: String { get }   // "ABCDEFGH": what the proof key is derived from (host's normalise_code)
      /// Trims, drops spaces and "-", upper-cases; exactly 8 ASCII letters or digits, else nil.
      public init?(typed: String)
  }
  public enum ServerLinkError: Error, Equatable { case notAServerLink, unsupportedVersion, invalid }
  public struct ServerLink: Equatable, Sendable {
      public let urls: [URL]                  // https only, server order, 1...8
      public let caSHA256: String?            // 64 lower-case hex, or nil (public certificate)
      public let pairingID: UUID              // the code's id on the server; not secret
      public static func isServerLink(_ url: URL) -> Bool          // scheme brievie, host "server" (case-insensitive); nothing else
      public static func parse(_ url: URL) throws(ServerLinkError) -> ServerLink
      public var displayAddress: String       // host (and non-default port) of urls[0], as in the URL (ASCII), no scheme or path
  }
  ```
  There is no `payload(code:)`: a link never becomes a `PairingPayload`, because a payload carries the code to `pairDevice` (Task 3 pairs from the link itself).
- Rules of `parse` (the QR rules of `PairingPayload`, same helpers): not `brievie://server` → `.notAServerLink`; the fragment (the part after `#`, at most 4096 characters) is read as `key=value` pairs separated by `&`, values percent-decoded; exactly one `v`, equal to `1` (missing or repeated → `.invalid`; another number → `.unsupportedVersion`); one to eight `url` items, each `https`, with a host, no user info, at most 2048 characters (any bad one → `.invalid`, as the QR refuses the whole payload); at most one `ca_sha256`, 64 hex digits (empty, short, repeated → `.invalid`); exactly one `pairing`, a UUID in any case (missing, repeated, not a UUID → `.invalid`); every other key, including `code`, is ignored. A path or query on the link is ignored.

- [ ] **Step 1: Write the failing tests.** `PairingCodeTests`: `"abcd-2345"`, `" ABCD 2345 "`, `"abcd2345\n"` → `text == "ABCD-2345"`, `normalised == "ABCD2345"`; `"ABCD-234"`, `"ABCD-23456"`, `""`, `"ABCD-23!5"`, `"ÀBCD-2345"` → nil. `ServerLinkTests`, with a fixture link built like the host's test (`brievie://server#v=1&url=https%3A%2F%2Fbrievie.local&url=https%3A%2F%2F192.168.1.4&ca_sha256=` + `ab` x 32 + `&pairing=0b7e2f4c-5d1a-4e8b-9c3f-6a2d8e1f4b70`):
  ```swift
  @Test func parsesTheHostsLink()                       // urls == [https://brievie.local, https://192.168.1.4], caSHA256 == "ab"*32, pairingID as given
  @Test func aLinkWithoutAPinIsAPublicCertificateServer()   // caSHA256 == nil
  @Test func unknownKeysAreIgnored()                    // "&future=x&v2=3" parses the same
  @Test func aCodeInTheLinkIsIgnored()                  // "&code=ABCD-EFGH": parses; ServerLink has no property that holds it (Mirror over the value finds no "ABCD-EFGH")
  @Test func theUpperCasePinIsStoredLowerCase()
  @Test(arguments: ["", "pairing=", "pairing=abc", "pairing=<uuid>&pairing=<uuid>"]) func pairingIdRules()   // missing, empty, not a UUID, repeated -> .invalid
  @Test(arguments: ["http%3A%2F%2Fbrievie.local", "https%3A%2F%2Fuser%3Apw%40brievie.local", "ftp%3A%2F%2Fx", "%20", "", "https%3A%2F%2F"])
  func aBadUrlRefusesTheWholeLink(url: String)          // .invalid; also when a good url is listed beside it
  @Test(arguments: ["", "v=2&url=...", "v=1&v=1&url=...", "url=https%3A%2F%2Fbrievie.local"]) func versionRules()   // invalid / unsupportedVersion / invalid / invalid
  @Test func pinRules()                                 // 63 hex, 65 hex, non-hex, empty value, two pins -> .invalid
  @Test func atMostEightAddressesAndABoundedFragment()  // 9 urls -> .invalid; a 5000-character fragment -> .invalid
  @Test func malformedPercentEscapesAreInvalidNotACrash()   // "url=%ZZ"
  @Test func otherLinksAreNotServerLinks()              // brievie://task/<id>, https://server#v=1, brievie://Server#... is a server link (case-insensitive)
  @Test func displayAddressShowsHostAndPortOnly()       // "brievie.local:8443"; an IPv6 host keeps its brackets
  ```
- [ ] **Step 2: Run** `swift test --package-path Packages/BrievieKit --filter "ServerLinkTests|PairingCodeTests"` → FAIL.
- [ ] **Step 3: Implement** `PairingCode`, `ServerLink` and the `PairingPayload` changes as specified above. Use `URLComponents` with `percentEncodedFragment`; split pairs by hand and decode with `removingPercentEncoding` (nil → `.invalid`). `displayAddress` uses `URL.host(percentEncoded: true)`: never a decoded or Unicode form, so what is shown is what is contacted.
- [ ] **Step 4: Run** the same filter, then `make test` and `make lint` → PASS.
- [ ] **Step 5: Commit** `feat(pairing): parse the server link and the typed pairing code`

---

### Task 3: The proof, and pairing from a link without sending the code

**Files:**
- Create: `Packages/BrievieKit/Sources/BrievieCore/Pairing/PairingProof.swift`, `Packages/BrievieKit/Tests/BrievieCoreTests/Fixtures/pairing_proof_v1.json`, `Packages/BrievieKit/Tests/BrievieCoreTests/PairingProofTests.swift`, `Packages/BrievieKit/Tests/BrievieCoreTests/PairingServiceLinkTests.swift`
- Modify: `BrievieCore/Pairing/BrievieServer.swift`, `BrievieCore/Pairing/PairingService.swift`, `BrievieCore/Server/LiveBrievieServer.swift`, `BrievieCore/Server/ServerError+Mapping.swift`, `Tests/BrievieCoreTests/Support/FakeBrievieServer.swift`, `Tests/BrievieCoreTests/LiveBrievieServerTests.swift`

**Interfaces:**
- Produces (`PairingProof.swift`):
  ```swift
  public enum PairingProof {
      static let context = "brievie-pair-v1"
      static let iterations: UInt32 = 600_000
      /// PBKDF2-HMAC-SHA256 (CommonCrypto CCKeyDerivationPBKDF) of code.normalised, salt context + pairingID.uuidString.lowercased(); 32 bytes. nil only if CommonCrypto fails.
      static func key(code: PairingCode, pairingID: UUID) -> Data?
      /// Lower-case hex of HMAC-SHA256(key, context + nonce + trustBinding) (CryptoKit).
      static func proof(key: Data, nonce: String, trustBinding: String) -> String
      /// caSHA256 (lower case) when present; else url's host lower-cased, without brackets and a trailing dot; nil without a host.
      static func trustBinding(caSHA256: String?, connectedTo url: URL) -> String?
  }
  ```
- Produces (`BrievieServer`), two requirements, each with a default implementation in a protocol extension that throws `ServerError.other("not_implemented")` (so the five test conformers compile unchanged; `LiveBrievieServer` implements both and a test calls them through the stub):
  ```swift
  /// A one-time nonce for pairing by proof (POST /devices/pairing-challenges). A 1c server answers 404.
  func pairingChallenge(at url: URL, trust: ServerTrust) async throws -> String
  /// POST /devices/pair-with-proof. The code is not a parameter: it never leaves the phone on this path.
  func pairWithProof(at url: URL, trust: ServerTrust, pairingID: UUID, nonce: String, proof: String, deviceName: String) async throws -> PairedDevice
  ```
  Mapping (`ServerError+Mapping.swift`): `pairDeviceWithProof` 400 `pairing_code_invalid` → `.invalidCode`; 400 `pairing_challenge_invalid` → `.other("pairing_challenge_invalid")`; 429 → `.tooManyAttempts`; `createPairingChallenge` 404 → `.other("http_404")`. The live `pairWithProof` adds `app_version` to the body as `pair` does (Task 1). The nonce from the server is accepted only as 1...64 characters of `A-Za-z0-9_-`; anything else → `.incompatible`.
- Produces (`PairingService`):
  ```swift
  /// Tries the link's URLs as `pair(_:deviceName:)` does (CA pin and health first; an unverifiable URL is skipped), then on the
  /// verified one: pairingChallenge, PairingProof, pairWithProof. The code is never sent; nothing is stored unless pairing succeeds.
  public func pair(link: ServerLink, code: PairingCode, deviceName: String) async throws(PairingError) -> ServerConnection
  ```
  The verification step (`verify`) is shared with the QR path, unchanged. After it: the challenge; a 404 there → `.incompatibleServer` (a server without the proof path; the code was not sent and is not spent); `.unreachable`/`.untrusted` there → `.noServerReachable` (nothing was spent). The key derivation runs after the challenge (about half a second; cancellation is checked before and after). The proof call's failures map like `finish` does today: `.unreachable`/`.untrusted` → `.connectionLostWhilePairing`, `.invalidCode` → `.invalidCode`, `.other("pairing_challenge_invalid")` → `.failed("pairing_challenge_invalid")`. The trust binding is computed for the URL the challenge and proof go to. Storing the `ServerConnection` is the same code as the QR path (`urls`, `caCertificateDER`, `deviceID`, `userName`).
- The fixture `Tests/BrievieCoreTests/Fixtures/pairing_proof_v1.json` is a verbatim copy of the host's `backend/tests/fixtures/pairing_proof_v1.json` (host plan Task 8), read in the test through `#filePath` (no `Package.swift` resource change):

```json
{
  "context": "brievie-pair-v1",
  "iterations": 600000,
  "typed_code": "abcd-2345",
  "normalised_code": "ABCD2345",
  "pairing_id": "0b7e2f4c-5d1a-4e8b-9c3f-6a2d8e1f4b70",
  "nonce": "q3Vb8xN2pL5sT9wE1yH4kM7rC0uZ6aJdFgQiXoWnYeK",
  "key": "b0d30d4a0224e576656286cc732bc500d8b93217860fd69ca42cb95cf245105f",
  "cases": [
    {"trust_binding": "abababababababababababababababababababababababababababababababab",
     "proof": "f658f764994838ca7b728313d410619295065549d3ccf22a04746ec95c0f2805"},
    {"trust_binding": "brievie.example.nl",
     "proof": "f6a8154668ac8fcdcdb1d516278aed3ad86bba62f6d993fd590c64bc54e641ad"}
  ]
}
```

- [ ] **Step 1: Write the failing tests.**
  ```swift
  // PairingProofTests
  @Test func proofMatchesTheSharedVector() throws      // fixture: PairingCode(typed: typed_code).normalised == normalised_code; key hex == key; each case's proof
  @Test func theFixtureIsTheHostsFile() throws          // the file's SHA-256 equals the value recorded in the test, so an edit on one side shows
  @Test func thePairingIdIsLowerCasedForTheSalt()       // UUID(uuidString: upper-case id) gives the same key as the lower-case one
  @Test func trustBindingPrefersThePin()                // caSHA256 "AB.."(upper) -> "ab.."; no pin + https://Brievie.Example.NL.:8443/x -> "brievie.example.nl"; https://[2001:db8::1] -> "2001:db8::1"
  // PairingServiceLinkTests, with FakeBrievieServer recording every call and body
  @Test func theLinkPathNeverSendsTheCode() async throws
      // link pairing succeeds; no recorded request body, header or URL contains "ABCD2345" or "ABCD-2345"; pair(at:...code:) never called
  @Test func aRelayGetsAProofForItsOwnBinding() async throws
      // link without pin naming https://attacker.example: the proof sent equals PairingProof.proof(... trustBinding: "attacker.example"),
      // not the proof for "brievie.example.nl"
  @Test func theLinkPairingVerifiesThePinFirst() async throws   // a CA whose SHA-256 differs from the link -> .serverNotVerified; no challenge requested
  @Test func aServerWithoutTheProofPathIsIncompatible() async throws   // challenge 404 -> .incompatibleServer; nothing stored; no proof sent
  @Test func aRefusedProofIsAnInvalidCode() async throws        // 400 pairing_code_invalid -> .invalidCode; nothing stored
  @Test func aLostConnectionAfterTheProofMaySpendTheCode() async throws   // proof call .unreachable -> .connectionLostWhilePairing
  @Test func theLinkPairingStoresTheSameConnectionAsTheQrPath() async throws   // urls, CA, deviceID, userName as pair(_:deviceName:) would store
  @Test func theQrPathStillSendsTheCode() async throws          // pair(_ payload:) unchanged: pair(at:...code: "ABCD-2345") called, no challenge
  // LiveBrievieServerTests
  @Test func challengeAndProofHitTheContractPaths() async throws   // stub: POST /api/v1/devices/pairing-challenges, POST /api/v1/devices/pair-with-proof with body keys pairing_id, nonce, proof, device_name, app_version
  ```
- [ ] **Step 2: Copy the fixture** from the host branch (`cp ../Brievie_host/.claude/worktrees/fase-1d/backend/tests/fixtures/pairing_proof_v1.json Packages/BrievieKit/Tests/BrievieCoreTests/Fixtures/`) once host Task 8 is done; compare with the block above (identical). Record its SHA-256 in `theFixtureIsTheHostsFile`.
- [ ] **Step 3: Run** `swift test --package-path Packages/BrievieKit --filter "PairingProofTests|PairingServiceLinkTests|LiveBrievieServerTests"` → FAIL.
- [ ] **Step 4: Implement** `PairingProof` (`import CommonCrypto` for `CCKeyDerivationPBKDF` with `kCCPBKDF2` and `kCCPRFHmacAlgSHA256`; `import CryptoKit` for `HMAC<SHA256>`; no `!`), the protocol requirements with defaults, the live calls and mapping, the fake's recording, and `PairingService.pair(link:code:deviceName:)` sharing `verify` and the storing code with the QR path.
- [ ] **Step 5: Run** the same filter, then `make test` and `make lint` → PASS.
- [ ] **Step 6: Commit** `feat(pairing): pair from a server link with a proof; the code never leaves the phone`

---

### Task 4: The offer, the refusals and the pairing (`ServerLinkModel`)

**Files:**
- Create: `Packages/BrievieKit/Sources/FeaturePairing/ServerLinkModel.swift`, `FeaturePairing/PairingFailure.swift`, `Packages/BrievieKit/Tests/FeaturePairingTests/ServerLinkModelTests.swift`
- Modify: `FeaturePairing/PairingModel.swift` (use `PairingFailure`; behaviour unchanged), `FeaturePairing/PairingStrings.swift` (+ `all`), `FeaturePairing/Resources/Localizable.xcstrings`

**Interfaces:**
- Consumes: `ServerLink`, `PairingCode` (Task 2), `PairingService.pair(link:code:deviceName:)`, `PairingError` (Task 3), `AppModel.State`, `ServerConnection` (BrievieCore).
- Produces:
  ```swift
  enum PairingFailure { static func describe(_ error: PairingError) -> (message: String, canRetry: Bool)? }   // nil for .cancelled
  @MainActor @Observable public final class ServerLinkModel {
      public enum Presentation: Equatable {
          case notice(String)                                         // bad link, newer version, already paired: one sentence and "Sluit"
          case offer(address: String, failure: String?, isPairing: Bool)
      }
      public private(set) var presentation: Presentation?
      public var code: String                                         // what the user typed
      public var canSubmit: Bool                                      // an offer, not pairing, PairingCode(typed: code) != nil
      public init(service: PairingService, deviceName: @escaping @MainActor () -> String,
                  onPaired: @escaping @MainActor (ServerConnection) -> Void)
      public func receive(_ url: URL, appState: AppModel.State)
      public func appStateChanged(_ state: AppModel.State)            // .locked or .ready closes everything that is not pairing
      public func submit()                                            // starts the owned task; ignored unless canSubmit
      public func dismiss()                                           // ignored while pairing
      @ObservationIgnored private(set) var pairingTask: Task<Void, Never>?
  }
  ```
- Behaviour: `receive` with `.locked` → nothing (no state kept, nothing shown); `.ready` → `.notice(PairingStrings.linkAlreadyPaired)`; `.unpaired` → parse: `.invalid`/`.notAServerLink` → `.notice(PairingStrings.linkInvalid)`, `.unsupportedVersion` → `.notice(PairingStrings.linkUnsupportedVersion)`, success → `.offer(address: link.displayAddress, failure: nil, isPairing: false)` with `code` cleared and the link kept privately. While `isPairing`, `receive` is ignored. `submit` pairs through `PairingService.pair(link:code:deviceName:)` (device name as `PairingModel`: trimmed, `"iPhone"` when empty). Success: `presentation = nil`, `code = ""`, then `onPaired`. A failure keeps the offer and shows the message from `PairingFailure`; `invalidCode` and `connectionLostWhilePairing` (the code may be spent) also clear `code`; `noServerReachable`, `tooManyAttempts`, `incompatibleServer` keep it. `.cancelled` returns to the idle offer. The task is owned by the model and survives the sheet going away, like `PairingModel.pairingTask`: once the server accepted the proof the pairing is stored and reported.
- New strings (`PairingStrings`, keys also in the catalog and in `all`): `linkInvalid` `pairing.link.error.invalid` "Deze link werkt niet. Maak op de webpagina een nieuwe koppelcode en open de link opnieuw."; `linkUnsupportedVersion` `pairing.link.error.unsupportedVersion` "Deze link komt van een nieuwere versie van Brievie. Werk de app bij en probeer het opnieuw."; `linkAlreadyPaired` `pairing.link.error.alreadyPaired` "Deze telefoon is al gekoppeld met een Brievie-server. Wil je een andere server gebruiken? Tik dan in je werkruimtes op Ontkoppelen en open de link opnieuw."

- [ ] **Step 1: Refactor under the existing tests.** Move `PairingModel.phase(for:)` into `PairingFailure.describe`; `swift test --filter PairingModelTests` stays green. Commit `refactor(pairing): share the pairing failure texts`.
- [ ] **Step 2: Write the failing tests** in `ServerLinkModelTests.swift`, with the file's own `StubServer` (as in `PairingModelTests`, now also answering `pairingChallenge` and `pairWithProof`; make it shared if the file allows) and `InMemorySecretStore`:
  ```swift
  @Test func receivingALinkSendsNothing()                       // unpaired + valid link: presentation == .offer(...); server.healthCalls == 0, challengeCalls == 0, proofCalls == 0, nothing stored
  @Test func offerShowsTheAddressNotTheLink()                   // address "brievie.local"; no pin, no url list, no pairing id in the presentation
  @Test func pairingNeedsAValidTypedCode()                      // canSubmit false for "", "ABCD-23"; submit() does nothing (healthCalls == 0)
  @Test func aTypedCodePairsByProof()                           // code "abcd 2345": the server receives a proof and never the code; connection reported; presentation nil; code ""
  @Test func theLinkPairingIsTheSameServerAsTheQrPairing()      // connection.serverID equals the QR pairing's serverID, so queued scans go out
  @Test func aDoubleTapPairsOnce()                              // submit(); submit(): proofCalls == 1
  @Test func aRefusedProofClearsTheFieldAndKeepsTheOffer()      // .invalidCode and .connectionLostWhilePairing: code == "", failure text set, offer still there
  @Test func anUnreachableServerKeepsTheCode()                  // .noServerReachable, .tooManyAttempts, .incompatibleServer
  @Test func aNewLinkReplacesTheOfferAndClearsTheCode()
  @Test func linksAreIgnoredWhilePairing()                      // gated server: a second receive() changes nothing
  @Test func anAlreadyPairedAppRefusesAndChangesNothing()       // .ready: .notice(linkAlreadyPaired); store untouched; onPaired not called
  @Test func aLockedAppDropsTheLink()                           // .locked: presentation == nil; after appStateChanged(.ready(...)) still nil
  @Test func lockingClosesTheSheet()                            // offer open, appStateChanged(.locked): presentation == nil (not while pairing)
  @Test func malformedAndNewerLinksAreOneSentenceEach()         // .notice(linkInvalid) / .notice(linkUnsupportedVersion)
  @Test func aPairingThatSucceededDespiteCancellationIsStillReported()   // as PairingModelTests
  @Test func diagnosticTextNeverReachesTheUser()                // .failed("x") shows PairingStrings.generic
  @Test func catalogAndCodeAgree()                              // PairingStrings.all vs the catalog, as PairingModelTests
  ```
- [ ] **Step 3: Run** `swift test --package-path Packages/BrievieKit --filter ServerLinkModelTests` → FAIL.
- [ ] **Step 4: Implement** `ServerLinkModel` and the strings as specified.
- [ ] **Step 5: Run** the suite, `make test`, `make lint` → PASS.
- [ ] **Step 6: Commit** `feat(pairing): the offer and refusals for a server link`

---

### Task 5: The screen, the wiring and accessibility

**Files:**
- Create: `Packages/BrievieKit/Sources/FeaturePairing/ServerLinkView.swift`, `Packages/BrievieKit/Tests/FeaturePairingTests/ServerLinkWiringTests.swift`
- Modify: `FeaturePairing/PairingView.swift` (add `closesScanner: Bool = false`), `FeaturePairing/Resources/Localizable.xcstrings`, `App/RootView.swift`

**Interfaces:**
- Consumes: `ServerLinkModel` (Task 4), `PrimaryButton`, `ErrorBanner`, `BrandMark` from `BrievieUI`.
- Produces: `public struct ServerLinkView: View { public init(model: ServerLinkModel) }` (`#if os(iOS)`), shown as a sheet; `PairingView.init(model:notice:closesScanner:)`.
- Catalog keys (view only, Dutch values fixed here): `pairing.link.title` "Koppelen met je server"; `pairing.link.intro` "Je hebt een link van je Brievie-server geopend. Hiermee koppel je deze telefoon."; `pairing.link.address.label` "Adres van de server"; `pairing.link.address.check` "Is dit niet het adres van jouw server? Sluit dan dit scherm. Er is dan niets verstuurd."; `pairing.link.code.label` "Koppelcode"; `pairing.link.code.prompt` "8 tekens"; `pairing.link.code.footer` "Typ de code die op de webpagina staat. Hoofdletters en het streepje maken niet uit. De code zelf gaat niet over het netwerk."; `pairing.link.code.hint` "Typ de acht tekens van de koppelcode."; `pairing.link.submit` "Koppel"; `pairing.link.close` "Sluit". Progress reuses `pairing.progress`.

- [ ] **Step 1: Write the failing tests** (`ServerLinkWiringTests`, pure file reads; the view and `RootView` have no unit tests, as for the other screens):
  ```swift
  @Test func theBrievieSchemeIsRegistered()          // App/Info.plist (five levels up from #filePath): CFBundleURLTypes holds scheme "brievie"
  @Test func taskLinksAreNotServerLinks()            // ServerLink.isServerLink(brievie://task/<uuid>) == false
  @Test func viewTextsInTheCatalogUseTheAgreedWording()   // every pairing.link.* key above equals its Dutch value (CatalogParity)
  ```
- [ ] **Step 2: Run** `swift test --package-path Packages/BrievieKit --filter ServerLinkWiringTests` → FAIL on the catalog test.
- [ ] **Step 3: Add the catalog entries**, same shape as the existing ones (`"extractionState": "manual"`, `nl` `translated`).
- [ ] **Step 4: Implement `ServerLinkView`.** `NavigationStack` with a `List` (grouped), title as a header trait. `.notice`: an `ErrorBanner` (`.warning` for already paired) and a "Sluit" button. `.offer`: intro; a section "Adres van de server" with the address as `Text` (`.font(.title3.weight(.semibold))`, no line limit, `.fixedSize(horizontal: false, vertical: true)`, `.textSelection(.enabled)`) and the check sentence below it; a section with the code `TextField` bound to `model.code`: `.textInputAutocapitalization(.characters)`, `.autocorrectionDisabled()`, `.keyboardType(.asciiCapable)`, `.textContentType(.none)`, `.submitLabel(.go)`, `.onSubmit { model.submit() }`, `.monospaced()`, `accessibilityLabel` "Koppelcode", hint `pairing.link.code.hint`; the footer sentence; failure as `ErrorBanner`; the "Koppel" button in a section footer as `PairingView` does (a list row clips it), `.disabled(!model.canSubmit)`; progress row while pairing. `.interactiveDismissDisabled(isPairing)`; "Sluit" in the toolbar, disabled while pairing. Focus: the code field is focused when the offer appears (`@FocusState`); a new failure posts `AccessibilityNotification.Announcement(message)`. No fixed heights or widths; nothing inside an `HStack` that cannot wrap.
- [ ] **Step 5: Wire it.** `RootView`: a `@State private var linkModel: ServerLinkModel` made in `init(appModel:environment:)` with `PairingService(server: environment.server, store: environment.store)`, `UIDevice.current.name` and `appModel.didPair`; replace `.onOpenURL { _ in }` by `guard ServerLink.isServerLink(url) else { return }` then `linkModel.receive(url, appState: appModel.state)` (the comment about task links stays true for everything else); `.onChange(of: appModel.state)` → `linkModel.appStateChanged`; a `.sheet(isPresented:)` on `body` whose setter calls `linkModel.dismiss()`, content `ServerLinkView(model: linkModel)`. `PairingScreen` passes `closesScanner: linkModel.presentation != nil`, and `PairingView` closes its scanner sheet when it turns true (two sheets must not compete).
- [ ] **Step 6: Run** the suite, `make test`, `make lint`, `make build` → PASS.
- [ ] **Step 7: Manual checklist** (simulator is enough; record results in the commit body or the task report): `xcrun simctl openurl booted "brievie://server#v=1&url=https%3A%2F%2Fbrievie.local&pairing=0b7e2f4c-5d1a-4e8b-9c3f-6a2d8e1f4b70"` shows the sheet; with the app paired it shows the refusal; at accessibility-extra-extra-extra-large (`xcrun simctl ui booted content_size accessibility-extra-extra-extra-large`) the address wraps, nothing is cut off and the sheet scrolls; VoiceOver reads title, address, check sentence, field with label and hint, button state; the failure is announced; `brievie://task/1` only opens the app.
- [ ] **Step 8: Commit** `feat(pairing): the server link sheet and its wiring`

---

### Task 6: An expired token looks like a revoked one

**Files:**
- Create: `Packages/BrievieKit/Tests/BrievieCoreTests/ExpiredTokenTests.swift`
- Modify: `Packages/BrievieKit/Sources/BrievieCore/App/AppStrings.swift:12-18`, `BrievieCore/Resources/Localizable.xcstrings` (`session.revoked`), `Tests/BrievieCoreTests/AppStringsTests.swift` (the pair stays, the value changes by itself)

**Interfaces:**
- Checked in code (no change needed): the host answers an expired token with `401`, `{"code":"not_authenticated","message":"Je bent niet ingelogd."}` (host Task 9). `ServerError.init(status:code:)` maps `(401, "not_authenticated")` to `.unauthorized`; every document and workspace call and `UploadPolicy.decide(.status(401, code: "not_authenticated"), ...)` end in `AppModel.sessionRevoked(deviceID:)`, which forgets the pairing and shows `AppStrings.sessionRevoked` on the pairing screen. The 1c tests cover the mapping piece by piece (`RevocationVerdictTests`, `UploadPolicyTests`, `UploadCoordinatorTests`); nothing pins the whole chain against the host's exact body, and the text only fits a manual revocation ("Dit apparaat is ontkoppeld. Koppel het opnieuw om verder te gaan.").
- Produces: `AppStrings.sessionRevoked` (same key `session.revoked`), new text: "Deze koppeling werkt niet meer: ze is ingetrokken of je hebt Brievie lang niet gebruikt. Koppel dit toestel opnieuw om verder te gaan."

- [ ] **Step 1: Write the failing tests** in `ExpiredTokenTests.swift`:
  ```swift
  @Test func theHostsExpiredTokenAnswerIsARevocation() async throws     // stub 401 with the exact body above -> server.workspaces throws .unauthorized
  @Test func theSameAnswerOnAnUploadIsARevocation()                     // UploadPolicy.decide(.status(401, code: "not_authenticated"), ...) == .revoked
  @Test func aRevokedAndAnExpiredDeviceEndOnTheSamePairingScreen()      // AppModel built as in AppModelTests: sessionRevoked(deviceID:) -> state == .unpaired(notice: AppStrings.sessionRevoked); the pairing and token are gone from the store
  @Test func theNoticeFitsBothCauses()                                  // contains "Koppel dit toestel opnieuw", "ingetrokken" and "lang niet gebruikt"; does not claim the user unpaired it
  @Test func aMissingCodeIsNotAnExpiry()                                // 401 with another or no code stays .other("http_401"): a reverse proxy never unpairs
  ```
- [ ] **Step 2: Run** `swift test --package-path Packages/BrievieKit --filter ExpiredTokenTests` → only `theNoticeFitsBothCauses` FAILS.
- [ ] **Step 3: Change the text** in `AppStrings.sessionRevoked` and the catalog, identical.
- [ ] **Step 4: Run** the suite, `AppStringsTests`, `make test`, `make lint` → PASS.
- [ ] **Step 5: Commit** `fix(app): the unpaired notice fits a revoked and an expired device`

---

### Task 7: Compatibility with a 1c server, and the decisions

**Files:**
- Create: `Packages/BrievieKit/Tests/BrievieCoreTests/OneCServerCompatibilityTests.swift`
- Modify: `docs/DECISIONS.md` (append), `CLAUDE.md` ("Current plan" line)

**Interfaces:** none new.

- [ ] **Step 1: Write the tests** (they should pass at once on a correct Task 1 to 4; a failure means a new field was made required):
  ```swift
  @Test func aOneCPairAnswerWithoutTheNewFieldsDecodes() async throws        // stub 201 as in LiveBrievieServerTests (user without is_server_admin) -> PairedDevice
  @Test func aOneCWorkspaceListDecodes() async throws                         // list answer without any 1d field
  @Test func aOneDAnswerWithExtraFieldsDecodes() async throws                 // is_server_admin, an unknown workspace role and an unknown key are read; the role is .unknown
  @Test func theVersionHeaderAndBodyFieldAreHarmlessToAOneCServer() async throws   // a stub that answers 201 whatever it receives: pairing works with appVersion set
  @Test func qrPairingWorksAgainstAOneCServer() async throws                  // FakeBrievieServer (health "1.0.0"): PairingService.pair(payload) stores the connection
  @Test func linkPairingAgainstAServerWithoutTheProofPathSendsNoCode() async throws
      // FakeBrievieServer (health "1.0.0", challenge -> 404): .incompatibleServer; nothing stored; no request carried the code
  @Test(arguments: ["1.0.0", "1.4.2"]) func anyOneXServerIsSupported(version: String) async throws
  @Test func aTwoXServerIsIncompatible() async throws                          // health "2.0.0" -> .incompatibleServer, nothing stored, the code never sent
  ```
- [ ] **Step 2: Run** `swift test --package-path Packages/BrievieKit --filter OneCServerCompatibilityTests` → PASS (if a test fails, fix the code, not the test).
- [ ] **Step 3: Write `docs/DECISIONS.md`** `## Members and devices (phase 1d, part A)`, one line each: the link carries addresses, pin and the code's pairing id; the app never sends the typed code from a link but a proof (PBKDF2 600,000 + HMAC-SHA256, bound to the pin or the host it connected to; why the address is shown first); the shared fixture and that it is a verbatim copy of the host's; CommonCrypto for PBKDF2 (system library, no package); the two new `BrievieServer` requirements with throwing defaults for test conformers; the already-paired refusal and the dropped link while locked; the scheme was registered in 1c and `onOpenURL` routes by host; `AppVersionHeader` and the rule "send nothing rather than a value the server ignores"; no header on the CA download; the new `session.revoked` text; offer survives backgrounding and the typed code is not cleared (accepted risk, one-time code that never leaves the phone); no new dependency. Update the "Current plan" line of `CLAUDE.md`.
- [ ] **Step 4: Final run** `make lint && make test && make build` → all green.
- [ ] **Step 5: Commit** `test(pairing): a 1c server still works; record the part A decisions`

---

## Plan decisions

1. **Address, not name.** The design says the app "shows the server's name"; the link carries no name (addresses, pin and pairing id only, by design: nothing else is known before pairing). The sheet shows the address. The server's name is not shown before pairing.
2. **The link is only a pointer, and the code stays on the phone** (controller ruling after the independent check; D 6.2 and D 20). The code is typed by hand and normalised; on the link path it is only the input of `PairingProof`. Nothing is sent from a link before the user types a code and taps "Koppel"; there is no pre-fetch of the CA or health, so a hostile link cannot make the phone contact a server by itself. A relay named by a hostile link receives a proof bound to its own certificate or host; the real server refuses it, and a guess of the code from it costs 600,000 PBKDF2 iterations per try.
3. **The QR path is unchanged** (the code goes to `pairDevice`): the QR comes from the server's own page, so the address in it is the server's own, and a 1c server only knows that path.
4. **A server without the proof path** (a 1c server, or 404 on `createPairingChallenge`) gets `.incompatibleServer` on the link path: no fallback to sending the code, ever. No 1c server makes links, so no real user meets this.
5. **The two new `BrievieServer` requirements have throwing defaults**, so the five test conformers (`FakeBrievieServer`, the `StubServer`s of `PairingModelTests` and `WorkspacesModelTests`, and others) compile unchanged; `LiveBrievieServer` implements both and `challengeAndProofHitTheContractPaths` proves it. The risk of a production conformer relying on a default is accepted: there is one.
6. **The shared vector is stated as hex** in both plans, computed once from fixed inputs (Python `hashlib.pbkdf2_hmac` and `hmac`; one HMAC re-checked with `openssl`); the iOS fixture is a verbatim copy of the host file and a test pins its digest.
7. **Already paired:** refuse with the way out, never replace. Unpairing stays an explicit act with its own questions about scans and calendars.
8. **Locked:** the link is dropped, nothing is kept, nothing shows above the lock. An unpaired app has no lock. After a reinstall with a leftover Keychain entry the app is `.locked` until the first-launch check clears it; a link in that window is dropped too (the user taps the link again).
9. **One header owner:** `AppVersionHeader` in `BrievieAPI`, used by the middleware and the upload request; `pair` and `pairWithProof` keep the code-free protocol signatures and the live server adds `app_version` to the body.
10. **No header on the CA download**, and none when the bundle's version is outside the server's accepted set.
11. **The 401 chain is unchanged**; a test pins it against the host's exact body, and the notice text names both causes.
12. **`project.yml` and `Info.plist` untouched:** the `brievie` scheme has been registered since 1c for task links.
13. **Checked, no task:** a member removed from one of several workspaces gets `404 workspace_not_found`, which passes through `ServerError`, shows `DocumentStrings.workspaceGone` on a workspace screen and fails an upload permanently; the list drops the workspace on its next load, no sign-out. Calendar events follow the open-task list (the calendar is the state), so tasks that vanish lose their event at the next sync; the calendar of a workspace that vanished from the list is not synced any more and stays until the user removes it (an open point for part B, where it becomes common).
14. **Typed code and snapshots:** the offer survives going to the background (the user must go back to Safari to read the code); the code is a one-time, short-lived value that never leaves the phone, so it is not cleared and the unpaired app keeps having no privacy cover.
15. **The earlier test `pairingFromALinkWorksAgainstAOneCServer` is replaced, not weakened:** it asserted that a link pairs with a 1c server by sending the code, which the new rule forbids; `linkPairingAgainstAServerWithoutTheProofPathSendsNoCode` asserts the stricter behaviour and `qrPairingWorksAgainstAOneCServer` keeps the 1c pairing covered.

## Not verifiable without a real device

- A real phone following `brievie://server#...` from Safari (and from the mail or chat app that holds the link), including the app opening cold from the link and from the background.
- Another installed app that also claims `brievie` (iOS then picks one without telling): the sheet must never be reachable without the user choosing this app; only a second app can show this.
- Face ID lock timing: a link arriving while the paired app is locked, and while Face ID's own sheet is up.
- The camera scanner sheet being open when a link arrives (`closesScanner`).
- A private-CA server on the home network: the sheet's pairing with the pin from the link (the CA download and TLS pinning run against a real certificate), and "Lokaal netwerk" permission on the first contact.
- The time PBKDF2 with 600,000 iterations takes on the oldest supported iPhone (expected well under a second; the progress row covers it).
- Keyboard, VoiceOver focus and the announcement of a failure on a physical device; Dynamic Type at the largest accessibility size on a small screen.
- A device whose token really expired after 180 days: only the server's 401 is simulated here.
