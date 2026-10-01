using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using UniversalDownloader.Core.Configuration;
using UniversalDownloader.Worker.Download;

namespace UniversalDownloader.App.ViewModels;

/// <summary>Local settings (SRS FR-07, FR-10): destination directory, naming
/// template, batch length, concurrency, timezone, API base URL.</summary>
public sealed partial class SettingsViewModel : ObservableObject
{
    private readonly ClientSettings _settings;
    private readonly WorkerOptions _workerOptions;

    [ObservableProperty] private string _destinationDirectory = string.Empty;
    [ObservableProperty] private string _namingTemplate = string.Empty;
    [ObservableProperty] private int _batchLength;
    [ObservableProperty] private int _concurrencyLimit;
    [ObservableProperty] private string _timezoneId = string.Empty;
    [ObservableProperty] private string _apiBaseUrl = string.Empty;
    [ObservableProperty] private string _saveMessage = string.Empty;
    [ObservableProperty] private bool _saveSucceeded;

    public bool HasSaveMessage => !string.IsNullOrWhiteSpace(SaveMessage);

    partial void OnSaveMessageChanged(string value) => OnPropertyChanged(nameof(HasSaveMessage));

    public SettingsViewModel(ClientSettings settings, WorkerOptions workerOptions)
    {
        _settings = settings;
        _workerOptions = workerOptions;
        LoadFromSettings();
    }

    [RelayCommand]
    private void Browse()
    {
        using var dialog = new System.Windows.Forms.FolderBrowserDialog
        {
            Description = "Select the download destination directory",
            SelectedPath = DestinationDirectory,
            ShowNewFolderButton = true,
        };
        if (dialog.ShowDialog() == System.Windows.Forms.DialogResult.OK)
            DestinationDirectory = dialog.SelectedPath;
    }

    [RelayCommand]
    private void Reset() => LoadFromSettings();

    [RelayCommand]
    private void Save()
    {
        SaveSucceeded = false;

        if (string.IsNullOrWhiteSpace(DestinationDirectory))
        {
            SaveMessage = "Destination directory is required.";
            return;
        }
        if (BatchLength is < 1 or > 1000)
        {
            SaveMessage = "Batch length must be between 1 and 1000.";
            return;
        }
        if (ConcurrencyLimit is < 1 or > 8)
        {
            SaveMessage = "Concurrency limit must be between 1 and 8.";
            return;
        }
        if (string.IsNullOrWhiteSpace(NamingTemplate) || !NamingTemplate.Contains("{ext}", StringComparison.Ordinal))
        {
            SaveMessage = "Naming template must contain the {ext} token.";
            return;
        }

        var tz = TimeZoneHelper.Find(TimezoneId);
        if (!string.IsNullOrWhiteSpace(TimezoneId) &&
            !string.Equals(tz.Id, TimezoneId, StringComparison.OrdinalIgnoreCase))
        {
            SaveMessage = $"Timezone '{TimezoneId}' not recognized; using {tz.Id} instead.";
            TimezoneId = tz.Id;
        }
        else
        {
            SaveMessage = string.Empty;
        }

        _settings.DestinationDirectory = DestinationDirectory.Trim();
        _settings.NamingTemplate = NamingTemplate.Trim();
        _settings.BatchLength = BatchLength;
        _settings.ConcurrencyLimit = ConcurrencyLimit;
        _settings.TimezoneId = TimezoneId.Trim();
        _settings.ApiBaseUrl = ApiBaseUrl.Trim().TrimEnd('/');
        _settings.Save();

        // Apply live where possible; concurrency and API URL need a restart.
        _workerOptions.DestinationDirectory = _settings.DestinationDirectory;
        _workerOptions.NamingTemplate = _settings.NamingTemplate;

        SaveSucceeded = true;
        if (string.IsNullOrEmpty(SaveMessage))
            SaveMessage = "Settings saved. Concurrency and API URL changes apply after restart.";
    }

    private void LoadFromSettings()
    {
        DestinationDirectory = _settings.DestinationDirectory;
        NamingTemplate = _settings.NamingTemplate;
        BatchLength = _settings.BatchLength;
        ConcurrencyLimit = _settings.ConcurrencyLimit;
        TimezoneId = _settings.TimezoneId;
        ApiBaseUrl = _settings.ApiBaseUrl;
        SaveMessage = string.Empty;
        SaveSucceeded = false;
    }
}
