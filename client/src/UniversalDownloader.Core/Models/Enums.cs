// Job state machine for the Universal Downloader client.
// Mirrors SRS section 8 (v1.1): 14 states with validated transitions.

namespace UniversalDownloader.Core.Models;

/// <summary>
/// Lifecycle states of a download job. Must stay in sync with the SRS state
/// machine table; see <see cref="StateMachine.JobStateMachine"/>.
/// </summary>
public enum JobState
{
    Discovered,
    Queued,
    Batched,
    Ready,
    Downloading,
    RetryWait,
    Completed,
    Failed,
    Paused,
    Cancelled,
    WaitingForNetwork,
    Invalid,
    Duplicate,
    Requeued,
}

/// <summary>Supported content platforms.</summary>
public enum Platform
{
    TikTok,
    Instagram,
    YouTube,
}

/// <summary>
/// Subscription states. Date-derived states follow SDS section 7:
/// Active (expires &gt; now + 3 days), ExpiringSoon (now &lt; expires &lt;= now + 3 days),
/// Expired (expires &lt;= now). Suspended/Disabled are explicit administrative states
/// that override the date-derived value.
/// </summary>
public enum SubscriptionStatus
{
    Active,
    ExpiringSoon,
    Expired,
    Suspended,
    Disabled,
}

/// <summary>Customer-controlled source lifecycle.</summary>
public enum SourceStatus
{
    Active,
    Paused,
}

/// <summary>Daily batch lifecycle.</summary>
public enum BatchStatus
{
    Pending,
    Active,
    Completed,
}
