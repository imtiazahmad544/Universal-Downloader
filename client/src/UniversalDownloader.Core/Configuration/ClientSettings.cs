// Local client settings persisted under %AppData%.
// Mirrors SRS FR-07 (batch length), FR-10 (destination directory, naming template),
// and the v1.1 timezone requirement.

using System.Text.Json;
using UniversalDownloader.Core.Naming;

namespace UniversalDownloader.Core.Configuration;

/// <summary>Customer-configurable local settings (SRS FR-07, FR-10).</summary>
public sealed class ClientSettings
{
    public string DestinationDirectory { get; set; } = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
        "Downloads", "UniversalDownloader");

    public string NamingTemplate { get; set; } = FileNamingTemplate.DefaultTemplate;

    /// <summary>Videos processed per daily batch (SRS FR-07).</summary>
    public int BatchLength { get; set; } = 20;

    /// <summary>Max concurrent downloads (SRS FR-07).</summary>
    public int ConcurrencyLimit { get; set; } = 3;

    /// <summary>IANA timezone id used for batch activation (v1.1).</summary>
    public string TimezoneId { get; set; } = TimeZoneInfo.Local.Id;

    /// <summary>Base URL of the SaaS API.</summary>
    public string ApiBaseUrl { get; set; } = "https://api.universaldownloader.example";

    public static string DefaultPath => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
        "UniversalDownloader", "settings.json");

    public static ClientSettings Load(string? path = null)
    {
        path ??= DefaultPath;
        try
        {
            if (File.Exists(path))
            {
                var json = File.ReadAllText(path);
                var s = JsonSerializer.Deserialize<ClientSettings>(json);
                if (s is not null)
                    return s;
            }
        }
        catch (Exception)
        {
            // Corrupt settings file: fall back to defaults rather than crash startup.
        }
        return new ClientSettings();
    }

    public void Save(string? path = null)
    {
        path ??= DefaultPath;
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        var json = JsonSerializer.Serialize(this, new JsonSerializerOptions { WriteIndented = true });
        File.WriteAllText(path, json);
    }
}
