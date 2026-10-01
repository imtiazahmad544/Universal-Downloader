using CommunityToolkit.Mvvm.ComponentModel;
using UniversalDownloader.Core.Models;
using UniversalDownloader.Infrastructure.Api;
using UniversalDownloader.Infrastructure.Auth;

namespace UniversalDownloader.App.ViewModels;

/// <summary>Login form view-model (SDS section 14, step 1).</summary>
public sealed partial class LoginViewModel : ObservableObject
{
    private readonly AuthService _auth;
    private readonly IApiClient _api;

    [ObservableProperty]
    private string _customerCode = string.Empty;

    [ObservableProperty]
    private string _errorMessage = string.Empty;

    [ObservableProperty]
    private bool _hasError;

    [ObservableProperty]
    private bool _isBusy;

    /// <summary>Raised with the authenticated customer + subscription.</summary>
    public event Func<Customer, Subscription, Task>? LoginSucceeded;

    public LoginViewModel(AuthService auth, IApiClient api)
    {
        _auth = auth;
        _api = api;
    }

    /// <summary>
    /// Authenticates with the admin-assigned customer ID + password, then loads
    /// /me. The password is passed from the view's PasswordBox (never bound).
    /// </summary>
    public async Task LoginAsync(string password)
    {
        HasError = false;
        ErrorMessage = string.Empty;

        if (string.IsNullOrWhiteSpace(CustomerCode) || string.IsNullOrEmpty(password))
        {
            HasError = true;
            ErrorMessage = "Enter your customer ID and password.";
            return;
        }

        IsBusy = true;
        try
        {
            await _auth.LoginAsync(CustomerCode.Trim(), password).ConfigureAwait(true);
            var me = await _api.GetMeAsync().ConfigureAwait(true);
            var handler = LoginSucceeded;
            if (handler is not null)
            {
                await handler(
                    DtoMapper.ToCustomer(me.Customer),
                    DtoMapper.ToSubscription(me.Subscription, me.Customer.Id)).ConfigureAwait(true);
            }
        }
        catch (ApiException ex)
        {
            HasError = true;
            ErrorMessage = ex.Message;
        }
        catch (Exception ex)
        {
            HasError = true;
            ErrorMessage = $"Could not reach the server: {ex.Message}";
        }
        finally
        {
            IsBusy = false;
        }
    }
}
