using System.Windows.Controls;
using UniversalDownloader.App.ViewModels;

namespace UniversalDownloader.App.Views;

public partial class DashboardView : UserControl
{
    public DashboardViewModel ViewModel => (DashboardViewModel)DataContext;

    public DashboardView(DashboardViewModel viewModel)
    {
        InitializeComponent();
        DataContext = viewModel;
    }
}
