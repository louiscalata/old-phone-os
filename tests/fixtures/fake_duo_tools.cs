// SPDX-License-Identifier: GPL-3.0-or-later
// A stand-in for adb.exe and fastboot.exe, compiled by tests/test_duo_persist_probe.py under Windows PowerShell and
// copied to both names (it looks at its own file name). It answers a healthy Duo in TWRP or in the bootloader unless a
// FAKE_* environment variable says otherwise, appends every call to FAKE_LOG, and streams deterministic bytes that hold
// CR, LF, SUB and NUL so a text-mode copy would show. Nothing here talks to hardware. C# 5 only (the Framework compiler).
using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Text;

public static class FakeDuoTool
{
    static readonly string[] Names = { "boot_a", "boot_b", "vbmeta_a", "vbmeta_b", "dtbo_a", "dtbo_b", "misc" };
    static readonly string[] Nodes = { "sde9", "sde26", "sde14", "sde31", "sde15", "sde32", "sda5" };
    static readonly long[] Sizes = { 100663296L, 100663296L, 65536L, 65536L, 16777216L, 16777216L, 1048576L };

    static string E(string n) { string v = Environment.GetEnvironmentVariable(n); return v == null ? "" : v; }
    static Stream stdout = Console.OpenStandardOutput();
    static void Out(string s) { byte[] b = Encoding.UTF8.GetBytes(s); stdout.Write(b, 0, b.Length); stdout.Flush(); }
    static void Err(string s) { byte[] b = Encoding.UTF8.GetBytes(s); Stream e = Console.OpenStandardError(); e.Write(b, 0, b.Length); e.Flush(); }

    static int Idx(string node)
    {
        for (int i = 0; i < Nodes.Length; i++) { if (Nodes[i] == node) return i; }
        return node == "sde" ? 100 : -1;
    }

    static void Fill(int idx, long block, byte[] b)
    {
        for (int i = 0; i < 4096; i++) { b[i] = (byte)((i * 7 + idx * 13 + (int)(block & 0xFF)) & 0xFF); }
        b[0] = 0x0D; b[1] = 0x0A; b[2] = 0x1A; b[3] = 0x00;
        b[4] = (byte)(block & 0xFF); b[5] = (byte)((block >> 8) & 0xFF); b[6] = (byte)((block >> 16) & 0xFF); b[7] = 0x0A;
        if (idx == 6 && block == 0) { for (int i = 0; i < 2048; i++) { b[i] = 0; } }
    }

    static long SizeOf(string node)
    {
        int i = Idx(node);
        if (i == 100) return 239000000000L;
        return i < 0 ? 0L : Sizes[i];
    }

    static string Sha(string node)
    {
        int idx = Idx(node);
        long blocks = SizeOf(node) / 4096;
        SHA256 h = SHA256.Create();
        byte[] b = new byte[4096];
        for (long k = 0; k < blocks; k++) { Fill(idx, k, b); h.TransformBlock(b, 0, 4096, null, 0); }
        h.TransformFinalBlock(b, 0, 0);
        return BitConverter.ToString(h.Hash).Replace("-", "").ToLower();
    }

    static string NodeOf(string path)
    {
        int at = path.LastIndexOf('/');
        string leaf = path.Substring(at + 1);
        for (int i = 0; i < Names.Length; i++) { if (Names[i] == leaf) return Nodes[i]; }
        return leaf;
    }

    static int Main(string[] args)
    {
        string exe = Path.GetFileNameWithoutExtension(Environment.GetCommandLineArgs()[0]).ToLower();
        List<string> rest = new List<string>(args);
        string serial = "";
        if (rest.Count >= 2 && rest[0] == "-s") { serial = rest[1]; rest.RemoveRange(0, 2); }
        string line = string.Join(" ", rest.ToArray());
        string shown = exe + (serial != "" ? " -s <serial> " : " ") + line;
        int prior = 0;
        string log = E("FAKE_LOG");
        if (log != "")
        {
            if (File.Exists(log)) { foreach (string l in File.ReadAllLines(log)) { if (l == shown) prior++; } }
            File.AppendAllText(log, shown + "\n");
        }
        if (serial != "" && serial != "SER1" && serial != "SER2") { Err("error: device '" + serial + "' not found\n"); return 1; }
        if (exe == "adb") return Adb(line, prior);
        if (exe == "fastboot") return Fastboot(line);
        return 2;
    }

    static int Adb(string line, int prior)
    {
        if (line == "devices")
        {
            string st = E("FAKE_STATE") == "" ? "recovery" : E("FAKE_STATE");
            Out("List of devices attached\nSER1\t" + st + "\n");
            if (E("FAKE_DEVICES") == "2") Out("SER2\trecovery\n");
            Out("\n");
            return 0;
        }
        if (line == "get-state") { Out(E("FAKE_STATE") == "" ? "recovery\n" : E("FAKE_STATE") + "\n"); return 0; }
        if (line.StartsWith("exec-out dd if=/dev/block/"))
        {
            string[] w = line.Split(' ');
            string node = w[2].Substring("if=/dev/block/".Length);
            long count = long.Parse(w[4].Substring("count=".Length));
            int idx = Idx(node);
            if (E("FAKE_SHORT") == node && prior == 0) count--;
            byte[] b = new byte[4096];
            for (long k = 0; k < count; k++)
            {
                Fill(idx, k, b);
                if (k == 3 && ((E("FAKE_CORRUPT_FIRST") == node && prior == 0) || (E("FAKE_CORRUPT_SECOND") == node && prior == 1))) b[1000] ^= 0x01;
                stdout.Write(b, 0, 4096);
            }
            stdout.Flush();
            return 0;
        }
        if (line.StartsWith("shell ")) return Shell(line.Substring(6));
        return 1;
    }

