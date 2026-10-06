using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using UniversalDownloader.App.Services;
using UniversalDownloader.App.Views;
using UniversalDownloader.Core.Models;

namespace UniversalDownloader.App.ViewModels;

/// <summary>Shell view-model: navigation, login gating, and account lockout.</summary>
public sealed partial class MainViewModel : ObservableObject
{
    private readonly SessionCoordinator _coordinator;
    private readonly SessionState _session;
    private readonly LoginView _loginView;
    private readonly DashboardView _dashboardView;
    private readonly SourcesView _sourcesView;
    private readonly JobsView _jobsView;
    private readonly SettingsView _settingsView;

    [ObservableProperty]
    private object _currentView = null!;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(CanUseProtectedFeatures))]
    private bool _isLoggedIn;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(CanUseProtectedFeatures))]
    private bool _isAccountLocked;

    [ObservableProperty]
    private string _lockoutMessage = string.Empty;

    [ObservableProperty]
    private string _accountSummary = string.Empty;

    public bool CanUseProtectedFeatures => IsLoggedIn && !IsAccountLocked;

    public MainViewModel(
        SessionCoordinator coordinator,
        SessionState session,
        LoginView loginView,
        DashboardView dashboardView,
        SourcesView sourcesView,
        JobsView jobsView,
        SettingsView settingsView)
    {
        _coordinator = coordinator;
        _session = session;
        _loginView = loginView;
        _dashboardView = dashboardView;
        _sourcesView = sourcesView;
        _jobsView = jobsView;
        _settingsView = settingsView;
        _currentView = _loginView;
    }

    /// <summary>Called after a successful login (SDS section 14, steps 2-5).</summary>
    public async Task HandleLoginSucceededAsync(Customer customer, Subscription subscription)
    {
        _session.Set(customer, subscription);
        var now = DateTimeOffset.UtcNow;
        var status = subscription.DeriveStatus(now);

        IsAccountLocked = !subscription.AllowsProtectedOperations(now);
        LockoutMessage = status switch
        {
            SubscriptionStatus.Expired =>
                $"Your subscription expired on {subscription.ExpiresAt:yyyy-MM-dd}. Contact your administrator to renew.",
            SubscriptionStatus.Suspended =>
                "Your account is suspended. Contact your administrator.",
            SubscriptionStatus.Disabled =>
                "Your account is disabled. Contact your administrator.",
            _ => string.Empty,
        };
        AccountSummary = $"Customer {customer.CustomerCode} · {customer.Name}";

        await _coordinator.StartSessionAsync().ConfigureAwait(true);
        IsLoggedIn = true;
        ShowDashboard();
    }

    [RelayCommand]
    private void ShowDashboard()
    {
        CurrentView = _dashboardView;
        _dashboardView.ViewModel.RefreshCommand.Execute(null);
    }

    [RelayCommand]
    private void ShowSources()
    {
        CurrentView = _sourcesView;
        _sourcesView.ViewModel.RefreshCommand.Execute(null);
    }

    [RelayCommand]
    private void ShowJobs()
    {
        CurrentView = _jobsView;
        _jobsView.ViewModel.RefreshCommand.Execute(null);
    }

    [RelayCommand]
    private void ShowSettings()
    {
        CurrentView = _settingsView;
        // Best-effort presence flag refresh; never blocks navigation.
        _ = _settingsView.ViewModel.RefreshServerCookiesStatusAsync();
    }

    [RelayCommand]
    private async Task LogoutAsync()
    {
        await _coordinator.EndSessionAsync().ConfigureAwait(true);
        IsLoggedIn = false;
        IsAccountLocked = false;
        LockoutMessage = string.Empty;
        AccountSummary = string.Empty;
        CurrentView = _loginView;
    }
}
