// Validated job state machine — SRS section 8 (v1.1).
// Every job state change on the client must pass through this class so the
// client can never produce a transition the server would reject.

using UniversalDownloader.Core.Models;

namespace UniversalDownloader.Core.StateMachine;

/// <summary>Raised when a job state transition is not in the SRS state machine.</summary>
public sealed class InvalidJobStateTransitionException : InvalidOperationException
{
    public JobState From { get; }
    public JobState To { get; }

    public InvalidJobStateTransitionException(JobState from, JobState to)
        : base($"Invalid job state transition: {from} -> {to}.")
    {
        From = from;
        To = to;
    }
}

/// <summary>
/// The 14-state job lifecycle from SRS section 8 (v1.1), with validated transitions.
/// Terminal states (Invalid, Duplicate) have no outgoing transitions.
/// </summary>
public static class JobStateMachine
{
    private static readonly IReadOnlyDictionary<JobState, IReadOnlySet<JobState>> _transitions =
        new Dictionary<JobState, IReadOnlySet<JobState>>
        {
            [JobState.Discovered] = new HashSet<JobState> { JobState.Queued, JobState.Invalid, JobState.Duplicate },
            [JobState.Queued] = new HashSet<JobState> { JobState.Batched, JobState.Paused, JobState.Cancelled },
            [JobState.Batched] = new HashSet<JobState> { JobState.Ready, JobState.Paused },
            [JobState.Ready] = new HashSet<JobState> { JobState.Downloading, JobState.Paused, JobState.WaitingForNetwork },
            [JobState.Downloading] = new HashSet<JobState> { JobState.Completed, JobState.RetryWait, JobState.Failed, JobState.WaitingForNetwork },
            [JobState.RetryWait] = new HashSet<JobState> { JobState.Ready, JobState.Failed },
            [JobState.Completed] = new HashSet<JobState> { JobState.Requeued },
            [JobState.Failed] = new HashSet<JobState> { JobState.Ready, JobState.Requeued },
            [JobState.Paused] = new HashSet<JobState> { JobState.Queued, JobState.Ready, JobState.Cancelled },
            [JobState.Cancelled] = new HashSet<JobState> { JobState.Requeued },
            [JobState.WaitingForNetwork] = new HashSet<JobState> { JobState.Ready, JobState.Paused, JobState.Cancelled },
            [JobState.Invalid] = new HashSet<JobState>(),
            [JobState.Duplicate] = new HashSet<JobState>(),
            [JobState.Requeued] = new HashSet<JobState> { JobState.Queued },
        };

    /// <summary>All states in the machine.</summary>
    public static IReadOnlyCollection<JobState> States =>
        (IReadOnlyCollection<JobState>)_transitions.Keys;

    /// <summary>True when <paramref name="to"/> is a legal successor of <paramref name="from"/>.</summary>
    public static bool CanTransition(JobState from, JobState to) =>
        _transitions.TryGetValue(from, out var next) && next.Contains(to);

    /// <summary>Throws <see cref="InvalidJobStateTransitionException"/> for illegal transitions.</summary>
    public static void Validate(JobState from, JobState to)
    {
        if (!CanTransition(from, to))
            throw new InvalidJobStateTransitionException(from, to);
    }

    /// <summary>True when the state has no outgoing transitions.</summary>
    public static bool IsTerminal(JobState state) =>
        _transitions.TryGetValue(state, out var next) && next.Count == 0;
}
