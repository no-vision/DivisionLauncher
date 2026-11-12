namespace Division.Core.Observations;

/// <summary>Developer observations, not gameplay messages. Payload offsets are zero-based,
/// end-exclusive within the plaintext inner message: varuint ID followed by game contents.
/// TLS, LZ4, channel wrapping and frame-length prefixes are deliberately outside this view.</summary>
public sealed record MessageObservation(long Sequence, DateTimeOffset Time, Guid Connection,
    string Source, string Kind, string Direction, string Service, uint? MessageId,
    int PayloadBytes, int IdBytes, byte[] Message, string Status, string Detail,
    bool Redacted = false, bool Truncated = false);

public sealed record ObservationQuery(Guid? Stream = null, long After = 0, bool Enabled = true);
public sealed record AccountObservationQuery(Guid AccountId, ObservationQuery Query);
public sealed record ObservationBatch(Guid Stream, long Cursor, long Evicted, bool Reset,
    MessageObservation[] Events);
