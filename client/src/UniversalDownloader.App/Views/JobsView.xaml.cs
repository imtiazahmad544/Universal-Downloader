using System.Windows.Controls;
using UniversalDownloader.App.ViewModels;

namespace UniversalDownloader.App.Views;

public partial class JobsView : System.Windows.Controls.UserControl
{
    public JobsViewModel ViewModel => (JobsViewModel)DataContext;

    public JobsView(JobsViewModel viewModel)
    {
        InitializeComponent();
        DataContext = viewModel;
    }
}
