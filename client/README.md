# Universal Downloader — Windows client

WPF (.NET 8) desktop client for the Universal Downloader SaaS platform.
Implements the customer-facing flows from SRS v1.1 / SDS v1.1:
login, source management, daily-batch downloads, job monitoring, and settings.

> **Build environment note.** This client was authored on a Linux VM **without
> the .NET SDK**, so it has **not been compiled or run**. The code was written
> against .NET 8 / C# 12 / CommunityToolkit.Mvvm 8.2.2 APIs and self-reviewed
> file by file, but you must build it on Windows before trusting it.
> The most likely compile risks are flagged in the "Known review risks" section
> below — please read it before the first build.

## Solution layout

```
client/
├── UniversalDownloader.sln
├── src/
│   ├── UniversalDownloader.Core/            # net8.0 — no external deps
│   │   ├── Models/         Enums, Entities (Customer, Subscription, Source,
│   │   │                   MediaItem, Batch, DownloadJob)
│   │   ├── StateMachine/   JobStateMachine — the 14-state lifecycle (SRS §8)
│   │   ├── Naming/         FileNamingTemplate — {platform}/{source}/… resolver
│   │   └── Configuration/  ClientSettings (JSON under %AppData%), TimeZoneHelper
│   ├── UniversalDownloader.Infrastructure/  # net8.0
│   │   ├── Api/            ApiClient (all 18 customer endpoints, SDS §9 v1.1),
│   │   │                   ApiModels (snake_case DTOs), DtoMapper
│   │   ├── Auth/           AuthService, DpapiTokenStore (refresh token DPAPI-
│   │   │                   encrypted under %AppData%; access token memory-only),
│   │   │                   AuthenticatedHttpHandler (refresh-on-401)
│   │   └── Cache/          LocalCache — SQLite mirror of sources/jobs
│   ├── UniversalDownloader.Worker/          # net8.0
│   │   ├── Download/       DownloadWorker (concurrency, resume, pause/resume/
│   │   │                   retry, per-read timeouts, verification, throttled
│   │   │                   server status reports), RetryPolicy
│   │   └── Networking/     NetworkMonitor (NetworkChange + /health probe)
│   └── UniversalDownloader.App/             # net8.0-windows — WPF UI
│       ├── App.xaml(.cs)   DI composition, SDS §14 startup sequence
│       ├── MainWindow       Navigation shell
│       ├── Views/          Login, Dashboard, Sources, Jobs, Settings
│       ├── ViewModels/     MVVM view-models (CommunityToolkit.Mvvm)
│       └── Services/       SessionState, SessionCoordinator (sync + enqueue)
├── tests/
│   └── UniversalDownloader.Tests/  xUnit: state machine, naming, retry, banners
└── installer/              MSIX packaging notes + sample Package.appxmanifest
```

## Build (Windows)

Prerequisites: **.NET 8 SDK** and **Visual Studio 2022 17.8+** (or VS Build Tools).

```powershell
cd client
dotnet restore UniversalDownloader.sln
dotnet build UniversalDownloader.sln -c Release
dotnet test tests\UniversalDownloader.Tests\UniversalDownloader.Tests.csproj
```

Run the app from Visual Studio (set `UniversalDownloader.App` as startup project),
or `dotnet run --project src\UniversalDownloader.App`.

### Standalone UniversalDownloader.exe

On any Windows PC with the .NET 8 SDK:

```powershell
cd client
dotnet publish src\UniversalDownloader.App\UniversalDownloader.App.csproj `
  -c Release -r win-x64 --self-contained `
  -p:PublishSingleFile=true -p:AssemblyName=UniversalDownloader `
  -o publish
```

This produces `publish\UniversalDownloader.exe` — a single file that runs
without a .NET install. The same build runs automatically on GitHub Actions
(`.github/workflows/build-windows.yml`): push the repo to GitHub and download
the exe from the workflow run's Artifacts.

For the signed MSIX installer, see [installer/README.md](installer/README.md).

## Configuration

- `ClientSettings` persists to `%AppData%\UniversalDownloader\settings.json`:
  `ApiBaseUrl` (default `https://api.universaldownloader.example`),
  `DestinationDirectory` (default `%USERPROFILE%\Downloads\UniversalDownloader`),
  `NamingTemplate`, `BatchLength`, `ConcurrencyLimit`, `TimezoneId`.
- The refresh token is DPAPI-encrypted in `%AppData%\UniversalDownloader\tokens.dat`
  (Windows-only; `ProtectedData` requires `net8.0-windows` or the
  `System.Security.Cryptography.ProtectedData` package — referenced here).
- SQLite cache lives at `%AppData%\UniversalDownloader\cache.db`.

## Key behaviors

- **State machine:** every job state change passes through `JobStateMachine`;
  illegal transitions throw `InvalidJobStateTransitionException`.
- **Network loss** moves active jobs to `WaitingForNetwork`, never `Failed`;
  they resume automatically on reconnect.
- **Files are never overwritten:** name collisions get ` (2)`, ` (3)`, … suffixes.
- **Account lockout:** expired/suspended/disabled subscriptions lock Sources,
  Jobs, and Settings client-side (the server enforces it too); a T-3 renewal
  banner appears when expiry is within 3 days.
- **No third-party social credentials** are stored anywhere (SDS §19).

## Known review risks (first Windows build)

These are the spots a human should double-check at first compile:

1. `App.xaml.cs` — `AuthenticatedHttpHandler` is constructed manually with an
   `ApiClient` instance built outside DI (to avoid a mid-registration
   `BuildServiceProvider`); verify no double-dispose of the shared `HttpClient`
   and that the refresh-on-401 path replays correctly.
2. XAML bindings — `ListView` command bindings use
   `RelativeSource AncestorType=ListView`; verify the visual tree resolves them
   (the rows' `DataContext` is the row view-model, so `DataContext.XCommand`
   must climb to the `ListView`).
3. `CommunityToolkit.Mvvm` source generators — `[ObservableProperty]` on
   `ObservableCollection<T>` fields and `partial void On<Prop>Changed` hooks;
   confirm generated member names match the XAML bindings.
4. `System.Windows.Forms.FolderBrowserDialog` in `SettingsViewModel` — the App
   project now sets `<UseWindowsForms>true</UseWindowsForms>`; verify it
   resolves under `net8.0-windows` with `UseWPF`.
5. `DpapiTokenStore` — `ProtectedData` only works on Windows; the
   Infrastructure project targets plain `net8.0`, so guard any non-Windows
   test runs.
6. Pause-during-download walks the validated chain
   DOWNLOADING → RETRY_WAIT → READY → PAUSED (there is no direct
   DOWNLOADING → PAUSED edge in SRS §8 v1.1); the server will see the
   intermediate states via the throttled/forced status reports.
