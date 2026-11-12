using System.Buffers.Binary;
using System.Globalization;
using System.Numerics;
using Division.Core.Observations;

namespace Division.Launcher.Addons;

/// <summary>
/// Read-only C2S15 diagnostic, after the inner message ID: raw Guid16, ZigZag64
/// clock30, Vec3 position38, Vec3 position50, two packed-normal6 fields5C/44,
/// Vec2 field68, snorm16 angle70, varuint32 centiseconds74, flags16.
/// Evidence: Research/critical_packet_protocol.py motion15/unpack_direction,
/// native223d6d0. These are client reports BEFORE server validation. Position50,
/// directions and angle retain native offset names: body/camera roles are not
/// established. This decoder never changes gameplay or accepts movement.
/// </summary>
public sealed record PlayerPoseObservation(string Actor, Guid Connection, DateTimeOffset Time,
    Vector3 Position38, Vector3 Position50, Vector3 Direction5C, Vector3 Direction44, short Angle70)
{
    public static PlayerPoseObservation? Read(MessageObservation Event)
    {
        if (Event.Kind != "packet" || Event.Direction != "C2S" || Event.Service != "game_server" ||
            Event.MessageId != 15 || Event.Redacted || Event.Truncated || Event.IdBytes != 1 ||
            Event.Message.Length is < 67 or > 81 || Event.Message[0] != 15 ||
            Event.PayloadBytes != Event.Message.Length - 1) return null;
        try
        {
            Cursor Reader = new(Event.Message.AsSpan(1));
            string Actor = Convert.ToHexStringLower(Reader.Take(16));
            Reader.Unsigned(64);
            Vector3 Position38 = Reader.Vector(), Position50 = Reader.Vector();
            Vector3 Direction5C = Normal(Reader.Take(6)), Direction44 = Normal(Reader.Take(6));
            Reader.Float(); Reader.Float();
            short Angle = BinaryPrimitives.ReadInt16LittleEndian(Reader.Take(2));
            Reader.Unsigned(32); Reader.Take(2);
            if (!Reader.Empty) return null;
            return new(Actor, Event.Connection, Event.Time, Position38, Position50, Direction5C, Direction44,
                Angle);
        }
        catch (FormatException) { return null; }
    }

    public string Text(DateTimeOffset Now)
    {
        double Age = Math.Max(0, (Now - Time).TotalSeconds);
        string Vector(Vector3 Value) => string.Create(CultureInfo.InvariantCulture, $"{Value.X:F2} {Value.Y:F2} {Value.Z:F2}");
        return string.Create(CultureInfo.InvariantCulture,
            $"POSE C2S / {(Age > 3 ? "STALE" : "OBSERVED")} {Age:F1}S\n") +
            "POS38 " + Vector(Position38) + "\nPOS50 " + Vector(Position50) +
            "\nDIR5C " + Vector(Direction5C) + "\nDIR44 " + Vector(Direction44) +
            string.Create(CultureInfo.InvariantCulture, $"\nANGLE70 {Angle70} RAW / NOT VALIDATED");
    }

    public string CompactText(DateTimeOffset Now) => string.Create(CultureInfo.InvariantCulture,
        $"POS {Position38.X:F2} {Position38.Y:F2} {Position38.Z:F2} | DIR5C {Direction5C.X:F2} {Direction5C.Y:F2} {Direction5C.Z:F2} | {((Now - Time).TotalSeconds > 3 ? "STALE " : "")}{Math.Max(0, (Now - Time).TotalSeconds):F1}s");

    private static Vector3 Normal(ReadOnlySpan<byte> Bytes)
    {
        uint A = (uint)(Bytes[0] | Bytes[1] << 8 | Bytes[2] << 16);
        uint B = (uint)(Bytes[3] | Bytes[4] << 8 | Bytes[5] << 16);
        int XBits = (int)(A & 0x7fffff), YBits = (int)(B & 0x3fffff);
        float X = XBits == 0 ? 0 : ((A & 0x800000) == 0 ? XBits + 1 : -XBits - 1) / 8388608f;
        float Y = YBits == 0 ? 0 : ((B & 0x400000) == 0 ? YBits + 1 : -YBits - 1) / 4194304f;
        float ZSquared = (1f - X * X) - Y * Y;
        if (ZSquared >= 0) return new(X, Y, MathF.Sqrt(ZSquared) * ((B & 0x800000) == 0 ? 1 : -1));
        float Inverse = 1f / MathF.Sqrt(X * X + Y * Y);
        return new(X * Inverse, Y * Inverse, 0);
    }

    private ref struct Cursor(ReadOnlySpan<byte> Bytes)
    {
        private ReadOnlySpan<byte> Remaining = Bytes;
        public bool Empty => Remaining.IsEmpty;
        public ReadOnlySpan<byte> Take(int Count)
        {
            if (Remaining.Length < Count) throw new FormatException();
            ReadOnlySpan<byte> Value = Remaining[..Count]; Remaining = Remaining[Count..]; return Value;
        }
        public float Float()
        {
            float Value = BinaryPrimitives.ReadSingleLittleEndian(Take(4));
            if (!float.IsFinite(Value)) throw new FormatException();
            return Value;
        }
        public Vector3 Vector() => new(Float(), Float(), Float());
        public ulong Unsigned(int Bits)
        {
            ulong Value = 0;
            for (int Shift = 0; Shift < Bits; Shift += 7)
            {
                byte Part = Take(1)[0];
                if ((Part & 127) >= 1 << Math.Min(7, Bits - Shift)) throw new FormatException();
                Value |= (ulong)(Part & 127) << Shift;
                if ((Part & 128) == 0) return Value;
            }
            throw new FormatException();
        }
    }
}
