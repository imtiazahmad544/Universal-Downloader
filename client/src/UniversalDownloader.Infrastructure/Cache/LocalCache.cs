// Local SQLite cache — SDS section 4.1.
// Mirrors sources and jobs so the UI stays usable offline; the server remains
// the system of record (SDS section 22: never treat a local cache as authoritative).

using Microsoft.Data.Sqlite;
using UniversalDownloader.Core.Models;

namespace UniversalDownloader.Infrastructure.Cache;

/// <summary>SQLite mirror of server-side sources and jobs for offline UI continuity.</summary>
public sealed class LocalCache : IAsyncDisposable
{
    private readonly SqliteConnection _connection;
    private bool _disposed;

    private LocalCache(SqliteConnection connection)
    {
        _connection = connection;
    }

    public static async Task<LocalCache> OpenAsync(string? dbPath = null)
    {
        dbPath ??= Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
            "UniversalDownloader", "cache.db");
        Directory.CreateDirectory(Path.GetDirectoryName(dbPath)!);

        var connection = new SqliteConnection($"Data Source={dbPath}");
        await connection.OpenAsync().ConfigureAwait(false);
        var cache = new LocalCache(connection);
        await cache.EnsureSchemaAsync().ConfigureAwait(false);
        return cache;
    }

    private async Task EnsureSchemaAsync()
    {
        const string sql = """
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL,
                platform INTEGER NOT NULL,
                input_value TEXT NOT NULL,
                canonical_id TEXT NOT NULL,
                status INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL,
                media_item_id INTEGER NOT NULL,
                batch_id INTEGER NULL,
                state INTEGER NOT NULL,
                attempts INTEGER NOT NULL,
                provider TEXT NULL,
                progress REAL NOT NULL,
                bytes_downloaded INTEGER NOT NULL,
                total_bytes INTEGER NULL,
                title TEXT NOT NULL,
                platform INTEGER NOT NULL,
                source_name TEXT NOT NULL,
                media_url TEXT NOT NULL,
                checksum_sha256 TEXT NULL,
                error_message TEXT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_jobs_customer_state ON jobs(customer_id, state);
            CREATE INDEX IF NOT EXISTS idx_jobs_batch_state ON jobs(batch_id, state);
            """;
        using var cmd = _connection.CreateCommand();
        cmd.CommandText = sql;
        await cmd.ExecuteNonQueryAsync().ConfigureAwait(false);

        // v2.0 columns: added idempotently so existing cache.db files migrate.
        await EnsureColumnAsync("sources", "has_cookies", "INTEGER NOT NULL DEFAULT 0")
            .ConfigureAwait(false);
        await EnsureColumnAsync("jobs", "captcha_required", "INTEGER NOT NULL DEFAULT 0")
            .ConfigureAwait(false);
        await EnsureColumnAsync("jobs", "source_id", "INTEGER NOT NULL DEFAULT 0")
            .ConfigureAwait(false);
    }

    private async Task EnsureColumnAsync(string table, string column, string definition)
    {
        // Table/column names are fixed literals from code (never user input).
        using var probe = _connection.CreateCommand();
        probe.CommandText = $"PRAGMA table_info({table});";
        using var reader = await probe.ExecuteReaderAsync().ConfigureAwait(false);
        while (await reader.ReadAsync().ConfigureAwait(false))
        {
            if (string.Equals(reader.GetString(1), column, StringComparison.OrdinalIgnoreCase))
                return;
        }
        using var alter = _connection.CreateCommand();
        alter.CommandText = $"ALTER TABLE {table} ADD COLUMN {column} {definition};";
        await alter.ExecuteNonQueryAsync().ConfigureAwait(false);
    }

    public async Task UpsertSourcesAsync(IEnumerable<Source> sources, CancellationToken ct = default)
    {
        const string sql = """
            INSERT INTO sources (id, customer_id, platform, input_value, canonical_id, status, has_cookies, updated_at)
            VALUES ($id, $customer_id, $platform, $input_value, $canonical_id, $status, $has_cookies, $updated_at)
            ON CONFLICT(id) DO UPDATE SET
                platform = excluded.platform,
                input_value = excluded.input_value,
                canonical_id = excluded.canonical_id,
                status = excluded.status,
                has_cookies = excluded.has_cookies,
                updated_at = excluded.updated_at;
            """;
        using var tx = (SqliteTransaction)await _connection.BeginTransactionAsync(ct).ConfigureAwait(false);
        foreach (var s in sources)
        {
            using var cmd = _connection.CreateCommand();
            cmd.Transaction = tx;
            cmd.CommandText = sql;
            cmd.Parameters.AddWithValue("$id", s.Id);
            cmd.Parameters.AddWithValue("$customer_id", s.CustomerId);
            cmd.Parameters.AddWithValue("$platform", (int)s.Platform);
            cmd.Parameters.AddWithValue("$input_value", s.InputValue);
            cmd.Parameters.AddWithValue("$canonical_id", s.CanonicalId);
            cmd.Parameters.AddWithValue("$status", (int)s.Status);
            cmd.Parameters.AddWithValue("$has_cookies", s.HasCookies ? 1 : 0);
            cmd.Parameters.AddWithValue("$updated_at", s.UpdatedAt.ToString("O"));
            await cmd.ExecuteNonQueryAsync(ct).ConfigureAwait(false);
        }
        await tx.CommitAsync(ct).ConfigureAwait(false);
    }

    public async Task<IReadOnlyList<Source>> GetSourcesAsync(CancellationToken ct = default)
    {
        const string sql = "SELECT id, customer_id, platform, input_value, canonical_id, status, has_cookies, updated_at FROM sources;";
        var list = new List<Source>();
        using var cmd = _connection.CreateCommand();
        cmd.CommandText = sql;
        using var reader = await cmd.ExecuteReaderAsync(ct).ConfigureAwait(false);
        while (await reader.ReadAsync(ct).ConfigureAwait(false))
        {
            list.Add(new Source
            {
                Id = reader.GetInt32(0),
                CustomerId = reader.GetInt32(1),
                Platform = (Platform)reader.GetInt32(2),
                InputValue = reader.GetString(3),
                CanonicalId = reader.GetString(4),
                Status = (SourceStatus)reader.GetInt32(5),
                HasCookies = reader.GetInt32(6) != 0,
                UpdatedAt = DateTimeOffset.Parse(reader.GetString(7)),
            });
        }
        return list;
    }

    public async Task UpsertJobsAsync(IEnumerable<DownloadJob> jobs, CancellationToken ct = default)
    {
        const string sql = """
            INSERT INTO jobs (id, customer_id, media_item_id, batch_id, state, attempts, provider,
                              progress, bytes_downloaded, total_bytes, title, platform, source_name,
                              media_url, checksum_sha256, error_message, captcha_required, source_id, updated_at)
            VALUES ($id, $customer_id, $media_item_id, $batch_id, $state, $attempts, $provider,
                    $progress, $bytes_downloaded, $total_bytes, $title, $platform, $source_name,
                    $media_url, $checksum_sha256, $error_message, $captcha_required, $source_id, $updated_at)
            ON CONFLICT(id) DO UPDATE SET
                batch_id = excluded.batch_id,
                state = excluded.state,
                attempts = excluded.attempts,
                provider = excluded.provider,
                progress = excluded.progress,
                bytes_downloaded = excluded.bytes_downloaded,
                total_bytes = excluded.total_bytes,
                title = excluded.title,
                platform = excluded.platform,
                source_name = excluded.source_name,
                media_url = excluded.media_url,
                checksum_sha256 = excluded.checksum_sha256,
                error_message = excluded.error_message,
                captcha_required = excluded.captcha_required,
                source_id = excluded.source_id,
                updated_at = excluded.updated_at;
            """;
        using var tx = (SqliteTransaction)await _connection.BeginTransactionAsync(ct).ConfigureAwait(false);
        foreach (var j in jobs)
        {
            using var cmd = _connection.CreateCommand();
            cmd.Transaction = tx;
            cmd.CommandText = sql;
            AddJobParameters(cmd, j);
            await cmd.ExecuteNonQueryAsync(ct).ConfigureAwait(false);
        }
        await tx.CommitAsync(ct).ConfigureAwait(false);
    }

    public Task UpdateJobAsync(DownloadJob job, CancellationToken ct = default) =>
        UpsertJobsAsync(new[] { job }, ct);

    public async Task<IReadOnlyList<DownloadJob>> GetJobsAsync(CancellationToken ct = default)
    {
        const string sql = """
            SELECT id, customer_id, media_item_id, batch_id, state, attempts, provider,
                   progress, bytes_downloaded, total_bytes, title, platform, source_name,
                   media_url, checksum_sha256, error_message, captcha_required, source_id, updated_at
            FROM jobs;
            """;
        var list = new List<DownloadJob>();
        using var cmd = _connection.CreateCommand();
        cmd.CommandText = sql;
        using var reader = await cmd.ExecuteReaderAsync(ct).ConfigureAwait(false);
        while (await reader.ReadAsync(ct).ConfigureAwait(false))
            list.Add(ReadJob(reader));
        return list;
    }

    public async Task<DownloadJob?> GetJobAsync(int id, CancellationToken ct = default)
    {
        const string sql = """
            SELECT id, customer_id, media_item_id, batch_id, state, attempts, provider,
                   progress, bytes_downloaded, total_bytes, title, platform, source_name,
                   media_url, checksum_sha256, error_message, captcha_required, source_id, updated_at
            FROM jobs WHERE id = $id;
            """;
        using var cmd = _connection.CreateCommand();
        cmd.CommandText = sql;
        cmd.Parameters.AddWithValue("$id", id);
        using var reader = await cmd.ExecuteReaderAsync(ct).ConfigureAwait(false);
        return await reader.ReadAsync(ct).ConfigureAwait(false) ? ReadJob(reader) : null;
    }

    public async Task ClearCustomerDataAsync(CancellationToken ct = default)
    {
        using var cmd = _connection.CreateCommand();
        cmd.CommandText = "DELETE FROM jobs; DELETE FROM sources;";
        await cmd.ExecuteNonQueryAsync(ct).ConfigureAwait(false);
    }

    private static void AddJobParameters(SqliteCommand cmd, DownloadJob j)
    {
        cmd.Parameters.AddWithValue("$id", j.Id);
        cmd.Parameters.AddWithValue("$customer_id", j.CustomerId);
        cmd.Parameters.AddWithValue("$media_item_id", j.MediaItemId);
        cmd.Parameters.AddWithValue("$batch_id", (object?)j.BatchId ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$state", (int)j.State);
        cmd.Parameters.AddWithValue("$attempts", j.Attempts);
        cmd.Parameters.AddWithValue("$provider", (object?)j.Provider ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$progress", j.Progress);
        cmd.Parameters.AddWithValue("$bytes_downloaded", j.BytesDownloaded);
        cmd.Parameters.AddWithValue("$total_bytes", (object?)j.TotalBytes ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$title", j.Title);
        cmd.Parameters.AddWithValue("$platform", (int)j.Platform);
        cmd.Parameters.AddWithValue("$source_name", j.SourceName);
        cmd.Parameters.AddWithValue("$media_url", j.MediaUrl);
        cmd.Parameters.AddWithValue("$checksum_sha256", (object?)j.ChecksumSha256 ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$error_message", (object?)j.ErrorMessage ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$captcha_required", j.CaptchaRequired ? 1 : 0);
        cmd.Parameters.AddWithValue("$source_id", j.SourceId);
        cmd.Parameters.AddWithValue("$updated_at", j.UpdatedAt.ToString("O"));
    }

    private static DownloadJob ReadJob(SqliteDataReader r)
    {
        string? Str(int i) => r.IsDBNull(i) ? null : r.GetString(i);
        long? Int64(int i) => r.IsDBNull(i) ? null : r.GetInt64(i);
        return new DownloadJob
        {
            Id = r.GetInt32(0),
            CustomerId = r.GetInt32(1),
            MediaItemId = r.GetInt32(2),
            BatchId = r.IsDBNull(3) ? null : r.GetInt32(3),
            State = (JobState)r.GetInt32(4),
            Attempts = r.GetInt32(5),
            Provider = Str(6),
            Progress = r.GetDouble(7),
            BytesDownloaded = r.GetInt64(8),
            TotalBytes = Int64(9),
            Title = r.GetString(10),
            Platform = (Platform)r.GetInt32(11),
            SourceName = r.GetString(12),
            MediaUrl = r.GetString(13),
            ChecksumSha256 = Str(14),
            ErrorMessage = Str(15),
            CaptchaRequired = r.GetInt32(16) != 0,
            SourceId = r.GetInt32(17),
            UpdatedAt = DateTimeOffset.Parse(r.GetString(18)),
        };
    }

    public async ValueTask DisposeAsync()
    {
        if (_disposed)
            return;
        _disposed = true;
        await _connection.DisposeAsync().ConfigureAwait(false);
    }
}
