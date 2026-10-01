using System.Windows;
using UniversalDownloader.App.ViewModels;

namespace UniversalDownloader.App;

public partial class MainWindow : Window
{
    public MainWindow(MainViewModel viewModel)
    {
        InitializeComponent();
        DataContext = viewModel;
    }
}
