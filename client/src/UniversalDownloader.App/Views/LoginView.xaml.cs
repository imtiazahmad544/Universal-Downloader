using System.Windows;
using System.Windows.Controls;
using UniversalDownloader.App.ViewModels;

namespace UniversalDownloader.App.Views;

public partial class LoginView : System.Windows.Controls.UserControl
{
    public LoginViewModel ViewModel => (LoginViewModel)DataContext;

    public LoginView(LoginViewModel viewModel)
    {
        InitializeComponent();
        DataContext = viewModel;
    }

    private async void LoginButton_Click(object sender, RoutedEventArgs e)
    {
        string password = PasswordBox.Password;
        try
        {
            await ViewModel.LoginAsync(password).ConfigureAwait(true);
        }
        finally
        {
            PasswordBox.Clear();
            password = string.Empty;
        }
    }
}
