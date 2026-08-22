// Advanced Color (HDR) probe for the Nivuus Windows guest.
//
// Reference measurement on the outgoing Windows Server 2022 guest:
//     sizes rc=0 paths=1 modes=2
//     target=24832 rc=31 supported=0 enabled=0 bpc=0
// rc=31 is ERROR_GEN_FAILURE. The acceptance test of sub-project A is the same
// probe reporting supported=1 and bpc>=10 on Windows 11 LTSC 26100.
//
// Must run in session 1: in session 0 QueryDisplayConfig reports zero paths.
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

public static class NivuusAdvancedColor
{
    const uint QDC_ONLY_ACTIVE_PATHS = 0x00000002;
    const uint INFO_TYPE_ADVANCED_COLOR = 9;

    [StructLayout(LayoutKind.Sequential)]
    public struct LUID { public uint LowPart; public int HighPart; }

    [StructLayout(LayoutKind.Sequential)]
    struct RATIONAL { public uint Numerator; public uint Denominator; }

    [StructLayout(LayoutKind.Sequential)]
    struct PATH_SOURCE_INFO
    {
        public LUID adapterId; public uint id; public uint modeInfoIdx;
        public uint statusFlags;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct PATH_TARGET_INFO
    {
        public LUID adapterId; public uint id; public uint modeInfoIdx;
        public uint outputTechnology; public uint rotation; public uint scaling;
        public RATIONAL refreshRate; public uint scanLineOrdering;
        public int targetAvailable; public uint statusFlags;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct PATH_INFO
    {
        public PATH_SOURCE_INFO sourceInfo;
        public PATH_TARGET_INFO targetInfo;
        public uint flags;
    }

    // DISPLAYCONFIG_MODE_INFO is a 64-byte union we never read: 16 bytes of
    // header plus a 48-byte payload kept as blittable words so the array
    // marshals without any per-field marshalling rules.
    [StructLayout(LayoutKind.Sequential)]
    struct MODE_INFO
    {
        public uint infoType; public uint id; public LUID adapterId;
        public ulong u0, u1, u2, u3, u4, u5;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct DEVICE_INFO_HEADER
    {
        public uint type; public uint size; public LUID adapterId; public uint id;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct ADVANCED_COLOR_INFO
    {
        public DEVICE_INFO_HEADER header;
        public uint value;            // bit 0: supported, bit 1: enabled
        public uint colorEncoding;
        public uint bitsPerColorChannel;
    }

    [DllImport("user32.dll")]
    static extern int GetDisplayConfigBufferSizes(uint flags, out uint numPath,
                                                  out uint numMode);

    [DllImport("user32.dll")]
    static extern int QueryDisplayConfig(uint flags, ref uint numPath,
                                         [Out] PATH_INFO[] paths, ref uint numMode,
                                         [Out] MODE_INFO[] modes, IntPtr topologyId);

    [DllImport("user32.dll")]
    static extern int DisplayConfigGetDeviceInfo(ref ADVANCED_COLOR_INFO info);

    public static string[] Run()
    {
        var lines = new List<string>();
        uint numPath, numMode;
        int rc = GetDisplayConfigBufferSizes(QDC_ONLY_ACTIVE_PATHS, out numPath,
                                             out numMode);
        lines.Add(string.Format("sizes rc={0} paths={1} modes={2}", rc, numPath,
                                numMode));
        if (rc != 0 || numPath == 0) { return lines.ToArray(); }

        var paths = new PATH_INFO[numPath];
        var modes = new MODE_INFO[numMode];
        rc = QueryDisplayConfig(QDC_ONLY_ACTIVE_PATHS, ref numPath, paths,
                                ref numMode, modes, IntPtr.Zero);
        if (rc != 0) { lines.Add("query rc=" + rc); return lines.ToArray(); }

        for (uint i = 0; i < numPath; i++)
        {
            var info = new ADVANCED_COLOR_INFO();
            info.header.type = INFO_TYPE_ADVANCED_COLOR;
            info.header.size = (uint)Marshal.SizeOf(typeof(ADVANCED_COLOR_INFO));
            info.header.adapterId = paths[i].targetInfo.adapterId;
            info.header.id = paths[i].targetInfo.id;
            int grc = DisplayConfigGetDeviceInfo(ref info);
            lines.Add(string.Format(
                "target={0} rc={1} supported={2} enabled={3} bpc={4}",
                info.header.id, grc, info.value & 1, (info.value >> 1) & 1,
                info.bitsPerColorChannel));
        }
        return lines.ToArray();
    }
}
