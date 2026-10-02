using System.Windows.Controls;
using UniversalDownloader.App.ViewModels;

namespace UniversalDownloader.App.Views;

public partial class SettingsView : System.Windows.Controls.UserControl
{
    public SettingsViewModel ViewModel => (SettingsViewModel)DataContext;

    public SettingsView(SettingsViewModel viewModel)
    {
        InitializeComponent();
        DataContext = viewModel;
    }
}
