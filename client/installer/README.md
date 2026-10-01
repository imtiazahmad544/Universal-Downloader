# Universal Downloader — Windows installer

## Strategy (SDS §17)

The Universal Downloader client ships as a **signed MSIX package** (Windows 10
1809+ / Windows 11), published from the UniversalDownloader.App WPF project.

| Concern | Decision |
|---|---|
| Package format | MSIX — clean install/uninstall, automatic updates via `.appinstaller` |
| Distribution | Sideloaded from the customer's portal page, or Microsoft Store (phase 2) |
| Code signing | EV code-signing certificate; SmartScreen reputation accrues on the signed publisher |
| Updates | `.appinstaller` file pointing at the versioned MSIX feed; silent background updates |
| Per-user vs machine | Per-user install (no admin rights required) |

## Build steps (on a Windows machine with the .NET 8 SDK)

```powershell
# 1. Publish the WPF app as a single self-contained exe
dotnet publish ..\src\UniversalDownloader.App\UniversalDownloader.App.csproj `
  -c Release -r win-x64 --self-contained `
  -p:PublishSingleFile=true -o .\publish

# 2. Lay out the MSIX payload
New-Item -ItemType Directory -Force .\msix
Copy-Item .\publish\* .\msix\ -Recurse

# 3. Copy and edit Package.appxmanifest (sample in this folder):
#    - set Identity Name / Publisher to match your signing certificate
#    - bump Version for every release (updates require a higher version)

# 4. Pack the MSIX (Windows SDK: MakeAppx.exe)
& "C:\Program Files (x86)\Windows Kits\10\bin\10.0.22621.0\x64\MakeAppx.exe" pack `
  /d .\msix /p .\UniversalDownloader.msix

# 5. Sign it (Windows SDK: SignTool.exe)
& "C:\Program Files (x86)\Windows Kits\10\bin\10.0.22621.0\x64\SignTool.exe" sign `
  /fd SHA256 /a /f .\certs\publisher.pfx /p <password> .\UniversalDownloader.msix
```

## Update feed

Publish an `UniversalDownloader.appinstaller` next to the MSIX files:

```xml
<?xml version="1.0" encoding="utf-8"?>
<AppInstaller Uri="https://downloads.example.com/app/UniversalDownloader.appinstaller"
              Version="1.0.0.0">
  <MainPackage Name="Imtiaz.UniversalDownloader"
               Version="1.0.0.0"
               Publisher="CN=Universal Downloader"
               Uri="https://downloads.example.com/app/UniversalDownloader.msix"
               ProcessorArchitecture="x64"/>
  <UpdateSettings>
    <OnLaunch HoursBetweenUpdateChecks="24"/>
  </UpdateSettings>
</AppInstaller>
```

Customers install once from the `.appinstaller` link; subsequent launches check
for updates automatically.

## Notes

- The manifest's `Publisher` attribute **must exactly match** the signing
  certificate's subject, or installation fails.
- The app writes its SQLite cache and settings under `%AppData%` — no special
  MSIX capabilities are required.
- Keep the certificate's private key out of the repo; sign in CI with a secret.
