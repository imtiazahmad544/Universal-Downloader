// Application composition root. Builds the DI container, wires the SDS
// section 14 startup flow (login -> /me -> session -> sync -> worker),
// and disposes infrastructure on exit.

using System.IO;
using System.Windows;
using Microsoft.Extensions.DependencyInjection;
using UniversalDownloader.App.Services;
using UniversalDownloader.App.ViewModels;
using UniversalDownloader.App.Views;
using UniversalDownloader.Core.Configuration;
using UniversalDownloader.Infrastructure.Api;
using UniversalDownloader.Infrastructure.Auth;
using UniversalDownloader.Infrastructure.Cache;
using UniversalDownloader.Worker.Download;
using UniversalDownloader.Worker.Networking;

namespace UniversalDownloader.App;

public partial class App : System.Windows.Application
{
    private ServiceProvider? _services;
    private DownloadWorker? _worker;
    private NetworkMonitor? _network;
    private LocalCache? _cache;

    protected override async void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);

        var settings = ClientSettings.Load();

        var services = new ServiceCollection();
        services.AddSingleton(settings);

        // Auth: raw HttpClient for login/refresh (no auth handler -> no cycle).
        var tokenStore = new DpapiTokenStore();
        services.AddSingleton<ITokenStore>(tokenStore);
        var authHttp = new HttpClient
        {
            BaseAddress = new Uri(settings.ApiBaseUrl),
            Timeout = TimeSpan.FromSeconds(30),
        };
        var authService = new AuthService(authHttp, tokenStore);
        services.AddSingleton(authService);

        // Authenticated API client with refresh-on-401.
        var authedHandler = new AuthenticatedHttpHandler(authService, tokenStore)
        {
            InnerHandler = new HttpClientHandler(),
        };
        var apiHttp = new HttpClient(authedHandler)
        {
            BaseAddress = new Uri(settings.ApiBaseUrl),
            Timeout = TimeSpan.FromSeconds(60),
        };
        var apiClient = new ApiClient(apiHttp);
        services.AddSingleton<IApiClient>(apiClient);

        _cache = await LocalCache.OpenAsync().ConfigureAwait(true);
        services.AddSingleton(_cache);

        _network = new NetworkMonitor(settings.ApiBaseUrl);
        services.AddSingleton(_network);

        var workerOptions = new WorkerOptions
        {
            ConcurrencyLimit = settings.ConcurrencyLimit,
            DestinationDirectory = settings.DestinationDirectory,
            NamingTemplate = settings.NamingTemplate,
            GlobalCookiesFilePath = settings.GlobalCookiesFilePath,
        };
        services.AddSingleton(workerOptions);
        _worker = new DownloadWorker(apiClient, _cache, _network, workerOptions, new TraceWorkerLog());
        services.AddSingleton(_worker);

        services.AddSingleton<SessionState>();
        services.AddSingleton<SessionCoordinator>();

        // ViewModels (singletons: views hold their state while navigating).
        services.AddSingleton<MainViewModel>();
        services.AddSingleton<LoginViewModel>();
        services.AddSingleton<DashboardViewModel>();
        services.AddSingleton<SourcesViewModel>();
        services.AddSingleton<JobsViewModel>();
        services.AddSingleton<SettingsViewModel>();

        // Views.
        services.AddSingleton<LoginView>();
        services.AddSingleton<DashboardView>();
        services.AddSingleton<SourcesView>();
        services.AddSingleton<JobsView>();
        services.AddSingleton<SettingsView>();
        services.AddSingleton<MainWindow>();

        _services = services.BuildServiceProvider();

        // Wire the login-success callback after resolution (avoids a VM cycle).
        var main = _services.GetRequiredService<MainViewModel>();
        var login = _services.GetRequiredService<LoginViewModel>();
        login.LoginSucceeded += async (customer, subscription) =>
            await main.HandleLoginSucceededAsync(customer, subscription).ConfigureAwait(true);

        var window = _services.GetRequiredService<MainWindow>();
        MainWindow = window;
        window.Show();
    }

    protected override async void OnExit(ExitEventArgs e)
    {
        try
        {
            if (_worker is not null)
                await _worker.DisposeAsync().ConfigureAwait(true);
            _network?.Dispose();
            if (_cache is not null)
                await _cache.DisposeAsync().ConfigureAwait(true);
            _services?.Dispose();
        }
        finally
        {
            base.OnExit(e);
        }
    }
}
