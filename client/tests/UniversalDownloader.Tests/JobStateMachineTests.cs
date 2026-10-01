// Tests for the validated job state machine — SRS section 8 (v1.1).

using UniversalDownloader.Core.Models;
using UniversalDownloader.Core.StateMachine;

namespace UniversalDownloader.Tests;

public sealed class JobStateMachineTests
{
    [Fact]
    public void States_contains_all_fourteen_states()
    {
        Assert.Equal(14, JobStateMachine.States.Count);
    }

    [Theory]
    [InlineData(JobState.Discovered, JobState.Queued)]
    [InlineData(JobState.Queued, JobState.Batched)]
    [InlineData(JobState.Batched, JobState.Ready)]
    [InlineData(JobState.Ready, JobState.Downloading)]
    [InlineData(JobState.Downloading, JobState.Completed)]
    [InlineData(JobState.Downloading, JobState.RetryWait)]
    [InlineData(JobState.Downloading, JobState.Failed)]
    [InlineData(JobState.Downloading, JobState.WaitingForNetwork)]
    [InlineData(JobState.RetryWait, JobState.Ready)]
    [InlineData(JobState.RetryWait, JobState.Failed)]
    [InlineData(JobState.Ready, JobState.Paused)]
    [InlineData(JobState.Paused, JobState.Ready)]
    [InlineData(JobState.Paused, JobState.Cancelled)]
    [InlineData(JobState.Failed, JobState.Ready)]
    [InlineData(JobState.Completed, JobState.Requeued)]
    [InlineData(JobState.Requeued, JobState.Queued)]
    [InlineData(JobState.Cancelled, JobState.Requeued)]
    [InlineData(JobState.WaitingForNetwork, JobState.Ready)]
    [InlineData(JobState.Discovered, JobState.Invalid)]
    [InlineData(JobState.Discovered, JobState.Duplicate)]
    public void CanTransition_allows_valid_transitions(JobState from, JobState to)
    {
        Assert.True(JobStateMachine.CanTransition(from, to));
    }

    [Theory]
    [InlineData(JobState.Completed, JobState.Downloading)]
    [InlineData(JobState.Failed, JobState.Completed)]
    [InlineData(JobState.Invalid, JobState.Queued)]
    [InlineData(JobState.Duplicate, JobState.Ready)]
    [InlineData(JobState.Queued, JobState.Completed)]
    [InlineData(JobState.Ready, JobState.Completed)]
    [InlineData(JobState.Paused, JobState.Downloading)]
    public void CanTransition_rejects_invalid_transitions(JobState from, JobState to)
    {
        Assert.False(JobStateMachine.CanTransition(from, to));
    }

    [Fact]
    public void Validate_throws_for_invalid_transition()
    {
        var ex = Assert.Throws<InvalidJobStateTransitionException>(
            () => JobStateMachine.Validate(JobState.Completed, JobState.Downloading));
        Assert.Equal(JobState.Completed, ex.From);
        Assert.Equal(JobState.Downloading, ex.To);
    }

    [Theory]
    [InlineData(JobState.Invalid, true)]
    [InlineData(JobState.Duplicate, true)]
    [InlineData(JobState.Completed, false)]
    [InlineData(JobState.Ready, false)]
    public void IsTerminal_reports_only_terminal_states(JobState state, bool expected)
    {
        Assert.Equal(expected, JobStateMachine.IsTerminal(state));
    }

    [Fact]
    public void Network_loss_never_transitions_to_failed_directly()
    {
        // Per SDS: connectivity loss moves a job to WaitingForNetwork, never Failed.
        Assert.True(JobStateMachine.CanTransition(JobState.Downloading, JobState.WaitingForNetwork));
        Assert.True(JobStateMachine.CanTransition(JobState.Ready, JobState.WaitingForNetwork));
    }
}
