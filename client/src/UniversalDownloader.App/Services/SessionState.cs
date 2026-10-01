// Holds the authenticated customer's identity and subscription for the session.

using UniversalDownloader.Core.Models;

namespace UniversalDownloader.App.Services;

/// <summary>Session-scoped customer + subscription state (SDS section 14).</summary>
public sealed class SessionState
{
    public Customer? Customer { get; private set; }
    public Subscription? Subscription { get; private set; }

    public bool IsAuthenticated => Customer is not null;

    public event EventHandler? Changed;

    public void Set(Customer customer, Subscription subscription)
    {
        Customer = customer ?? throw new ArgumentNullException(nameof(customer));
        Subscription = subscription ?? throw new ArgumentNullException(nameof(subscription));
        Changed?.Invoke(this, EventArgs.Empty);
    }

    public void Clear()
    {
        Customer = null;
        Subscription = null;
        Changed?.Invoke(this, EventArgs.Empty);
    }
}
