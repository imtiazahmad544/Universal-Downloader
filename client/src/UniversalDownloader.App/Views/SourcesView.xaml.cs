using System.Windows.Controls;
using UniversalDownloader.App.ViewModels;

namespace UniversalDownloader.App.Views;

public partial class SourcesView : UserControl
{
    public SourcesViewModel ViewModel => (SourcesViewModel)DataContext;

    public SourcesView(SourcesViewModel viewModel)
    {
        InitializeComponent();
        DataContext = viewModel;
    }
}