    static int Shell(string c)
    {
        string ro = E("FAKE_RO");
        if (c == "id") { Out("uid=0(root) gid=0(root) groups=0(root) context=u:r:recovery:s0\n"); return 0; }
        if (c == "cat /proc/cmdline")
        {
            Out("console=ttyMSM0 androidboot.hardware=surfaceduo androidboot.slot_suffix=" + (E("FAKE_SLOT_B") == "1" ? "_b" : "_a") + " androidboot.bootloader=2022.815.527\n");
            return 0;
        }
        if (c.StartsWith("ls -l /dev/block/platform"))
        {
            for (int i = 0; i < Names.Length; i++)
            {
                if (E("FAKE_NO_DTBO") == "1" && Names[i].StartsWith("dtbo")) continue;
                Out("lrwxrwxrwx 1 root root 15 1970-01-01 00:00 " + Names[i] + " -> /dev/block/" + Nodes[i] + "\n");
            }
            Out("lrwxrwxrwx 1 root root 15 1970-01-01 00:00 userdata -> /dev/block/sda6\n");
            return 0;
        }
        if (c == "cat /proc/mounts")
        {
            Out("rootfs / rootfs rw 0 0\n/dev/block/sda6 /data ext4 rw 0 0\n");
            if (E("FAKE_MOUNT") != "") Out("/dev/block/" + E("FAKE_MOUNT") + " /mnt/x ext4 rw 0 0\n");
            return 0;
        }
        if (c == "ls /sys/class/power_supply") { Out("battery\nusb\n"); return 0; }
        if (c == "cat /sys/class/power_supply/battery/capacity") { Out((E("FAKE_BATTERY_TWRP") == "" ? "80" : E("FAKE_BATTERY_TWRP")) + "\n"); return 0; }
        if (c.StartsWith("readlink -f "))
        {
            string name = c.Substring(c.LastIndexOf('/') + 1);
            Out(E("FAKE_LINK_WRONG") == name ? "/dev/block/sde99\n" : "/dev/block/" + NodeOf(c) + "\n");
            return 0;
        }
        if (c.StartsWith("blockdev --getsize64 "))
        {
            string node = NodeOf(c);
            long n = SizeOf(node);
            if (E("FAKE_BADSIZE") == node) n += 4096;
            Out(n + "\n");
            return 0;
        }
        if (c.StartsWith("blockdev --getro ")) { Out(NodeOf(c) == ro ? "1\n" : "0\n"); return 0; }
        if (c.StartsWith("cat /sys/class/block/")) { string[] p = c.Split('/'); string node = p[4]; Out(c.EndsWith("/ro") ? (node == ro ? "1\n" : "0\n") : SizeOf(node) / 512 + "\n"); return 0; }
        if (c.StartsWith("sha256sum /dev/block/")) { string node = NodeOf(c); Out(Sha(node) + "  /dev/block/" + node + "\n"); return 0; }
        if (c == "getenforce") { Out("Permissive\n"); return 0; }
        if (c.StartsWith("sgdisk"))
        {
            if (E("FAKE_NO_SGDISK") == "1") { Out("/system/bin/sh: sgdisk: not found\n"); return 127; }
            Out("Disk /dev/block/sde: 58503795 sectors\nPartition GUID code: 1234\nAttribute flags: 0000000000000000\n");
            return 0;
        }
        if (c.StartsWith("bootctl"))
        {
            if (E("FAKE_NO_BOOTCTL") == "1") { Out("/system/bin/sh: bootctl: inaccessible or not found\n"); return 127; }
            Out(c.Contains("get-number-slots") ? "2\n" : "0\n");
            return 0;
        }
        if (c == "dmesg") { Out("[    0.000000] Booting Linux on physical CPU 0x0\n[    9.1] ufshcd: link up\n"); return 0; }
        if (c.StartsWith("cat /proc/")) { Out("fake " + c + "\n"); return 0; }
        if (c.StartsWith("getprop ")) { Out("\n"); return 0; }
        Out("fake: unhandled " + c + "\n");
        return 1;
    }

    static int Fastboot(string line)
    {
        if (line == "devices") { Out("SER1\tfastboot\n"); return 0; }
        if (line.StartsWith("getvar "))
        {
            string v = line.Substring(7);
            string val = null;
            if (v == "product") val = E("FAKE_PRODUCT") == "" ? "surfaceduo" : E("FAKE_PRODUCT");
            else if (v == "current-slot") val = E("FAKE_SLOT") == "" ? "a" : E("FAKE_SLOT");
            else if (v == "unlocked") val = E("FAKE_UNLOCKED") == "" ? "yes" : E("FAKE_UNLOCKED");
            else if (v == "battery-level") val = E("FAKE_BATTERY") == "" ? "80" : E("FAKE_BATTERY");
            else if (v == "is-userspace") val = "no";
            else if (v == "slot-count") val = "2";
            else if (v == "partition-size:boot_a" || v == "partition-size:boot_b") val = "0x6000000";
            else if (v == "all") { Err("(bootloader) product:surfaceduo\n(bootloader) slot-count:2\nFinished. Total time: 0.1s\n"); return 0; }
            if (val == null) { Err("getvar:" + v + " FAILED (remote: 'GetVar Variable Not found')\nfinished. total time: 0.001s\n"); return 0; }
            Err(v + ": " + val + "\nFinished. Total time: 0.001s\n");
            return 0;
        }
        return 1;
    }
}
